"""
Binance data sync + realtime signal runner
------------------------------------------
- fetch_klines()  : tarik histori OHLCV via REST (publik, tanpa API key), dengan paginasi
- LiveFeed        : seed histori -> WebSocket kline -> jalankan engine tiap candle CLOSE
- Reconnect otomatis (backoff), deteksi gap candle -> re-seed otomatis
- Sinyal hanya dievaluasi pada candle yang sudah close (tidak repainting)

Dependensi: pip install pandas numpy requests websockets

Catatan simbol:
  Binance tidak punya XAUUSD spot. Opsi emas: PAXGUSDT (spot, token emas) atau
  XAUUSDT perpetual di futures (market="futures"). Cek ketersediaan di akun/wilayahmu.
"""
import asyncio
import json
import os
import time

import pandas as pd
import requests

from .confluence_engine import Config, generate_signals

# Kandidat host: utama + fallback publik (data-api.binance.vision = endpoint
# resmi khusus data pasar; berguna bila api.binance.com diblokir jaringan).
# Urutan didahulukan host .vision: di sebagian besar jaringan yang memblokir
# api.binance.com / stream.binance.com (SSL mismatch), host .vision tetap
# jalan — mencoba host utama dulu hanya buang waktu + retry tiap reconnect.
# Bisa dioverride env AT_REST_BASE / AT_WS_BASE (URL lengkap klines / base WS).
REST_CANDIDATES = {
    "spot": ["https://data-api.binance.vision/api/v3/klines",
             "https://api.binance.com/api/v3/klines"],
    "futures": ["https://fapi.binance.com/fapi/v1/klines"],
}
WS_CANDIDATES = {
    "spot": ["wss://data-stream.binance.vision:9443/ws",
             "wss://stream.binance.com:9443/ws"],
    "futures": ["wss://fstream.binance.com/ws"],
}
for _env, _cands in ((os.getenv("AT_REST_BASE"), REST_CANDIDATES),
                     (os.getenv("AT_WS_BASE"), WS_CANDIDATES)):
    if _env:
        for _m in _cands:
            _cands[_m].insert(0, _env.rstrip("/"))

REST = {m: c[0] for m, c in REST_CANDIDATES.items()}      # kompatibilitas lama
WS = {m: c[0] for m, c in WS_CANDIDATES.items()}
_working_rest = {}                                        # host yang terbukti jalan

# Provider cadangan gratis: Bybit v5 public (spot, tanpa API key).
BYBIT_REST = "https://api.bybit.com/v5/market/kline"
BYBIT_WS = "wss://stream.bybit.com/v5/public/spot"
BYBIT_INTERVAL = {"1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
                  "1h": "60", "2h": "120", "4h": "240", "6h": "360",
                  "12h": "720", "1d": "D"}

# Provider cadangan: Yahoo Finance via raw chart API (fallback akhir).
# Punya emas asli (GC=F = futures COMEX) — relevan untuk XAUUSD.
YAHOO_REST = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
YAHOO_INTERVAL = {"1m": "1m", "3m": "2m", "5m": "5m", "15m": "15m", "30m": "30m",
                  "1h": "60m"}
YAHOO_RANGE = {"1m": "5d", "3m": "7d", "5m": "10d", "15m": "30d", "30m": "60d",
               "1h": "120d"}

# Provider yfinance — library resmi (pip install yfinance).
# Lebih stabil daripada raw chart API, punya GC=F & simbol Yahoo lainnya.
# Interval native yfinance; 4h & 2h di-resample dari 1h.
YFINANCE_INTERVAL = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "2h": "1h", "4h": "1h",
}
YFINANCE_PERIOD = {
    "1m": "7d",   # 1m max 7 hari di yfinance
    "5m": "60d", "15m": "60d", "30m": "60d",
    "1h": "730d", "2h": "730d", "4h": "730d",
}


def _yf_symbol(symbol):
    """PAXGUSDT -> PAXG-USD; GC=F / BTC-USD dipakai apa adanya."""
    s = symbol.upper()
    if "=" in s or "-" in s:
        return s
    return s[:-4] + "-USD" if s.endswith("USDT") else s


def _yfinance_df(symbol, interval, bars):
    """OHLCV via library yfinance -> DataFrame format feed.
    Interval 4h & 2h di-resample dari 1h (yfinance tidak punya native 4h)."""
    try:
        import yfinance as yf
    except ImportError:
        print("[yfinance] library tidak terpasang — pip install yfinance", flush=True)
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "close_time"])

    ys = _yf_symbol(symbol)
    yiv = YFINANCE_INTERVAL.get(interval, "1h")
    period = YFINANCE_PERIOD.get(interval, "60d")
    now_ms = int(time.time() * 1000)

    ticker = yf.Ticker(ys)
    raw = ticker.history(period=period, interval=yiv, auto_adjust=True, prepost=False)

    if raw is None or raw.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "close_time"])

    # Normalisasi kolom & index -> UTC
    raw.columns = [c.lower() for c in raw.columns]
    df = raw[["open", "high", "low", "close", "volume"]].copy()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    df.index.name = "time"

    # Resample jika interval native yfinance berbeda (mis. 4h dari 1h)
    if YFINANCE_INTERVAL.get(interval, interval) != interval:
        rule = {"2h": "2h", "4h": "4h"}.get(interval, interval)
        df = df.resample(rule).agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum"
        }).dropna()

    step_ms = INTERVAL_MS.get(interval, 60_000)
    df["close_time"] = (
        df.index.as_unit("ms").asi8 + step_ms - 1
    ).astype("int64")

    # Buang candle yang belum close (candle berjalan)
    df = df[df["close_time"] < now_ms]
    return df.tail(bars)


def _yahoo_symbol(symbol):
    """PAXGUSDT -> PAXG-USD; simbol Yahoo asli (mis. GC=F) dipakai apa adanya."""
    s = symbol.upper()
    if "=" in s or s.endswith("-USD"):
        return s
    return s[:-4] + "-USD" if s.endswith("USDT") else s


def _yahoo_chart_df(symbol, interval, bars):
    """OHLCV dari Yahoo chart API -> DataFrame format feed. Interval yang tidak
    ada di Yahoo (mis. 4h) diambil dari 60m lalu di-resample."""
    ys = _yahoo_symbol(symbol)
    if interval in YAHOO_INTERVAL:
        yiv, rng = YAHOO_INTERVAL[interval], YAHOO_RANGE[interval]
    else:
        yiv, rng = "60m", "2y"
    r = requests.get(YAHOO_REST.format(sym=ys),
                     params={"interval": yiv, "range": rng},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    r.raise_for_status()
    res = (r.json().get("chart") or {}).get("result") or []
    if not res:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "close_time"])
    d = res[0]
    ts = d.get("timestamp") or []
    q = (d.get("indicators") or {}).get("quote") or [{}]
    q = q[0]
    rows = []
    for i, t in enumerate(ts):
        try:
            o, h, l, c, v = (q["open"][i], q["high"][i], q["low"][i],
                             q["close"][i], q["volume"][i])
        except (KeyError, IndexError, TypeError):
            continue
        if None in (o, h, l, c):
            continue
        rows.append([t * 1000, float(o), float(h), float(l), float(c),
                     float(v or 0), t * 1000 + INTERVAL_MS["1m"] - 1,
                     0, 0, 0, 0, 0])
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "close_time"])
    df = _to_df(rows)
    if interval in YAHOO_INTERVAL:
        out = df
    else:                                                    # resample ke TF besar (mis. 4h)
        out = df.resample(interval).agg({"open": "first", "high": "max", "low": "min",
                                         "close": "last", "volume": "sum"}).dropna()
        out["close_time"] = (out.index.as_unit("ms").asi8 + INTERVAL_MS[interval] - 1).astype("int64")
    return out.tail(bars)


def _bybit_klines_df(symbol, interval, start_ms, end_ms):
    """Tarik kline dari Bybit (public, tanpa key) -> DataFrame format sama
    dengan hasil Binance. Dipakai otomatis bila semua host Binance gagal."""
    iv = BYBIT_INTERVAL[interval]
    step = INTERVAL_MS[interval]
    out, cursor, guard = [], start_ms, 0
    while cursor is not None and cursor <= end_ms and guard < 20:
        guard += 1
        r = requests.get(BYBIT_REST, params={"category": "spot", "symbol": symbol.upper(),
                                             "interval": iv, "start": int(cursor),
                                             "end": int(end_ms), "limit": 1000}, timeout=15)
        r.raise_for_status()
        lst = (r.json().get("result") or {}).get("list") or []
        if not lst:
            break
        lst = list(reversed(lst))                            # Bybit: terbaru dulu
        for row in lst:
            t0 = int(row[0])
            out.append([t0, float(row[1]), float(row[2]), float(row[3]),
                        float(row[4]), float(row[5]), t0 + step - 1, 0, 0, 0, 0, 0])
        cursor = int(lst[-1][0]) + step
        if len(lst) < 1000:
            break
        time.sleep(0.2)
    now = int(time.time() * 1000)
    if not out:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "close_time"])
    df = _to_df(out)
    df = df[~df.index.duplicated(keep="last")]
    return df[df["close_time"] < now]                        # buang candle berjalan


def _rest_url(market):
    """URL REST klines: pakai host yang sudah terbukti, kalau belum uji berurutan."""
    if _working_rest.get(market):
        return _working_rest[market]
    return REST_CANDIDATES.get(market, REST_CANDIDATES["spot"])[0]

INTERVAL_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
               "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
               "4h": 14_400_000, "6h": 21_600_000, "12h": 43_200_000,
               "1d": 86_400_000}
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
        "qav", "trades", "tbbav", "tbqav", "ignore"]


# ----------------------------- REST histori ---------------------------------
def _to_df(rows):
    df = pd.DataFrame(rows, columns=COLS)
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    df.index = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df.index.name = "time"
    df["close_time"] = df["close_time"].astype("int64")
    return df[["open", "high", "low", "close", "volume", "close_time"]]


def fetch_klines(symbol, interval, start_ms=None, end_ms=None, bars=None,
                 market="spot", pause=0.25):
    """Tarik kline historis. Beri `bars` (N candle terakhir) ATAU start_ms/end_ms.
    Hanya mengembalikan candle yang sudah close."""
    step = INTERVAL_MS[interval]
    now = int(time.time() * 1000)
    end_ms = end_ms or now
    if bars and not start_ms:
        start_ms = end_ms - bars * step
    out, cursor = [], start_ms
    candidates = REST_CANDIDATES.get(market, REST_CANDIDATES["spot"])
    ci = 0

    while True:
        params = {"symbol": symbol.upper(), "interval": interval, "limit": 1000,
                  "endTime": end_ms}
        if cursor is not None:
            params["startTime"] = cursor
        try:
            url = _working_rest.get(market) or candidates[ci]
            r = requests.get(url, params=params, timeout=15)
        except requests.RequestException:
            ci += 1
            if ci >= len(candidates):
                # semua host Binance gagal -> provider cadangan gratis
                try:
                    print("[rest] Binance tidak terjangkau, coba Bybit...", flush=True)
                    return _bybit_klines_df(symbol, interval, start_ms, end_ms)
                except Exception as e1:
                    print(f"[rest] Bybit gagal ({e1.__class__.__name__}), coba yfinance...", flush=True)
                    try:
                        return _yfinance_df(symbol, interval, bars or 500)
                    except Exception as e2:
                        print(f"[rest] yfinance gagal ({e2.__class__.__name__}), coba Yahoo raw...", flush=True)
                        return _yahoo_chart_df(symbol, interval, bars or 500)
            print(f"[rest] fallback ke {candidates[ci]}", flush=True)
            continue
        if r.status_code in (418, 429):                      # rate limit
            time.sleep(int(r.headers.get("Retry-After", 5)))
            continue
        r.raise_for_status()
        _working_rest[market] = url
        rows = r.json()
        if not rows:
            break
        out.extend(rows)
        cursor = rows[-1][0] + step
        if len(rows) < 1000 or cursor >= end_ms:
            break
        time.sleep(pause)

    if not out:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "close_time"])
    df = _to_df(out)
    df = df[~df.index.duplicated(keep="last")]
    return df[df["close_time"] < now]                        # buang candle berjalan


# ----------------------------- Notifikasi -----------------------------------
def format_signal(symbol, interval, t, row):
    side = "LONG 🟢" if row["signal"] == 1 else "SHORT 🔴"
    return (f"{symbol} {interval} | {side}\n"
            f"Waktu : {t:%Y-%m-%d %H:%M} UTC\n"
            f"Entry : {row['entry']:.2f}\nSL    : {row['sl']:.2f}\n"
            f"TP1   : {row['tp1']:.2f}\nTP2   : {row['tp2']:.2f}\n"
            f"Skor  : {int(row['score'])}")


def telegram_notifier(token=None, chat_id=None):
    """Callback on_signal yang kirim ke Telegram. Token dari env TG_TOKEN / TG_CHAT_ID."""
    token = token or os.getenv("TG_TOKEN")
    chat_id = chat_id or os.getenv("TG_CHAT_ID")

    def _send(text):
        print(text, flush=True)
        if token and chat_id:
            try:
                requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              json={"chat_id": chat_id, "text": text}, timeout=5)
            except requests.RequestException as e:
                print("Telegram gagal:", e)
    return _send


# ----------------------------- Live feed ------------------------------------
class LiveFeed:
    def __init__(self, symbol="PAXGUSDT", interval="15m", market="spot",
                 cfg=None, buffer_bars=1500, on_signal=None, cache_csv=None,
                 on_candle=None, quiet=False, evaluate=True, provider="binance",
                 poll_sec=20):
        self.symbol, self.interval, self.market = symbol.upper(), interval, market
        self.cfg = cfg or Config()
        self.buffer_bars = buffer_bars
        self.on_signal = on_signal or telegram_notifier()
        self.on_candle = on_candle            # fn(interval, buf) tiap candle close
        self.quiet = quiet
        self.evaluate = evaluate              # False = hanya isi buffer (TF tampilan)
        self.provider = provider              # binance (WS) | yahoo (polling REST)
        self.poll_sec = poll_sec
        self.cache_csv = cache_csv
        self.step = INTERVAL_MS[interval]
        self.buf = pd.DataFrame()
        self._last_emitted = None

    # -- data
    def seed(self):
        if self.provider == "yfinance":
            self.buf = _yfinance_df(self.symbol, self.interval, self.buffer_bars)
        elif self.provider == "yahoo":
            self.buf = _yahoo_chart_df(self.symbol, self.interval, self.buffer_bars)
        else:
            self.buf = fetch_klines(self.symbol, self.interval, bars=self.buffer_bars,
                                    market=self.market)
        if not self.quiet:
            print(f"[seed] {len(self.buf)} candle {self.symbol} {self.interval} "
                  f"({self.provider})", flush=True)
        self._save()

    def _save(self):
        if self.cache_csv and len(self.buf):
            self.buf.to_csv(self.cache_csv)

    def handle_kline(self, k):
        """k = field 'k' dari pesan kline Binance. Hanya proses saat candle close."""
        if not k.get("x"):
            return
        t = pd.to_datetime(k["t"], unit="ms", utc=True)
        row = pd.DataFrame(
            {"open": float(k["o"]), "high": float(k["h"]), "low": float(k["l"]),
             "close": float(k["c"]), "volume": float(k["v"]), "close_time": int(k["T"])},
            index=pd.DatetimeIndex([t], name="time"))

        if len(self.buf):
            last = self.buf.index[-1]
            if t <= last:                                    # duplikat / replay
                self.buf = self.buf[self.buf.index < t]
            elif (t - last).total_seconds() * 1000 > self.step * 1.5:
                print("[gap] candle terlewat, re-seed...", flush=True)
                self.seed()
                return
        self.buf = pd.concat([self.buf, row]).iloc[-self.buffer_bars:]
        self._save()
        if self.on_candle:
            try:
                self.on_candle(self.interval, self.buf)
            except Exception as e:
                print(f"[on_candle] error ({self.interval}):", e, flush=True)
        self._evaluate()

    def _evaluate(self):
        if not self.evaluate:
            return
        if len(self.buf) < max(self.cfg.vp_lookback, 250) + 10:
            return
        sig = generate_signals(self.buf, self.cfg)
        if sig.empty:
            return
        t = sig.index[-1]
        if t == self.buf.index[-1] and t != self._last_emitted:
            self._last_emitted = t
            self.on_signal(format_signal(self.symbol, self.interval, t, sig.iloc[-1]))

    # -- websocket / polling
    async def run(self):
        import websockets                                    # impor lokal
        self.seed()
        if self.provider in ("yahoo", "yfinance"):
            # Tidak ada WS -> polling REST berkala, feed candle yang close
            _fetch_fn = _yfinance_df if self.provider == "yfinance" else _yahoo_chart_df
            _label = self.provider
            while True:
                await asyncio.sleep(self.poll_sec)
                try:
                    df = await asyncio.get_running_loop().run_in_executor(
                        None, _fetch_fn, self.symbol, self.interval,
                        self.buffer_bars)
                except Exception as e:
                    print(f"[{_label}] poll gagal ({self.interval}): {e}", flush=True)
                    continue
                if df is None or df.empty:
                    continue
                new = df[df.index > self.buf.index[-1]] if len(self.buf) else df.tail(1)
                for t, row in new.iterrows():
                    self.handle_kline({"t": int(t.timestamp() * 1000),
                                       "T": int(row["close_time"]),
                                       "o": row["open"], "h": row["high"],
                                       "l": row["low"], "c": row["close"],
                                       "v": row["volume"], "x": True})
            return
        self.provider = "binance"
        candidates = WS_CANDIDATES.get(self.market, WS_CANDIDATES["spot"])
        wi, backoff = 0, 1
        while True:
            if self.provider == "binance":
                url = f"{candidates[wi % len(candidates)]}/{self.symbol.lower()}@kline_{self.interval}"
                op = None
            else:
                url = BYBIT_WS
                op = {"op": "subscribe",
                      "args": [f"kline.{BYBIT_INTERVAL[self.interval]}.{self.symbol.upper()}"]}
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=30) as ws:
                    print(f"[ws] terhubung ({self.provider}): {url}", flush=True)
                    backoff = 1
                    if op:
                        await ws.send(json.dumps(op))
                    async for msg in ws:
                        for k in self._parse_ws(json.loads(msg)):
                            self.handle_kline(k)
            except (websockets.ConnectionClosed, OSError, asyncio.TimeoutError) as e:
                wi += 1
                if self.provider == "binance" and wi >= len(candidates) * 2:
                    self.provider = "bybit"                  # semua host Binance gagal
                    print("[ws] beralih ke provider Bybit", flush=True)
                    wi = 0
                print(f"[ws] putus ({e}) [{self.provider}]; ulang {backoff}s", flush=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
                try:
                    self.seed()                              # sinkron ulang setelah putus
                except requests.RequestException as e2:
                    print("[seed] gagal:", e2, flush=True)

    @staticmethod
    def _parse_ws(data):
        """Normalisasi pesan WS provider -> list format kline Binance."""
        if not isinstance(data, dict):
            return []
        if data.get("e") == "kline":                          # binance
            return [data["k"]]
        topic = str(data.get("topic") or "")
        if topic.startswith("kline."):                        # bybit v5
            return [{"t": int(k["start"]), "T": int(k["end"]),
                     "o": k["open"], "h": k["high"], "l": k["low"],
                     "c": k["close"], "v": k["volume"], "x": True}
                    for k in data.get("data", []) if k.get("confirm")]
        return []


# ----------------------------- Multi timeframe -------------------------------
class MultiFeed:
    """Beberapa timeframe NATIVE sekaligus (mis. 15m entry + 4h tren), masing-masing
    dari stream WebSocket Binance sendiri — bukan hasil resample.
    - feed TF entry       : on_candle(interval, buf) + on_signal tiap candle close
    - feed TF lainnya     : hanya mengisi buffer (untuk htf_df engine / heatmap)
    Koneksi tiap TF independen dengan reconnect backoff masing-masing."""

    def __init__(self, symbol="PAXGUSDT", intervals=("15m", "4h"), market="spot",
                 cfg=None, buffer_bars=1500, signal_tf=None, on_signal=None,
                 on_candle=None, cache_dir=None, provider="binance"):
        self.symbol = symbol.upper()
        self.intervals = list(intervals)
        self.market = market
        self.signal_tf = signal_tf or self.intervals[0]
        self.on_signal = on_signal
        self.on_candle = on_candle
        self.cache_dir = cache_dir
        self.provider = provider
        self.buffers = {tf: pd.DataFrame() for tf in self.intervals}
        self.feeds = {}
        for tf in self.intervals:
            is_entry = tf == self.signal_tf
            cache_csv = (f"{cache_dir}/{self.symbol}_{tf}.csv") if cache_dir else None
            self.feeds[tf] = LiveFeed(
                symbol=symbol, interval=tf, market=market, cfg=cfg,
                buffer_bars=buffer_bars,
                on_signal=(on_signal or telegram_notifier()) if is_entry else self._noop_signal,
                on_candle=self._dispatch,
                quiet=not is_entry, cache_csv=cache_csv,
                evaluate=is_entry, provider=provider,           # TF tampilan: tanpa evaluasi engine
                poll_sec=max(15, INTERVAL_MS[tf] // 4000))

    @staticmethod
    def _noop_signal(*a, **k):
        pass

    def _dispatch(self, interval, buf):
        self.buffers[interval] = buf
        if self.on_candle:
            self.on_candle(interval, buf)

    async def run(self):
        await asyncio.gather(*(f.run() for f in self.feeds.values()))


if __name__ == "__main__":
    # Mode multi-timeframe native (entry 15m + tren 4h langsung dari Binance):
    #   mf = MultiFeed(symbol="PAXGUSDT", intervals=("15m", "4h"), market="spot")
    #   asyncio.run(mf.run())
    feed = LiveFeed(symbol="PAXGUSDT", interval="15m", market="spot",
                    cache_csv="paxg_15m.csv")
    try:
        asyncio.run(feed.run())
    except KeyboardInterrupt:
        print("stop")
