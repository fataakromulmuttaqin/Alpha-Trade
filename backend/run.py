"""
Alpha-Trade runner — feed + engine + dashboard dalam satu proses.
-----------------------------------------------------------------
Live (Binance, multi-TF native):
  python -m backend.run --symbol PAXGUSDT --entry-tf 15m --htf 4h
Demo offline (data sintetis, tanpa internet):
  python -m backend.run --demo
Dashboard : http://localhost:8788   |   WS broadcast: ws://localhost:8789
Eksekusi  : EXECUTION_MODE=off|paper|testnet  (lihat testnet_executor.py)
"""
import argparse
import asyncio
import os
import time

import numpy as np
import pandas as pd

from .analytics import build_snapshot
from .binance_feed import MultiFeed, INTERVAL_MS
from .confluence_engine import Config, generate_signals
from .server import DashboardServer
from .testnet_executor import TestnetExecutor


class AppState:
    """Buffer per TF + snapshot terakhir + log sinyal + executor."""

    def __init__(self, symbol, cfg, executor, label=None):
        self.symbol = symbol
        self.label = label or symbol
        self.cfg = cfg
        self.executor = executor
        self.buffers = {}
        self.snapshot_data = None
        self.signals_log = []
        self._last_sig_time = None
        self.server = None
        self.lock = asyncio.Lock()
        self.sensitivity = "golden"                  # preset mode aktif
        self.exit_protocol = "quick"
        self.demo = False
        self._view_cache = {}                        # view_tf -> (ts, snap)

    # ---- dipakai DashboardServer
    def snapshot(self, view_tf=None):
        """Snapshot TF entry (cache) atau hitung on-demand untuk TF lain."""
        if not view_tf or view_tf == self.entry_tf:
            return self.snapshot_data
        hit = self._view_cache.get(view_tf)
        if hit and (time.time() - hit[0]) < 5:
            return hit[1]
        snap = build_snapshot(dict(self.buffers), self.cfg, self.signals_log,
                              symbol=self.label, view_tf=view_tf,
                              htf_tf=(self.htf if view_tf == self.entry_tf else None))
        if snap:
            snap["demo"] = self.demo
            snap["exec_mode"] = self.executor.mode
            snap["engine_entry"] = self.entry_tf
            snap["view_only"] = True
            self._view_cache[view_tf] = (time.time(), snap)
        return snap

    def apply_settings(self, body):
        """POST /api/settings: sensitivity + exit_protocol -> preset Config."""
        from .godmode_engine import apply_mode
        out = {"applied": []}
        sens = body.get("sensitivity")
        if sens in ("golden", "aggressive", "standard", "precision", "pingpong"):
            self.sensitivity = sens
            apply_mode(self.cfg, sensitivity=sens)
            out["applied"].append("sensitivity:" + sens)
        ex = body.get("exit_protocol")
        if ex in ("quick", "nitro", "hold"):
            self.exit_protocol = ex
            apply_mode(self.cfg, exit_protocol=ex)
            out["applied"].append("exit:" + ex)
        out["sensitivity"] = self.sensitivity
        out["exit_protocol"] = self.exit_protocol
        print(f"[settings] {out}", flush=True)
        return out

    def candles(self, tf, limit=1500):
        df = self.buffers.get(tf)
        if df is None or df.empty:
            return None
        d = df.tail(limit)
        ms = INTERVAL_MS[tf]
        return [[int(t.timestamp() * 1000), round(float(r["open"]), 2),
                 round(float(r["high"]), 2), round(float(r["low"]), 2),
                 round(float(r["close"]), 2), round(float(r["volume"]), 2)]
                for t, r in d.iterrows()] if ms else None

    def signals(self):
        return {"signals": self.signals_log}

    def health(self):
        return {"ok": True, "symbol": self.label, "mode": self.executor.mode,
                "sensitivity": self.sensitivity, "exit_protocol": self.exit_protocol,
                "tfs": list(self.buffers), "bars": {t: len(b) for t, b in self.buffers.items()},
                "clients": len(self.server.clients) if self.server else 0}

    # ---- alur utama: candle close dari TF entry
    async def on_candle(self, interval, buf, demo=False):
        self.buffers[interval] = buf
        if interval != self.entry_tf:
            if self.server:                               # candle TF tampilan saja
                last = buf.iloc[-1]
                self.server.broadcast_threadsafe({
                    "type": "candle", "tf": interval,
                    "candle": [int(buf.index[-1].timestamp() * 1000),
                               float(last["open"]), float(last["high"]),
                               float(last["low"]), float(last["close"]),
                               float(last["volume"])]})
            return
        htf_buf = self.buffers.get(self.htf)
        if len(buf) < 260 or htf_buf is None or len(htf_buf) < 210:
            return                                       # engine butuh warm-up

        async with self.lock:
            sig = generate_signals(buf, self.cfg, htf_df=htf_buf)
            self._handle_new_signals(sig)
            snap = build_snapshot(dict(self.buffers), self.cfg, self.signals_log,
                                  symbol=self.label, view_tf=self.entry_tf,
                                  htf_tf=self.htf)
            if snap:
                snap["demo"] = demo
                snap["exec_mode"] = self.executor.mode
                snap["engine_entry"] = self.entry_tf
            self.snapshot_data = snap
            self._view_cache.clear()          # snapshot TF lain ikut basi

        if self.server:
            self.server.broadcast_threadsafe({"type": "snapshot", "data": snap})
            last = buf.iloc[-1]
            self.server.broadcast_threadsafe({
                "type": "candle", "tf": interval,
                "candle": [int(buf.index[-1].timestamp() * 1000),
                           float(last["open"]), float(last["high"]),
                           float(last["low"]), float(last["close"]),
                           float(last["volume"])]})

    def _handle_new_signals(self, sig):
        if sig.empty:
            return
        for t, row in sig.iterrows():
            if self._last_sig_time is not None and t <= self._last_sig_time:
                continue
            self._last_sig_time = t
            entry = {"time": t.isoformat(), "tf": self.entry_tf,
                     "side": "LONG" if row["signal"] == 1 else "SHORT",
                     "entry": round(float(row["entry"]), 2),
                     "sl": round(float(row["sl"]), 2),
                     "tp1": round(float(row["tp1"]), 2),
                     "tp2": round(float(row["tp2"]), 2),
                     "score": int(row["score"])}
            self.signals_log.insert(0, entry)
            self.signals_log = self.signals_log[:30]
            print(f"[sinyal] {entry['time']} {self.label} {entry['tf']} "
                  f"{entry['side']} entry={entry['entry']} sl={entry['sl']} "
                  f"tp1={entry['tp1']} tp2={entry['tp2']} skor={entry['score']}", flush=True)
            self.executor.execute_signal(self.symbol, row)   # no-op saat mode off


# ----------------------------- Demo feed ------------------------------------
DEMO_TFS = ("1m", "5m", "15m", "30m", "1h", "4h")
LIVE_TFS = ("1m", "5m", "15m", "30m", "1h", "4h")
DEMO_SEED = {"1m": 900, "5m": 900, "15m": 520, "30m": 460, "1h": 420, "4h": 420}


class DemoFeed:
    """Feed sintetis untuk uji dashboard tanpa internet: random-walk + clustering
    volatilitas. Semua TF DEMO_TFS dibuat native (bukan resample) dan dipercepat."""

    def __init__(self, entry_tf="5m", tfs=DEMO_TFS, tick_sec=1.5):
        self.entry_tf = entry_tf
        self.tfs = tuple(tfs)
        self.tick_sec = tick_sec
        self.price = 3850.0

    def _candle(self, t, step_ms):
        vol = np.random.uniform(0.4, 1.6) * (1 if np.random.random() < 0.5 else 2.2)
        drift = np.random.choice([-1, 0, 0, 1]) * 0.9
        o = self.price
        c = o + np.random.normal(drift, 1.5 * vol)
        hi = max(o, c) + abs(np.random.normal(0, 0.9 * vol))
        lo = min(o, c) - abs(np.random.normal(0, 0.9 * vol))
        v = float(np.random.uniform(60, 900))
        self.price = c
        idx = pd.DatetimeIndex([pd.Timestamp(t, unit="ms", tz="UTC")], name="time")
        return pd.DataFrame({"open": [o], "high": [hi], "low": [lo],
                             "close": [c], "volume": [v], "close_time": [t + step_ms - 1]},
                            idx)

    def _seed(self, tf, n):
        step = INTERVAL_MS[tf]
        t0 = int(time.time() * 1000) - n * step
        return pd.concat([self._candle(t0 + i * step, step) for i in range(n)])

    async def run(self, state):
        bufs, last_t = {}, {}
        for tf in self.tfs:
            bufs[tf] = self._seed(tf, DEMO_SEED.get(tf, 500))
            last_t[tf] = int(bufs[tf].index[-1].timestamp() * 1000)
            await state.on_candle(tf, bufs[tf], demo=True)
        print("[demo] seed " + ", ".join(f"{tf}:{len(bufs[tf])}" for tf in self.tfs),
              flush=True)
        step = INTERVAL_MS[self.entry_tf]
        t = max(last_t.values())
        while True:
            await asyncio.sleep(self.tick_sec)
            t += step
            for tf in self.tfs:
                s = INTERVAL_MS[tf]
                if tf == self.entry_tf:
                    bufs[tf] = pd.concat([bufs[tf], self._candle(t, s)]).iloc[-1500:]
                    await state.on_candle(tf, bufs[tf], demo=True)
                    continue
                while last_t[tf] + s <= t:                # candle TF ini sudah "close"
                    last_t[tf] += s
                    bufs[tf] = pd.concat([bufs[tf], self._candle(last_t[tf], s)]).iloc[-1500:]
                    await state.on_candle(tf, bufs[tf], demo=True)


# ----------------------------- Main ------------------------------------------
async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="data sintetis tanpa internet")
    ap.add_argument("--symbol", default=os.getenv("AT_SYMBOL", "PAXGUSDT"))
    ap.add_argument("--label", default=os.getenv("AT_LABEL", "XAUUSD"))
    ap.add_argument("--entry-tf", default=os.getenv("AT_ENTRY_TF", "15m"))
    ap.add_argument("--htf", default=os.getenv("AT_HTF", "4h"))
    ap.add_argument("--market", default=os.getenv("AT_MARKET", "spot"),
                    choices=["spot", "futures"])
    ap.add_argument("--provider", default=os.getenv("AT_PROVIDER", "binance"),
                    choices=["binance", "yahoo", "yfinance"],
                    help="binance = realtime WS (fallback otomatis); "
                         "yahoo = polling raw chart API; "
                         "yfinance = polling via library yfinance (GC=F lebih stabil)")
    ap.add_argument("--bars", type=int, default=int(os.getenv("AT_BARS", "1500")))
    ap.add_argument("--port", type=int, default=int(os.getenv("AT_PORT", "8788")))
    ap.add_argument("--ws-port", type=int, default=int(os.getenv("AT_WS_PORT", "8789")))
    args = ap.parse_args()

    cfg = Config(htf=args.htf)
    executor = TestnetExecutor(market=args.market)
    state = AppState(args.symbol, cfg, executor, label=args.label)
    server = DashboardServer(state, args.port, args.ws_port)
    state.server = server
    await server.start()

    if args.demo:
        state.entry_tf, state.htf = "5m", "4h"
        state.demo = True
        print(f"[run] mode DEMO — {state.label} (sintetis) | TF: {', '.join(DEMO_TFS)} | "
              f"exec={executor.mode}", flush=True)
        feed = DemoFeed(entry_tf="5m", tfs=DEMO_TFS)
    else:
        state.entry_tf, state.htf = args.entry_tf, args.htf
        tfs = list(dict.fromkeys(LIVE_TFS + (args.entry_tf, args.htf)))
        print(f"[run] {state.label} ({args.symbol}) entry={args.entry_tf} "
              f"htf={args.htf} TF={','.join(tfs)} market={args.market} "
              f"exec={executor.mode}", flush=True)
        mf = MultiFeed(symbol=args.symbol, intervals=tfs,
                       market=args.market, cfg=cfg, buffer_bars=args.bars,
                       signal_tf=args.entry_tf,
                       on_signal=lambda txt: print(txt, flush=True),
                       on_candle=lambda tf, buf: asyncio.get_running_loop().create_task(
                           state.on_candle(tf, buf)),
                       cache_dir="cache", provider=args.provider)
        os.makedirs("cache", exist_ok=True)

        # seed awal langsung tersedia untuk dashboard sebelum candle pertama close
        for tf in mf.feeds:
            try:
                mf.feeds[tf].seed()
                state.buffers[tf] = mf.feeds[tf].buf
            except Exception as e:
                print(f"[seed] {tf} gagal: {e}", flush=True)
        snap = build_snapshot(state.buffers, cfg, state.signals_log, state.label,
                              view_tf=args.entry_tf, htf_tf=args.htf)
        if snap:
            snap["engine_entry"] = args.entry_tf
            snap["exec_mode"] = executor.mode
        state.snapshot_data = snap

        async def live_loop():
            await mf.run()
        return await live_loop()

    await feed.run(state)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("stop")
