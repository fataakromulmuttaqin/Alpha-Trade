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
from .tradoor_feed import TradoorFeed


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
        self.signal_golden = True                    # SIGNAL GOLDEN: ON/OFF
        self.signal_high_risk = False                # SIGNAL HIGH RISK: ON/OFF
        self.suppressed = []                         # sinyal yang disaring filter
        self.demo = False
        self.deep_history = {}                       # tf -> df history tradoor (chart ALL)
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
            self._decorate(snap)
            self._view_cache[view_tf] = (time.time(), snap)
        return snap

    def apply_settings(self, body):
        """POST /api/settings: sensitivity, exit_protocol, filter sinyal."""
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
        if "signal_golden" in body:
            self.signal_golden = bool(body["signal_golden"])
            out["applied"].append(f"signal_golden:{self.signal_golden}")
        if "signal_high_risk" in body:
            self.signal_high_risk = bool(body["signal_high_risk"])
            out["applied"].append(f"signal_high_risk:{self.signal_high_risk}")
        out["sensitivity"] = self.sensitivity
        out["exit_protocol"] = self.exit_protocol
        out["signal_golden"] = self.signal_golden
        out["signal_high_risk"] = self.signal_high_risk
        print(f"[settings] {out}", flush=True)
        return out

    def candles(self, tf, limit=1500):
        """Candle untuk chart: gabung history tradoor (deep) + buffer realtime."""
        df = self.buffers.get(tf)
        deep = self.deep_history.get(tf)
        if (df is None or df.empty) and (deep is None or deep.empty):
            return None
        if deep is not None and len(deep):
            if df is None or df.empty:
                d = deep.tail(limit)
            else:
                d = pd.concat([deep, df[~df.index.isin(deep.index)]]).sort_index().tail(limit)
        else:
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
                "signal_golden": self.signal_golden, "signal_high_risk": self.signal_high_risk,
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
            snap = self._decorate(snap)
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

    def _classify(self, score):
        """GODMODE: skor engine 0-4 -> GOLDEN (>=3) / HIGH RISK (<=2)."""
        return "GOLDEN" if score >= 3 else "HIGH RISK"

    def _decorate(self, snap):
        """Tempelkan state filter sinyal + daftar sinyal tersaring ke snapshot."""
        if snap is not None:
            snap["filters"] = {
                "golden": self.signal_golden,
                "high_risk": self.signal_high_risk,
                "suppressed": self.suppressed[:10],
                "suppressed_count": len(self.suppressed),
            }
            snap["mode"] = {"sensitivity": self.sensitivity,
                            "exit_protocol": self.exit_protocol}
        return snap

    def recompute_signal_history(self, bars=25_000):
        """Hitung ulang sinyal historis dengan config mode yang baru — inilah
        yang membuat perubahan sensitivity/exit protocol TERLIHAT di chart
        (marker sinyal bertambah/berkurang) tanpa menunggu candle berikutnya."""
        try:
            import pandas as pd
            hist = self.buffers.get(self.entry_tf)
            deep = self.deep_history.get(self.entry_tf)
            src = None
            if deep is not None and len(deep):
                extra = hist[~hist.index.isin(deep.index)] if hist is not None and len(hist) else None
                src = pd.concat([deep, extra]) if extra is not None else deep
            elif hist is not None:
                src = hist
            if src is None or len(src) < 300:
                return
            htf_buf = self.buffers.get(self.htf)
            sig = generate_signals(src.tail(bars), self.cfg, htf_df=htf_buf)
            entries = []
            for t, row in sig.tail(10).iterrows():
                entries.append({
                    "time": t.isoformat(), "tf": self.entry_tf,
                    "side": "LONG" if row["signal"] == 1 else "SHORT",
                    "entry": round(float(row["entry"]), 2),
                    "sl": round(float(row["sl"]), 2),
                    "tp1": round(float(row["tp1"]), 2),
                    "tp2": round(float(row["tp2"]), 2),
                    "score": int(row["score"]),
                    "kind": self._classify(int(row["score"])),
                })
            self.signals_log = list(reversed(entries))
            if sig.index.size:
                self._last_sig_time = sig.index[-1]
            print(f"[mode] sinyal historis dihitung ulang: {len(sig)} sinyal "
                  f"dengan mode {self.sensitivity}/{self.exit_protocol} "
                  f"(10 terakhir di log)", flush=True)
        except Exception as e:
            print("[mode] recompute gagal:", e, flush=True)

    def rebuild_and_broadcast(self, recompute_signals=False):
        """Bangun ulang snapshot dengan state terbaru (mis. setelah ubah filter)
        dan broadcast ke semua klien dashboard."""
        try:
            snap = build_snapshot(dict(self.buffers), self.cfg, self.signals_log,
                                  symbol=self.label, view_tf=self.entry_tf,
                                  htf_tf=self.htf)
            if snap:
                snap["demo"] = self.demo
                snap["exec_mode"] = self.executor.mode
                snap["engine_entry"] = self.entry_tf
                self._decorate(snap)
            self.snapshot_data = snap
            self._view_cache.clear()
            if recompute_signals:
                self.recompute_signal_history()
                snap = build_snapshot(dict(self.buffers), self.cfg, self.signals_log,
                                      symbol=self.label, view_tf=self.entry_tf,
                                      htf_tf=self.htf)
                if snap:
                    snap["demo"] = self.demo
                    snap["exec_mode"] = self.executor.mode
                    snap["engine_entry"] = self.entry_tf
                    self._decorate(snap)
                self.snapshot_data = snap
            if self.server:
                self.server.broadcast_threadsafe({"type": "snapshot", "data": snap})
        except Exception as e:
            print("[settings] rebuild snapshot gagal:", e, flush=True)

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
            kind = self._classify(entry["score"])
            allowed = (kind == "GOLDEN" and self.signal_golden) or \
                      (kind == "HIGH RISK" and self.signal_high_risk)
            if not allowed:
                # disaring: tetap tercatat untuk analisis, tidak dieksekusi
                entry["kind"] = kind
                self.suppressed.insert(0, entry)
                self.suppressed = self.suppressed[:30]
                print(f"[sinyal disaring] {kind} {entry['side']} {entry['time']} "
                      f"entry={entry['entry']} skor={entry['score']} "
                      f"(SIGNAL {kind}: OFF)", flush=True)
                continue
            entry["kind"] = kind
            self.signals_log.insert(0, entry)
            self.signals_log = self.signals_log[:30]
            print(f"[sinyal] {kind} {entry['time']} {self.label} {entry['tf']} "
                  f"{entry['side']} entry={entry['entry']} sl={entry['sl']} "
                  f"tp1={entry['tp1']} tp2={entry['tp2']} skor={entry['score']}", flush=True)
            self.executor.execute_signal(self.symbol, row)   # no-op saat mode off


def load_tradoor_history(source, market, tfs, caps=None):
    """Muat history dalam dari data tradoor (TDC1) untuk chart: decode 1m
    sekali, lalu resample per TF. Return {tf: DataFrame}."""
    from .tradoor_feed import load_market, INTERVAL_MIN
    caps = caps or {"1m": 200_000, "5m": 150_000, "15m": 150_000,
                    "30m": 100_000, "1h": 60_000, "4h": 30_000, "1d": 10_000}
    df1m = load_market(source, market, "1m", bars=10**9)
    if df1m.empty:
        return {}
    out = {}
    for tf in tfs:
        if tf == "1m":
            out[tf] = df1m.tail(caps["1m"])
            continue
        agg = {"open": "first", "high": "max", "low": "min", "close": "last",
               "volume": "sum"}
        r = df1m.resample(f"{INTERVAL_MIN[tf]}min").agg(agg).dropna(subset=["close"])
        out[tf] = r.tail(caps.get(tf, 100_000))
    return out


def seed_last_signal(state, deep_bars=50_000):
    """Cari sinyal terakhir di deep history (tanpa eksekusi) supaya log sinyal
    dan fallback toggle RR langsung terisi saat boot."""
    try:
        import pandas as pd
        hist = state.buffers.get(state.entry_tf)
        deep = state.deep_history.get(state.entry_tf)
        if deep is not None and len(deep):
            extra = hist[~hist.index.isin(deep.index)] if hist is not None and len(hist) else None
            src = pd.concat([deep, extra]) if extra is not None else deep
        else:
            src = hist
        if src is None or len(src) < 300:
            return
        htf_buf = state.buffers.get(state.htf)
        sig = generate_signals(src.tail(deep_bars), state.cfg, htf_df=htf_buf)
        if sig.empty:
            return
        t, row = sig.index[-1], sig.iloc[-1]
        kind = "GOLDEN" if int(row["score"]) >= 3 else "HIGH RISK"
        state.signals_log = [{
            "time": t.isoformat(), "tf": state.entry_tf,
            "side": "LONG" if row["signal"] == 1 else "SHORT",
            "entry": round(float(row["entry"]), 2), "sl": round(float(row["sl"]), 2),
            "tp1": round(float(row["tp1"]), 2), "tp2": round(float(row["tp2"]), 2),
            "score": int(row["score"]), "kind": kind,
        }]
        state._last_sig_time = t
        print(f"[boot] sinyal terakhir (history): {kind} {state.signals_log[0]['side']} "
              f"{t} entry={state.signals_log[0]['entry']}", flush=True)
    except Exception as e:
        print("[boot] seed sinyal history gagal:", e, flush=True)


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
                    choices=["binance", "yahoo", "yfinance", "tradoor"],
                    help="binance = realtime WS (fallback otomatis); "
                         "yahoo = polling raw chart API; "
                         "yfinance = polling via library yfinance (GC=F lebih stabil); "
                         "tradoor = baca partisi TDC1 tradoor self-host")
    ap.add_argument("--tradoor-data", default=os.getenv("AT_TRADOOR_DATA", ""),
                    help="direktori data tradoor ATAU base URL /data/... server tradoor")
    ap.add_argument("--tradoor-market", default=os.getenv("AT_TRADOOR_MARKET", ""),
                    help="market tradoor, mis. binance-perp:BTCUSDT (default dari --symbol)")
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
    elif args.provider == "tradoor":
        # Data dari tradoor self-host (file lokal atau HTTP /data/...).
        if not args.tradoor_data:
            raise SystemExit("--provider tradoor butuh --tradoor-data <dir|url>")
        state.entry_tf, state.htf = args.entry_tf, args.htf
        tfs = list(dict.fromkeys(LIVE_TFS + (args.entry_tf, args.htf)))
        tmarket = args.tradoor_market or (
            f"{'binance-perp' if args.market == 'futures' else 'binance'}:{args.symbol}")
        print(f"[run] {state.label} tradoor data={args.tradoor_data} market={tmarket} "
              f"entry={args.entry_tf} htf={args.htf} exec={executor.mode}", flush=True)
        feeds = []
        for tf in tfs:
            feeds.append(TradoorFeed(
                args.tradoor_data, tmarket, tf, buffer_bars=args.bars,
                on_candle=lambda tf_, buf_: asyncio.get_running_loop().create_task(
                    state.on_candle(tf_, buf_)),
                poll_sec=max(30, INTERVAL_MS[tf] // 4000)))
        for f in feeds:
            try:
                state.buffers[f.interval] = f.seed()
            except Exception as e:
                print(f"[seed] {f.interval} gagal: {e}", flush=True)
        snap = build_snapshot(state.buffers, cfg, state.signals_log, state.label,
                              view_tf=args.entry_tf, htf_tf=args.htf)
        if snap:
            snap["engine_entry"] = args.entry_tf
            snap["exec_mode"] = executor.mode
        state._decorate(snap)
        state.snapshot_data = snap

        async def tradoor_loop():
            await asyncio.gather(*(f.run(state) for f in feeds))
        return await tradoor_loop()
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

        # history dalam dari data tradoor (opsional): chart bisa tampil bertahun-tahun,
        # sementara engine tetap memakai buffer ringan dari feed realtime.
        if args.tradoor_data:
            try:
                tmarket = args.tradoor_market or (
                    f"{'binance-perp' if args.market == 'futures' else 'binance'}:{args.symbol}")
                deep = load_tradoor_history(args.tradoor_data, tmarket, tfs)
                state.deep_history = deep
                if deep:
                    ringkas = ", ".join(f"{tf}:{len(d)}" for tf, d in deep.items())
                    print(f"[history] tradoor dimuat ({tmarket}): {ringkas}", flush=True)
            except Exception as e:
                print(f"[history] tradoor gagal dimuat: {e}", flush=True)

        # seed awal langsung tersedia untuk dashboard sebelum candle pertama close
        for tf in mf.feeds:
            try:
                mf.feeds[tf].seed()
                state.buffers[tf] = mf.feeds[tf].buf
            except Exception as e:
                print(f"[seed] {tf} gagal: {e}", flush=True)
        seed_last_signal(state)                          # isi log dulu (fallback RR)
        snap = build_snapshot(state.buffers, cfg, state.signals_log, state.label,
                              view_tf=args.entry_tf, htf_tf=args.htf)
        if snap:
            snap["engine_entry"] = args.entry_tf
            snap["exec_mode"] = executor.mode
        state._decorate(snap)
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
