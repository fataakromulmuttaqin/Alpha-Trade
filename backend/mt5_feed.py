"""
MetaTrader 5 Feed untuk Alpha-Trade
-----------------------------------
- Auto-detect simbol emas broker di Market Watch (XAUUSD / XAUUSDm / GOLD / dll.)
- Ambil candle historis multi-TF langsung dari terminal MT5 Windows
- Selalu memakai bar yang SUDAH close (start_pos=1) -> non-repainting / tanpa lookahead
- Polling ringan tiap beberapa detik untuk mendeteksi candle close baru

Dependensi: pip install MetaTrader5 (hanya Windows)
"""
import asyncio
import time

import pandas as pd

from .binance_feed import INTERVAL_MS

# Fallback nilai konstanta timeframe MT5 (dipakai juga saat testing dengan mock)
TF_ATTRS = {
    "1m": ("TIMEFRAME_M1", 1),
    "3m": ("TIMEFRAME_M3", 3),
    "5m": ("TIMEFRAME_M5", 5),
    "15m": ("TIMEFRAME_M15", 15),
    "30m": ("TIMEFRAME_M30", 30),
    "1h": ("TIMEFRAME_H1", 16385),
    "2h": ("TIMEFRAME_H2", 16386),
    "4h": ("TIMEFRAME_H4", 16388),
    "1d": ("TIMEFRAME_D1", 16408),
}

GOLD_CANDIDATES = (
    "XAUUSD", "XAUUSDm", "XAUUSDc", "XAUUSD.a", "XAUUSD.i",
    "XAUUSD.raw", "XAUUSD.pro", "XAUUSD_i", "XAUUSD.", "GOLD",
    "GOLDm", "GOLDc", "GOLD.i", "GOLD.pro",
)


def import_mt5():
    """Import MetaTrader5 secara aman; kembalikan None bila belum terpasang."""
    try:
        import MetaTrader5 as mt5
        return mt5
    except ImportError:
        return None


def tf_const(mt5, interval):
    """Konversi string TF ('5m', '1h', '4h') ke konstanta TIMEFRAME_* MT5."""
    if interval not in TF_ATTRS:
        raise ValueError(f"Timeframe MT5 tidak didukung: {interval}")
    attr, default_val = TF_ATTRS[interval]
    return getattr(mt5, attr, default_val)


def resolve_mt5_symbol(mt5, requested=None):
    """Temukan nama simbol broker aktual di MT5.
    - Bila `requested` diisi dan ada di MT5 -> pakai langsung.
    - Bila kosong / 'AUTO' / 'PAXGUSDT' / 'XAUUSD' tidak ketemu persis ->
      auto-detect simbol emas (XAUUSD, XAUUSDm, GOLD, dll.) dari Market Watch / broker.
    """
    req = (requested or "").strip()
    is_auto_gold = req.upper() in ("", "AUTO", "PAXGUSDT", "GC=F")

    if req and not is_auto_gold:
        info = mt5.symbol_info(req)
        if info is not None:
            mt5.symbol_select(req, True)
            return info.name

    # 1) Coba kandidat emas populer terlebih dulu
    cands = list(GOLD_CANDIDATES)
    if req and not is_auto_gold and req not in cands:
        cands.insert(0, req)

    for cand in cands:
        info = mt5.symbol_info(cand)
        if info is not None:
            mt5.symbol_select(info.name, True)
            return info.name

    # 2) Pindai semua simbol broker yang mengandung XAUUSD / GOLD (atau nama diminta)
    all_syms = mt5.symbols_get() or []
    needles = ["XAUUSD", "GOLD"] if (is_auto_gold or "XAU" in req.upper() or "GOLD" in req.upper()) else [req.upper()]
    matches = []
    for s in all_syms:
        name_up = s.name.upper()
        if any(nd in name_up for nd in needles):
            visible_score = 0 if getattr(s, "visible", False) else 1
            matches.append((visible_score, len(s.name), s.name))
    if matches:
        matches.sort()
        chosen = matches[0][2]
        mt5.symbol_select(chosen, True)
        return chosen

    raise RuntimeError(
        f"Simbol MT5 '{req or 'XAUUSD/GOLD'}' tidak ditemukan di broker. "
        f"Pastikan simbol tampil di Market Watch atau atur simbol MT5 di Pengaturan."
    )


def rates_to_df(rates, interval):
    """Ubah array rate MT5 -> DataFrame format Alpha-Trade (UTC DatetimeIndex)."""
    cols = ["open", "high", "low", "close", "volume", "close_time"]
    if rates is None or len(rates) == 0:
        return pd.DataFrame(columns=cols)

    df = pd.DataFrame(rates)
    # Normalisasi volume: pakai real_volume bila ada & >0, selain itu tick_volume
    if "real_volume" in df.columns and (df["real_volume"] > 0).any():
        vol = df["real_volume"].astype(float)
    elif "tick_volume" in df.columns:
        vol = df["tick_volume"].astype(float)
    elif "volume" in df.columns:
        vol = df["volume"].astype(float)
    else:
        vol = pd.Series(0.0, index=df.index)

    step_ms = INTERVAL_MS.get(interval, 60_000)
    open_ms = (df["time"].astype("int64") * 1000)
    out = pd.DataFrame({
        "open": df["open"].astype(float),
        "high": df["high"].astype(float),
        "low": df["low"].astype(float),
        "close": df["close"].astype(float),
        "volume": vol,
        "close_time": (open_ms + step_ms - 1).astype("int64"),
    })
    out.index = pd.to_datetime(open_ms, unit="ms", utc=True)
    out.index.name = "time"
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def fetch_mt5_candles(mt5, symbol, interval, bars=1500):
    """Tarik `bars` candle yang SUDAH close (start_pos=1 melewati bar berjalan)."""
    tf = tf_const(mt5, interval)
    rates = mt5.copy_rates_from_pos(symbol, tf, 1, int(bars))
    if rates is None or len(rates) == 0:
        time.sleep(0.6)
        rates = mt5.copy_rates_from_pos(symbol, tf, 1, int(bars))
    return rates_to_df(rates, interval)


def fetch_mt5_forming_candle(mt5, symbol, interval):
    """Ambil 1 candle berjalan (start_pos=0) khusus tampilan visual chart live.
    Tidak dipakai oleh engine sinyal (engine tetap memakai buffer closed bars)."""
    if mt5 is None or not symbol or interval not in TF_ATTRS:
        return None
    try:
        tf = tf_const(mt5, interval)
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, 1)
        if rates is None or len(rates) == 0:
            return None
        r = rates[0]
        vol = float(r["real_volume"]) if ("real_volume" in r.dtype.names and r["real_volume"] > 0) else float(r["tick_volume"])
        from .godmode_engine import price_digits
        dp = price_digits(float(r["close"]))
        return [
            int(r["time"]) * 1000,
            round(float(r["open"]), dp),
            round(float(r["high"]), dp),
            round(float(r["low"]), dp),
            round(float(r["close"]), dp),
            round(vol, 2),
        ]
    except Exception:
        return None


class MT5MultiFeed:
    """Polling feed multi-TF langsung dari terminal MT5 lokal."""

    def __init__(self, symbol="", intervals=("1m", "5m", "15m", "30m", "1h", "4h"),
                 buffer_bars=1500, on_candle=None, terminal_path="",
                 poll_sec=2.0, mt5_mod=None):
        self.requested_symbol = symbol
        self.symbol = symbol
        self.intervals = tuple(dict.fromkeys(intervals))
        self.buffer_bars = int(buffer_bars)
        self.on_candle = on_candle
        self.terminal_path = (terminal_path or "").strip()
        self.poll_sec = max(0.5, float(poll_sec))
        self._mt5 = mt5_mod
        self.buffers = {}
        self._last_time = {}
        self._running = False

    def _ensure_init(self):
        if self._mt5 is None:
            self._mt5 = import_mt5()
        if self._mt5 is None:
            raise RuntimeError("Package MetaTrader5 belum terpasang (pip install MetaTrader5)")
        ok = (self._mt5.initialize(path=self.terminal_path)
              if self.terminal_path else self._mt5.initialize())
        if not ok:
            err = self._mt5.last_error() if hasattr(self._mt5, "last_error") else "unknown"
            raise RuntimeError(f"Gagal konek terminal MT5: {err}. Pastikan aplikasi MT5 sedang terbuka & login.")
        self.symbol = resolve_mt5_symbol(self._mt5, self.requested_symbol)
        return self._mt5

    def seed(self):
        """Muat histori candle closed untuk semua TF."""
        mt5 = self._ensure_init()
        for tf in self.intervals:
            df = fetch_mt5_candles(mt5, self.symbol, tf, self.buffer_bars)
            if not df.empty:
                self.buffers[tf] = df
                self._last_time[tf] = df.index[-1]
        return self.buffers

    def poll_once(self):
        """Periksa candle closed terbaru untuk tiap TF; kembalikan list (tf, df) yang baru."""
        mt5 = self._mt5
        if mt5 is None:
            return []
        updated = []
        for tf in self.intervals:
            fresh = fetch_mt5_candles(mt5, self.symbol, tf, bars=5)
            if fresh.empty:
                continue
            prev_t = self._last_time.get(tf)
            new_rows = fresh[fresh.index > prev_t] if prev_t is not None else fresh
            if new_rows.empty:
                continue
            cur = self.buffers.get(tf)
            if cur is None or cur.empty:
                merged = fresh.tail(self.buffer_bars)
            else:
                merged = pd.concat([cur, new_rows])
                merged = merged[~merged.index.duplicated(keep="last")].sort_index().tail(self.buffer_bars)
            self.buffers[tf] = merged
            self._last_time[tf] = merged.index[-1]
            updated.append((tf, merged))
        return updated

    async def run(self):
        """Loop polling async: memanggil on_candle(tf, buf) tiap ada bar baru yang close."""
        self._running = True
        print(f"[mt5-feed] aktif: simbol={self.symbol} TF={','.join(self.intervals)} "
              f"poll={self.poll_sec}s", flush=True)
        while self._running:
            try:
                updates = self.poll_once()
                for tf, buf in updates:
                    if self.on_candle:
                        res = self.on_candle(tf, buf)
                        if asyncio.iscoroutine(res):
                            await res
            except Exception as e:
                print(f"[mt5-feed] error poll: {e} — mencoba reconnect...", flush=True)
                await asyncio.sleep(3.0)
                try:
                    self._ensure_init()
                except Exception as e2:
                    print(f"[mt5-feed] reconnect gagal: {e2}", flush=True)
            await asyncio.sleep(self.poll_sec)

    def stop(self):
        self._running = False
