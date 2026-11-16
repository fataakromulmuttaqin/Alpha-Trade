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

from confluence_engine import Config, generate_signals

REST = {"spot": "https://api.binance.com/api/v3/klines",
        "futures": "https://fapi.binance.com/fapi/v1/klines"}
WS = {"spot": "wss://stream.binance.com:9443/ws",
      "futures": "wss://fstream.binance.com/ws"}

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

    while True:
        params = {"symbol": symbol.upper(), "interval": interval, "limit": 1000,
                  "endTime": end_ms}
        if cursor is not None:
            params["startTime"] = cursor
        r = requests.get(REST[market], params=params, timeout=15)
        if r.status_code in (418, 429):                      # rate limit
            time.sleep(int(r.headers.get("Retry-After", 5)))
            continue
        r.raise_for_status()
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
                 on_candle=None, quiet=False):
        self.symbol, self.interval, self.market = symbol.upper(), interval, market
        self.cfg = cfg or Config()
        self.buffer_bars = buffer_bars
        self.on_signal = on_signal or telegram_notifier()
        self.on_candle = on_candle            # fn(interval, buf) tiap candle close
        self.quiet = quiet
        self.cache_csv = cache_csv
        self.step = INTERVAL_MS[interval]
        self.buf = pd.DataFrame()
        self._last_emitted = None

    # -- data
    def seed(self):
        self.buf = fetch_klines(self.symbol, self.interval, bars=self.buffer_bars,
                                market=self.market)
        if not self.quiet:
            print(f"[seed] {len(self.buf)} candle {self.symbol} {self.interval}", flush=True)
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
        if len(self.buf) < max(self.cfg.vp_lookback, 250) + 10:
            return
        sig = generate_signals(self.buf, self.cfg)
        if sig.empty:
            return
        t = sig.index[-1]
        if t == self.buf.index[-1] and t != self._last_emitted:
            self._last_emitted = t
            self.on_signal(format_signal(self.symbol, self.interval, t, sig.iloc[-1]))

    # -- websocket
    async def run(self):
        import websockets                                    # impor lokal
        self.seed()
        url = f"{WS[self.market]}/{self.symbol.lower()}@kline_{self.interval}"
        backoff = 1
        while True:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=30) as ws:
                    print(f"[ws] terhubung: {url}", flush=True)
                    backoff = 1
                    async for msg in ws:
                        data = json.loads(msg)
                        if data.get("e") == "kline":
                            self.handle_kline(data["k"])
            except (websockets.ConnectionClosed, OSError, asyncio.TimeoutError) as e:
                print(f"[ws] putus ({e}); reconnect {backoff}s", flush=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
                try:
                    self.seed()                              # sinkron ulang setelah putus
                except requests.RequestException as e2:
                    print("[seed] gagal:", e2, flush=True)


# ----------------------------- Multi timeframe -------------------------------
class MultiFeed:
    """Beberapa timeframe NATIVE sekaligus (mis. 15m entry + 4h tren), masing-masing
    dari stream WebSocket Binance sendiri — bukan hasil resample.
    - feed TF entry       : on_candle(interval, buf) + on_signal tiap candle close
    - feed TF lainnya     : hanya mengisi buffer (untuk htf_df engine / heatmap)
    Koneksi tiap TF independen dengan reconnect backoff masing-masing."""

    def __init__(self, symbol="PAXGUSDT", intervals=("15m", "4h"), market="spot",
                 cfg=None, buffer_bars=1500, signal_tf=None, on_signal=None,
                 on_candle=None, cache_dir=None):
        self.symbol = symbol.upper()
        self.intervals = list(intervals)
        self.market = market
        self.signal_tf = signal_tf or self.intervals[0]
        self.on_signal = on_signal
        self.on_candle = on_candle
        self.cache_dir = cache_dir
        self.buffers = {tf: pd.DataFrame() for tf in self.intervals}
        self.feeds = {}
        for tf in self.intervals:
            is_entry = tf == self.signal_tf
            cache_csv = (f"{cache_dir}/{self.symbol}_{tf}.csv") if cache_dir else None
            self.feeds[tf] = LiveFeed(
                symbol=symbol, interval=tf, market=market, cfg=cfg,
                buffer_bars=buffer_bars,
                on_signal=(on_signal or telegram_notifier()) if is_entry else self._noop_signal,
                on_candle=self._dispatch if is_entry else self._track,
                quiet=not is_entry, cache_csv=cache_csv)

    @staticmethod
    def _noop_signal(*a, **k):
        pass

    def _track(self, interval, buf):
        """Buffer TF non-entry ikut tersimpan tanpa memicu evaluasi engine."""
        self.buffers[interval] = buf

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
