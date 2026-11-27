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
import json as _json_mod
import os
import time

import numpy as np
import pandas as pd

from .analytics import build_snapshot
from .binance_feed import MultiFeed, INTERVAL_MS
from .confluence_engine import Config, generate_signals
from .godmode_engine import apply_mode, generate_signals_auto, market_condition, price_digits
from .mt5_executor import MT5Executor
from .mt5_feed import MT5MultiFeed, fetch_mt5_forming_candle
from .mt5_mcp import MT5McpAdapter
from .news_feed import NewsFeed
from .paper_sim import PaperSim, START_EQUITY
from .server import DashboardServer
from .testnet_executor import TestnetExecutor
from .tradoor_feed import TradoorFeed

AUTO_STABLE_BARS = 8   # bar berturut-turut sebelum rezim dianggap ganti (15m ~ 2 jam)


def _rr_text(tp2, entry, sl, orig_risk=None):
    """Teks RR untuk kartu setup. Saat SL sudah digeser breakeven (sl == entry)
    pakai risk awal trade, bukan pembagian nol."""
    base = abs(entry - sl) or (orig_risk or 0)
    return f"1:{abs(tp2 - entry) / base:.0f}" if base else "BE"


class SymbolContext:
    """State terpisah per instrumen/pair (buffer TF, snapshot, sinyal, PaperSim, feed)."""

    def __init__(self, symbol, label=None, sim_path="sim_trades.json",
                 daily_stop_r=0.0, daily_max_trades=0):
        self.symbol = symbol
        self.label = label or symbol
        self.buffers = {}
        self.deep_history = {}
        self.snapshot_data = None
        self.signals_log = []
        self.suppressed = []
        self._last_sig_time = None
        self._view_cache = {}
        self.feed = None
        self._feed_task = None
        self.sim = PaperSim(
            path=sim_path,
            daily_stop_r=daily_stop_r,
            daily_max_trades=daily_max_trades,
            default_symbol=self.label,
        )


class AppState:
    """Buffer per TF + snapshot terakhir + log sinyal + executor (mendukung multi-pair)."""

    def __init__(self, symbol, cfg, executor, label=None):
        self.cfg = cfg
        self.executor = executor
        self.server = None
        self.lock = asyncio.Lock()
        self.sensitivity = "auto"                    # preset mode aktif ("auto" = adaptif rezim)
        self.exit_protocol = "quick"
        self._auto_cond = None                       # rezim yang sedang dipakai (live)
        self._auto_cand = None                       # rezim kandidat (hysteresis)
        self._auto_cand_n = 0
        self._auto_resolved = None                   # preset terpilih utk ditampilkan
        # pengaturan notifikasi & MT5 (cache/app_settings.json) — diatur dari UI
        self.app_settings = {}
        try:
            if os.path.exists("cache/app_settings.json"):
                with open("cache/app_settings.json", encoding="utf-8") as f:
                    self.app_settings = _json_mod.load(f)
            # fallback: warisi kredensial Telegram dari data/settings.json (versi native) bila belum ada
            if not self.app_settings.get("tg_token") and os.path.exists("data/settings.json"):
                with open("data/settings.json", encoding="utf-8") as f:
                    nat = _json_mod.load(f)
                for k in ("tg_token", "tg_chat_id", "tg_on", "notify_signals", "notify_trades"):
                    if k in nat and k not in self.app_settings:
                        self.app_settings[k] = nat[k]
        except Exception:
            self.app_settings = {}
        self.signal_golden = True                    # SIGNAL GOLDEN: ON/OFF
        self.signal_high_risk = False                # SIGNAL HIGH RISK: ON/OFF
        self.demo = False
        self.entry_tf = "15m"                        # default; ditimpa main() setelah parse arg
        self.htf = "4h"                              # — mencegah /api/snapshot error saat boot
        self.bars = 1500
        self._daily_stop_r = float(os.getenv("AT_SIM_DAILY_STOP", "0") or 0)
        self._daily_max_trades = int(os.getenv("AT_SIM_MAX_TRADES", "0") or 0)

        primary = SymbolContext(
            symbol=symbol, label=label or symbol, sim_path="sim_trades.json",
            daily_stop_r=self._daily_stop_r, daily_max_trades=self._daily_max_trades,
        )
        self._primary = primary
        self.contexts = {primary.symbol: primary}

        self.news_feed = NewsFeed()                  # berita 3 provider + MT5 MCP Calendar/News
        self.provider = "binance"
        self.mt5 = MT5Executor(settings=self.app_settings, on_event=self._on_mt5_event)
        if self.mt5.settings.get("mt5_mcp_url"):
            self.news_feed.configure_mcp(
                self.mt5.settings.get("mt5_mcp_url"),
                self.mt5.settings.get("mt5_mcp_key"),
            )

    # --- properti kompatibilitas simbol utama (single-symbol & unit test lama)
    @property
    def symbol(self): return self._primary.symbol
    @symbol.setter
    def symbol(self, v):
        old = self._primary.symbol
        if old in self.contexts and old != v:
            ctx = self.contexts.pop(old)
            ctx.symbol = v
            self.contexts = {v: ctx, **self.contexts}
        else:
            self._primary.symbol = v
    @property
    def label(self): return self._primary.label
    @label.setter
    def label(self, v):
        self._primary.label = v
        self._primary.sim.default_symbol = v
    @property
    def buffers(self): return self._primary.buffers
    @buffers.setter
    def buffers(self, v): self._primary.buffers = v
    @property
    def deep_history(self): return self._primary.deep_history
    @deep_history.setter
    def deep_history(self, v): self._primary.deep_history = v
    @property
    def snapshot_data(self): return self._primary.snapshot_data
    @snapshot_data.setter
    def snapshot_data(self, v): self._primary.snapshot_data = v
    @property
    def signals_log(self): return self._primary.signals_log
    @signals_log.setter
    def signals_log(self, v): self._primary.signals_log = v
    @property
    def suppressed(self): return self._primary.suppressed
    @suppressed.setter
    def suppressed(self, v): self._primary.suppressed = v
    @property
    def _last_sig_time(self): return self._primary._last_sig_time
    @_last_sig_time.setter
    def _last_sig_time(self, v): self._primary._last_sig_time = v
    @property
    def _view_cache(self): return self._primary._view_cache
    @_view_cache.setter
    def _view_cache(self, v): self._primary._view_cache = v
    @property
    def sim(self): return self._primary.sim
    @sim.setter
    def sim(self, v): self._primary.sim = v

    def symbol_list(self):
        return list(self.contexts.keys())

    def get_ctx(self, symbol=None):
        if not symbol:
            return self._primary
        if symbol in self.contexts:
            return self.contexts[symbol]
        up = symbol.strip().upper()
        for k, ctx in self.contexts.items():
            if k.upper() == up or ctx.label.upper() == up:
                return ctx
        return self._primary

    def _on_mt5_event(self, rec):
        """Teruskan kejadian penting MT5 (order/BE/reject) ke notifikasi Telegram bila aktif."""
        kind = rec.get("kind")
        if kind in ("order", "be", "close"):
            ico = "🤖" if kind == "order" else ("🔒" if kind == "be" else "✅")
            self._notify(f"{ico} <b>MT5</b> {rec.get('msg', '')}")

    def persist_app_settings(self):
        try:
            os.makedirs("cache", exist_ok=True)
            with open("cache/app_settings.json", "w", encoding="utf-8") as f:
                _json_mod.dump(self.app_settings, f, indent=2)
        except Exception as e:
            print("[settings] gagal simpan:", e, flush=True)

    def update_mt5_settings(self, patch):
        updated = self.mt5.update_settings(patch)
        self.app_settings.update(updated)
        self.persist_app_settings()
        if "mt5_mcp_url" in patch or "mt5_mcp_key" in patch:
            self.news_feed.configure_mcp(
                updated.get("mt5_mcp_url", ""),
                updated.get("mt5_mcp_key", ""),
            )
            self.news_feed.refresh(force=True)
        return updated

    def _make_mt5_feed(self, sym_name):
        tfs = list(dict.fromkeys(LIVE_TFS + (self.entry_tf, self.htf)))
        mt5_path = self.mt5.settings.get("mt5_terminal_path") or ""
        mt5_tr = (self.mt5.settings.get("mt5_transport") or "python").lower()
        feed_mod = None
        if mt5_tr == "mcp" and self.mt5.settings.get("mt5_mcp_url"):
            feed_mod = MT5McpAdapter(
                url=self.mt5.settings.get("mt5_mcp_url"),
                api_key=self.mt5.settings.get("mt5_mcp_key", ""),
                magic=int(self.mt5.settings.get("mt5_magic", 202605)),
            )
        return MT5MultiFeed(
            symbol=sym_name, intervals=tfs, buffer_bars=self.bars,
            on_candle=lambda tf, buf, _s=sym_name: asyncio.get_running_loop().create_task(
                self.on_candle(tf, buf, symbol=_s)),
            terminal_path=mt5_path, poll_sec=2.5, mt5_mod=feed_mod,
        )

    def add_mt5_symbol(self, sym_name, start_feed=True, persist=True):
        """Tambahkan pair MT5 baru ke dashboard secara live (seed + replay + polling)."""
        req = (sym_name or "").strip()
        if not req:
            return False, "Nama simbol kosong", None
        if not self.mt5.connected:
            ok, msg = self.mt5.connect()
            if not ok:
                return False, msg, None
        from .mt5_feed import resolve_mt5_symbol
        try:
            resolved = resolve_mt5_symbol(self.mt5._mt5, req)
        except Exception as e:
            return False, str(e), None

        if resolved in self.contexts:
            return True, f"{resolved} sudah ada di dashboard", resolved

        safe_slug = "".join(c if c.isalnum() else "_" for c in resolved.lower())
        os.makedirs("cache", exist_ok=True)
        ctx = SymbolContext(
            symbol=resolved, label=resolved,
            sim_path=os.path.join("cache", f"sim_trades_{safe_slug}.json"),
            daily_stop_r=self._daily_stop_r, daily_max_trades=self._daily_max_trades,
        )
        feed = self._make_mt5_feed(resolved)
        try:
            feed.seed()
        except Exception as e:
            return False, f"Gagal muat candle {resolved}: {e}", None
        if self.entry_tf not in feed.buffers or len(feed.buffers[self.entry_tf]) < 100:
            return False, f"Data candle {resolved} ({self.entry_tf}) tidak mencukupi", None

        ctx.buffers.update(feed.buffers)
        ctx.feed = feed
        self.contexts[resolved] = ctx

        seed_last_signal(self, ctx=ctx)
        sim_replay(self, ctx=ctx)
        snap = build_snapshot(dict(ctx.buffers), self.cfg, ctx.signals_log, ctx.label,
                              view_tf=self.entry_tf, htf_tf=self.htf)
        if snap:
            snap["demo"] = self.demo
            snap["exec_mode"] = "mt5" if self.provider == "mt5" else self.executor.mode
            snap["engine_entry"] = self.entry_tf
            self._decorate(snap, ctx=ctx)
            self._refresh_live_fields(snap, ctx=ctx)
        ctx.snapshot_data = snap

        # Segarkan daftar `symbols` pada snapshot yang sudah ada
        for c in self.contexts.values():
            if c.snapshot_data is not None:
                c.snapshot_data["symbols"] = self.symbol_list()

        if start_feed:
            loop = getattr(self.server, "_loop", None)
            if loop is not None:
                def _spawn():
                    ctx._feed_task = asyncio.create_task(feed.run())
                loop.call_soon_threadsafe(_spawn)

        if persist:
            self.app_settings["mt5_symbols"] = self.symbol_list()
            self.persist_app_settings()
        print(f"[pair] ditambahkan: {resolved} (total {len(self.contexts)} pair)", flush=True)
        return True, f"Pair {resolved} ditambahkan ({len(ctx.sim.trades)} trade replay)", resolved

    def remove_mt5_symbol(self, sym_name):
        """Hapus pair dari watchlist dashboard (minimal 1 pair tetap aktif)."""
        if len(self.contexts) <= 1:
            return False, "Minimal harus ada 1 pair aktif"
        ctx = self.get_ctx(sym_name)
        if ctx is None or ctx.symbol.upper() != (sym_name or "").strip().upper():
            return False, f"Pair '{sym_name}' tidak ditemukan"
        if ctx.feed:
            ctx.feed.stop()
        if ctx._feed_task:
            try:
                ctx._feed_task.cancel()
            except Exception:
                pass
        removed = ctx.symbol
        self.contexts.pop(removed, None)
        if self._primary.symbol == removed:
            self._primary = next(iter(self.contexts.values()))
        for c in self.contexts.values():
            if c.snapshot_data is not None:
                c.snapshot_data["symbols"] = self.symbol_list()
        self.app_settings["mt5_symbols"] = self.symbol_list()
        self.persist_app_settings()
        print(f"[pair] dihapus: {removed}", flush=True)
        return True, f"Pair {removed} dihapus"

    def _refresh_live_fields(self, snap, ctx=None):
        """Segarkan harga live & status MT5 pada snapshot tanpa mengubah sinyal closed-bar."""
        if not snap:
            return snap
        ctx = ctx or self._primary
        mt5_st = self.mt5.status()
        snap["mt5"] = mt5_st
        snap["symbols"] = self.symbol_list()
        live_px = None
        if self.provider == "mt5" and mt5_st.get("connected"):
            q = self.mt5.live_quote(ctx.symbol)
            if q and q.get("bid"):
                live_px = q["bid"]
        if live_px is None:
            b1m = ctx.buffers.get("1m")
            if b1m is not None and len(b1m):
                raw = float(b1m["close"].iloc[-1])
                live_px = round(raw, price_digits(raw))
        if live_px is not None:
            if isinstance(snap.get("stats"), dict):
                snap["stats"]["price"] = live_px
            if isinstance(snap.get("golden"), dict):
                snap["golden"]["price"] = live_px
            if isinstance(snap.get("high_risk"), dict):
                snap["high_risk"]["price"] = live_px
            ctx.sim.update_open_floating(live_px)
            snap["sim"] = {"stats": ctx.sim.summary(),
                           "trades": ctx.sim.snapshot_list(50)}
        return snap

    # ---- dipakai DashboardServer
    def snapshot(self, view_tf=None, symbol=None):
        """Snapshot TF entry (cache) atau hitung on-demand untuk TF & pair lain."""
        ctx = self.get_ctx(symbol)
        if not view_tf or view_tf == self.entry_tf:
            return self._refresh_live_fields(ctx.snapshot_data, ctx=ctx)
        hit = ctx._view_cache.get(view_tf)
        if hit and (time.time() - hit[0]) < 5:
            return self._refresh_live_fields(hit[1], ctx=ctx)
        snap = build_snapshot(dict(ctx.buffers), self.cfg, ctx.signals_log,
                              symbol=ctx.label, view_tf=view_tf,
                              htf_tf=(self.htf if view_tf == self.entry_tf else None))
        if snap:
            snap["demo"] = self.demo
            snap["exec_mode"] = "mt5" if self.provider == "mt5" else self.executor.mode
            snap["engine_entry"] = self.entry_tf
            snap["view_only"] = True
            self._decorate(snap, ctx=ctx)
            self._refresh_live_fields(snap, ctx=ctx)
            ctx._view_cache[view_tf] = (time.time(), snap)
        return snap

    def _resolve_auto_mode(self, buf):
        """AUTO: pasang preset sensitivity sesuai kondisi market, dengan
        hysteresis AUTO_STABLE_BARS bar agar tidak bolak-balik tiap candle."""
        cond = market_condition(buf)[0]
        target = {"TRENDING": "standard", "SIDEWAYS": "pingpong"}[cond]
        if self._auto_cond is None:                        # resolusi pertama
            self._auto_cond = cond
            self._auto_resolved = target
            apply_mode(self.cfg, sensitivity=target, exit_protocol=self.exit_protocol)
            print(f"[auto] mode -> {target} ({cond})", flush=True)
            return
        if cond == self._auto_cond:
            self._auto_cand, self._auto_cand_n = None, 0
            return
        if self._auto_cand == cond:
            self._auto_cand_n += 1
        else:
            self._auto_cand, self._auto_cand_n = cond, 1
        if self._auto_cand_n >= AUTO_STABLE_BARS:
            self._auto_cond = cond
            self._auto_cand, self._auto_cand_n = None, 0
            self._auto_resolved = target
            apply_mode(self.cfg, sensitivity=target, exit_protocol=self.exit_protocol)
            print(f"[auto] mode -> {target} ({cond})", flush=True)

    def apply_settings(self, body):
        """POST /api/settings: sensitivity, exit_protocol, entry_tf, filter sinyal, telegram."""
        from .godmode_engine import apply_mode, SENSITIVITY_PRESETS, EXIT_PRESETS
        out = {"applied": []}
        changed_persist = False
        sens = body.get("sensitivity")
        ex = body.get("exit_protocol")
        sens_changed = sens in ("auto", *SENSITIVITY_PRESETS.keys())
        ex_changed = ex in EXIT_PRESETS.keys()
        if sens_changed:
            self.sensitivity = sens
            self.app_settings["sensitivity"] = sens
            changed_persist = True
            out["applied"].append("sensitivity:" + sens)
        if ex_changed:
            self.exit_protocol = ex
            self.app_settings["exit_protocol"] = ex
            changed_persist = True
            out["applied"].append("exit:" + ex)
        etf = body.get("entry_tf")
        htf_map = {"1m": "15m", "5m": "15m", "15m": "1h", "30m": "4h", "1h": "4h", "4h": "4h"}
        if etf in htf_map and etf != self.entry_tf:
            self.entry_tf = etf
            self.htf = body.get("htf") if body.get("htf") in htf_map else htf_map[etf]
            self.cfg.htf = self.htf
            self.app_settings["entry_tf"] = self.entry_tf
            self.app_settings["htf"] = self.htf
            changed_persist = True
            out["applied"].append(f"entry_tf:{self.entry_tf}/{self.htf}")
        if sens_changed or ex_changed:
            if self.sensitivity == "auto":
                if sens_changed:
                    self._auto_cond = None             # paksa resolusi ulang
                    buf = self.buffers.get(self.entry_tf)
                    if buf is not None and len(buf) > 300:
                        self._resolve_auto_mode(buf)
                else:
                    apply_mode(self.cfg,
                               sensitivity=(self._auto_resolved or "standard"),
                               exit_protocol=self.exit_protocol)
            else:
                apply_mode(self.cfg, sensitivity=self.sensitivity,
                           exit_protocol=self.exit_protocol)
        if "signal_golden" in body:
            self.signal_golden = bool(body["signal_golden"])
            self.app_settings["signal_golden"] = self.signal_golden
            changed_persist = True
            out["applied"].append(f"signal_golden:{self.signal_golden}")
        if "signal_high_risk" in body:
            self.signal_high_risk = bool(body["signal_high_risk"])
            self.app_settings["signal_high_risk"] = self.signal_high_risk
            changed_persist = True
            out["applied"].append(f"signal_high_risk:{self.signal_high_risk}")
        # pengaturan notifikasi Telegram (persisten: cache/app_settings.json)
        st = getattr(self, "app_settings", None)
        if st is None:
            st = {}
        changed_tg = False
        for k in ("tg_token", "tg_chat_id", "tg_on", "notify_signals", "notify_trades"):
            if k in body:
                st[k] = body[k]
                changed_tg = True
        if changed_tg or changed_persist:
            self.app_settings = st
            self.persist_app_settings()
            if changed_tg:
                out["applied"].append("telegram")
        out["sensitivity"] = self.sensitivity
        out["exit_protocol"] = self.exit_protocol
        out["entry_tf"] = self.entry_tf
        out["htf"] = self.htf
        out["signal_golden"] = self.signal_golden
        out["signal_high_risk"] = self.signal_high_risk
        print(f"[settings] {out}", flush=True)
        return out

    def candles(self, tf, limit=1500, symbol=None):
        """Candle untuk chart: gabung history tradoor (deep) + buffer realtime."""
        ctx = self.get_ctx(symbol)
        df = ctx.buffers.get(tf)
        deep = ctx.deep_history.get(tf)
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
        if not ms:
            return None
        dp = price_digits(float(d["close"].iloc[-1])) if len(d) else 2
        rows = [[int(t.timestamp() * 1000), round(float(r["open"]), dp),
                 round(float(r["high"]), dp), round(float(r["low"]), dp),
                 round(float(r["close"]), dp), round(float(r["volume"]), 2)]
                for t, r in d.iterrows()]
        # Untuk provider MT5: tempelkan candle berjalan (start_pos=0) di ujung chart
        # agar grafik bergerak live sama persis dengan MT5 (engine tetap closed-bar)
        if self.provider == "mt5" and self.mt5.connected:
            live_bar = fetch_mt5_forming_candle(self.mt5._mt5, ctx.symbol, tf)
            if live_bar and (not rows or live_bar[0] > rows[-1][0]):
                rows.append(live_bar)
            elif live_bar and rows and live_bar[0] == rows[-1][0]:
                rows[-1] = live_bar
        return rows

    def signals(self, symbol=None):
        ctx = self.get_ctx(symbol)
        return {"symbol": ctx.symbol, "signals": ctx.signals_log}

    def sim_data(self, symbol=None):
        ctx = self.get_ctx(symbol)
        return {"symbol": ctx.symbol, "stats": ctx.sim.summary(),
                "trades": ctx.sim.snapshot_list(50)}

    def health(self):
        return {"ok": True, "symbol": self.label, "symbols": self.symbol_list(),
                "mode": self.executor.mode,
                "sensitivity": self.sensitivity, "exit_protocol": self.exit_protocol,
                "signal_golden": self.signal_golden, "signal_high_risk": self.signal_high_risk,
                "tfs": list(self.buffers), "bars": {t: len(b) for t, b in self.buffers.items()},
                "clients": len(self.server.clients) if self.server else 0}

    # ---- alur utama: candle close dari TF entry
    async def on_candle(self, interval, buf, demo=False, symbol=None):
        ctx = self.get_ctx(symbol)
        ctx.buffers[interval] = buf
        if interval != self.entry_tf:
            if self.server:                               # candle TF tampilan saja
                last = buf.iloc[-1]
                self.server.broadcast_threadsafe({
                    "type": "candle", "symbol": ctx.symbol, "tf": interval,
                    "candle": [int(buf.index[-1].timestamp() * 1000),
                               float(last["open"]), float(last["high"]),
                               float(last["low"]), float(last["close"]),
                               float(last["volume"])]})
            return
        htf_buf = ctx.buffers.get(self.htf)
        if len(buf) < 260 or htf_buf is None or len(htf_buf) < 210:
            return                                       # engine butuh warm-up

        async with self.lock:
            # simulator: proses candle yang baru close (hit SL/TP1/TP2/timeout)
            last_row = buf.iloc[-1]
            ctx.sim.on_candle(buf.index[-1], float(last_row["open"]),
                              float(last_row["high"]), float(last_row["low"]),
                              float(last_row["close"]))
            if self.sensitivity == "auto" and ctx is self._primary:
                self._resolve_auto_mode(buf)
            sig = generate_signals(buf, self.cfg, htf_df=htf_buf)
            self._handle_new_signals(sig, ctx=ctx)
            snap = build_snapshot(dict(ctx.buffers), self.cfg, ctx.signals_log,
                                  symbol=ctx.label, view_tf=self.entry_tf,
                                  htf_tf=self.htf)
            if snap:
                snap["demo"] = demo
                snap["exec_mode"] = "mt5" if self.provider == "mt5" else self.executor.mode
                snap["engine_entry"] = self.entry_tf
            snap = self._decorate(snap, ctx=ctx)
            ctx.snapshot_data = snap
            ctx._view_cache.clear()          # snapshot TF lain ikut basi

        if self.server:
            self.server.broadcast_threadsafe({"type": "snapshot", "symbol": ctx.symbol, "data": snap})
            last = buf.iloc[-1]
            self.server.broadcast_threadsafe({
                "type": "candle", "symbol": ctx.symbol, "tf": interval,
                "candle": [int(buf.index[-1].timestamp() * 1000),
                           float(last["open"]), float(last["high"]),
                           float(last["low"]), float(last["close"]),
                           float(last["volume"])]})

    def _classify(self, score):
        """GODMODE: skor engine 0-4 -> GOLDEN (>=3) / HIGH RISK (<=2)."""
        return "GOLDEN" if score >= 3 else "HIGH RISK"

    def _decorate(self, snap, ctx=None):
        """Tempelkan state filter sinyal + daftar sinyal tersaring ke snapshot."""
        ctx = ctx or self._primary
        if snap is not None:
            snap["symbols"] = self.symbol_list()
            snap["filters"] = {
                "golden": self.signal_golden,
                "high_risk": self.signal_high_risk,
                "suppressed": ctx.suppressed[:10],
                "suppressed_count": len(ctx.suppressed),
            }
            snap["mode"] = {"sensitivity": self.sensitivity,
                            "exit_protocol": self.exit_protocol,
                            "auto_resolved": self._auto_resolved}
            snap["sim"] = {"stats": ctx.sim.summary(),
                           "trades": ctx.sim.snapshot_list(50)}
            snap["news"] = {"items": self.news_feed.top_for_chart(3),
                            "count": len(self.news_feed.items),
                            "updated": self.news_feed.updated}
            snap["mt5"] = self.mt5.status()
            self._annotate_setup(snap, ctx=ctx)
        return snap

    def _annotate_setup(self, snap, ctx=None):
        """Hubungkan TRADE SETUP dengan filter + simulasi."""
        ctx = ctx or self._primary
        if not snap:
            return
        sim_open = ctx.sim.open
        sim_hist = ctx.sim.trades

        def sim_status_for(sig_time):
            norm = lambda s: str(s).replace("T", " ")[:19]   # isoformat vs str(Timestamp)
            for t in sim_open:
                if norm(t.get("sig_time")) == norm(sig_time):
                    return "OPEN", t.get("r", 0.0)
            for t in reversed(sim_hist):
                if norm(t.get("sig_time")) == norm(sig_time):
                    return t.get("status"), t.get("r", 0.0)
            return None, None

        su = snap.get("setup")
        if su:
            kind = "GOLDEN" if su.get("score", 0) >= 3 else "HIGH RISK"
            allowed = (kind == "GOLDEN" and self.signal_golden) or \
                      (kind == "HIGH RISK" and self.signal_high_risk)
            su["kind"] = kind
            su["executable"] = allowed
            st, r = sim_status_for(su.get("time"))
            su["sim_status"] = st or ("FILTERED" if not allowed else "QUEUED")
            su["sim_r"] = r
        elif sim_open:
            # tidak ada setup baru, tapi ada posisi simulasi berjalan -> tampilkan
            t = sim_open[-1]
            snap["setup"] = {
                "side": t["side"], "time": t.get("sig_time", t["time"]),
                "entry": t["entry"], "sl": t["sl"], "tp1": t["tp1"], "tp2": t["tp2"],
                "rr": _rr_text(t["tp2"], t["entry"], t["sl"], t.get("risk")),
                "score": 0, "reason": "posisi simulasi berjalan", "fresh": False,
                "kind": t.get("kind", ""), "executable": True,
                "sim_status": "OPEN", "sim_r": t.get("r", 0.0), "running": True,
            }
            if "setup_label" in snap:
                snap["setup_label"] = f"RUNNING · {t.get('kind', '')}"
        elif snap.get("signals_log"):
            # sinyal terakhir ada di deep history (di luar buffer) -> tampilkan
            # rencananya + hasil simulasinya (WIN/LOSS/BE+ kalau sudah dieksekusi)
            e = snap["signals_log"][0]
            allowed = (e.get("kind") == "GOLDEN" and self.signal_golden) or \
                      (e.get("kind") == "HIGH RISK" and self.signal_high_risk)
            st_, r_ = sim_status_for(e.get("time"))
            snap["setup"] = {
                "side": e["side"], "time": e["time"],
                "entry": e["entry"], "sl": e["sl"], "tp1": e["tp1"], "tp2": e["tp2"],
                "rr": _rr_text(e["tp2"], e["entry"], e["sl"]),
                "score": e.get("score", 0),
                "reason": "sinyal terakhir (deep history)", "fresh": False,
                "kind": e.get("kind", ""), "executable": allowed,
                "sim_status": st_ or ("FILTERED" if not allowed else "QUEUED"),
                "sim_r": r_,
            }

    def recompute_signal_history(self, bars=25_000, ctx=None):
        """Hitung ulang sinyal historis dengan config mode yang baru."""
        ctx = ctx or self._primary
        try:
            import pandas as pd
            hist = ctx.buffers.get(self.entry_tf)
            deep = ctx.deep_history.get(self.entry_tf)
            src = None
            if deep is not None and len(deep):
                extra = hist[~hist.index.isin(deep.index)] if hist is not None and len(hist) else None
                src = pd.concat([deep, extra]) if extra is not None else deep
            elif hist is not None:
                src = hist
            if src is None or len(src) < 300:
                return
            htf_buf = ctx.buffers.get(self.htf)
            if self.sensitivity == "auto":
                sig = generate_signals_auto(src.tail(bars), htf_df=htf_buf,
                                            exit_protocol=self.exit_protocol)
                mode_txt = f"auto/{self.exit_protocol}"
            else:
                sig = generate_signals(src.tail(bars), self.cfg, htf_df=htf_buf)
                mode_txt = f"{self.sensitivity}/{self.exit_protocol}"
            entries = []
            for t, row in sig.tail(10).iterrows():
                dp = price_digits(float(row["entry"]))
                entries.append({
                    "time": t.isoformat(), "tf": self.entry_tf,
                    "side": "LONG" if row["signal"] == 1 else "SHORT",
                    "entry": round(float(row["entry"]), dp),
                    "sl": round(float(row["sl"]), dp),
                    "tp1": round(float(row["tp1"]), dp),
                    "tp2": round(float(row["tp2"]), dp),
                    "score": int(row["score"]),
                    "kind": self._classify(int(row["score"])),
                })
            ctx.signals_log = list(reversed(entries))
            if sig.index.size:
                ctx._last_sig_time = sig.index[-1]
            print(f"[mode][{ctx.symbol}] sinyal historis dihitung ulang: {len(sig)} sinyal "
                  f"dengan mode {mode_txt}", flush=True)
        except Exception as e:
            print(f"[mode][{ctx.symbol}] recompute gagal:", e, flush=True)

    def rebuild_and_broadcast(self, recompute_signals=False):
        """Bangun ulang snapshot semua pair dengan state terbaru dan broadcast."""
        try:
            for ctx in list(self.contexts.values()):
                if recompute_signals:
                    self.recompute_signal_history(ctx=ctx)
                    sim_replay(self, ctx=ctx)
                snap = build_snapshot(dict(ctx.buffers), self.cfg, ctx.signals_log,
                                      symbol=ctx.label, view_tf=self.entry_tf,
                                      htf_tf=self.htf)
                if snap:
                    snap["demo"] = self.demo
                    snap["exec_mode"] = "mt5" if self.provider == "mt5" else self.executor.mode
                    snap["engine_entry"] = self.entry_tf
                    self._decorate(snap, ctx=ctx)
                ctx.snapshot_data = snap
                ctx._view_cache.clear()
                if self.server and ctx is self._primary:
                    self.server.broadcast_threadsafe({"type": "snapshot", "symbol": ctx.symbol, "data": snap})
        except Exception as e:
            print("[settings] rebuild snapshot gagal:", e, flush=True)

    def _handle_new_signals(self, sig, ctx=None):
        ctx = ctx or self._primary
        if sig.empty:
            return
        for t, row in sig.iterrows():
            if ctx._last_sig_time is not None and t <= ctx._last_sig_time:
                continue
            ctx._last_sig_time = t
            dp = price_digits(float(row["entry"]))
            entry = {"time": t.isoformat(), "tf": self.entry_tf,
                     "side": "LONG" if row["signal"] == 1 else "SHORT",
                     "entry": round(float(row["entry"]), dp),
                     "sl": round(float(row["sl"]), dp),
                     "tp1": round(float(row["tp1"]), dp),
                     "tp2": round(float(row["tp2"]), dp),
                     "score": int(row["score"])}
            kind = self._classify(entry["score"])
            allowed = (kind == "GOLDEN" and self.signal_golden) or \
                      (kind == "HIGH RISK" and self.signal_high_risk)
            if not allowed:
                # disaring: tetap tercatat untuk analisis, tidak dieksekusi
                entry["kind"] = kind
                ctx.suppressed.insert(0, entry)
                ctx.suppressed = ctx.suppressed[:30]
                print(f"[sinyal disaring][{ctx.symbol}] {kind} {entry['side']} {entry['time']} "
                      f"entry={entry['entry']} skor={entry['score']} "
                      f"(SIGNAL {kind}: OFF)", flush=True)
                continue
            # gate news: jangan entry +-30 mnt di sekitar berita berdampak tinggi
            hit = None
            try:
                hit = self.news_feed.high_impact_near(t, window_min=30)
            except Exception:
                hit = None
            if hit is not None:
                entry["kind"] = "NEWS"
                ctx.suppressed.insert(0, entry)
                ctx.suppressed = ctx.suppressed[:30]
                print(f"[sinyal ditahan NEWS][{ctx.symbol}] {entry['side']} {entry['time']} "
                      f"entry={entry['entry']} — '{hit.get('title', '')[:70]}'",
                      flush=True)
                continue
            entry["kind"] = kind
            ctx.signals_log.insert(0, entry)
            ctx.signals_log = ctx.signals_log[:30]
            # simulasi internal: antre entry di open candle berikutnya
            ctx.sim.queue_signal(t, row["signal"], entry["entry"], entry["sl"],
                                 entry["tp1"], entry["tp2"],
                                 mode=self.sensitivity, kind=kind, symbol=ctx.label)
            print(f"[sinyal][{ctx.symbol}] {kind} {entry['time']} {ctx.label} {entry['tf']} "
                  f"{entry['side']} entry={entry['entry']} sl={entry['sl']} "
                  f"tp1={entry['tp1']} tp2={entry['tp2']} skor={entry['score']}", flush=True)
            self.executor.execute_signal(ctx.symbol, row)   # no-op saat mode off
            self.mt5.execute_signal(ctx.symbol, row, sig_time=entry["time"])  # no-op saat STANDBY
            self._notify(f"⚡ <b>{ctx.symbol}</b> {kind} {entry['side']} @ {entry['entry']}"
                         f" | SL {entry['sl']} TP2 {entry['tp2']} | skor {entry['score']}")

    def _notify(self, text):
        """Kirim notifikasi Telegram bila diatur & aktif (fire-and-forget)."""
        st = getattr(self, "app_settings", {}) or {}
        if not st.get("tg_on", True):
            return
        if text.startswith("⚡") and not st.get("notify_signals", True):
            return
        if text.startswith(("✅", "🔻", "⏱")) and not st.get("notify_trades", True):
            return
        token, chat = st.get("tg_token", ""), st.get("tg_chat_id", "")
        if not token or not chat:
            return
        try:
            import requests as _rq
            r = _rq.post(f"https://api.telegram.org/bot{token}/sendMessage",
                         json={"chat_id": chat, "text": text, "parse_mode": "HTML"},
                         timeout=10)
            if r.status_code != 200:
                print(f"[tg] gagal kirim ({r.status_code}): {r.text[:160]}", flush=True)
        except Exception as e:
            print(f"[tg] error kirim: {e}", flush=True)


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


def seed_last_signal(state, deep_bars=50_000, ctx=None):
    """Cari sinyal terakhir di deep history (tanpa eksekusi) supaya log sinyal
    dan fallback toggle RR langsung terisi saat boot."""
    ctx = ctx or state._primary
    try:
        import pandas as pd
        hist = ctx.buffers.get(state.entry_tf)
        deep = ctx.deep_history.get(state.entry_tf)
        if deep is not None and len(deep):
            extra = hist[~hist.index.isin(deep.index)] if hist is not None and len(hist) else None
            src = pd.concat([deep, extra]) if extra is not None else deep
        else:
            src = hist
        if src is None or len(src) < 300:
            return
        htf_buf = ctx.buffers.get(state.htf)
        if state.sensitivity == "auto":
            sig = generate_signals_auto(src.tail(deep_bars), htf_df=htf_buf,
                                        exit_protocol=state.exit_protocol)
        else:
            sig = generate_signals(src.tail(deep_bars), state.cfg, htf_df=htf_buf)
        if sig.empty:
            return
        t, row = sig.index[-1], sig.iloc[-1]
        kind = "GOLDEN" if int(row["score"]) >= 3 else "HIGH RISK"
        dp = price_digits(float(row["entry"]))
        ctx.signals_log = [{
            "time": t.isoformat(), "tf": state.entry_tf,
            "side": "LONG" if row["signal"] == 1 else "SHORT",
            "entry": round(float(row["entry"]), dp), "sl": round(float(row["sl"]), dp),
            "tp1": round(float(row["tp1"]), dp), "tp2": round(float(row["tp2"]), dp),
            "score": int(row["score"]), "kind": kind,
        }]
        ctx._last_sig_time = t
        print(f"[boot][{ctx.symbol}] sinyal terakhir (history): {kind} {ctx.signals_log[0]['side']} "
              f"{t} entry={ctx.signals_log[0]['entry']}", flush=True)
    except Exception as e:
        print(f"[boot][{ctx.symbol}] seed sinyal history gagal:", e, flush=True)


def sim_replay(state, bars=25_000, ctx=None):
    """Putar ulang history melalui simulator internal dengan config mode aktif."""
    ctx = ctx or state._primary

    def _src():
        import pandas as pd
        hist = ctx.buffers.get(state.entry_tf)
        deep = ctx.deep_history.get(state.entry_tf)
        if deep is not None and len(deep):
            extra = hist[~hist.index.isin(deep.index)] if hist is not None and len(hist) else None
            return pd.concat([deep, extra]) if extra is not None else deep
        return hist

    def _replay(cfg, mode_label, sens=None):
        sens = sens or state.sensitivity
        src = _src()
        if src is None or len(src) < 300:
            return 0
        src = src.tail(bars)
        htf_buf = ctx.buffers.get(state.htf)
        if sens == "auto":
            sig = generate_signals_auto(src, htf_df=htf_buf,
                                        exit_protocol=state.exit_protocol)
        else:
            sig = generate_signals(src, cfg, htf_df=htf_buf)
        sig_times = set(sig.index)
        ctx.sim.trades, ctx.sim.open, ctx.sim.pending = [], [], None
        ctx.sim.reset_stream()
        ctx.sim.persist_enabled = False               # replay: jangan tulis file 25rbx
        ctx.sim.equity = START_EQUITY                 # reset BENERAN ke $10.000
        for t, row in src.iterrows():
            ctx.sim.on_candle(t, row["open"], row["high"], row["low"], row["close"])
            if t in sig_times:
                r = sig.loc[t]
                ctx.sim.queue_signal(t, int(r["signal"]), float(r["entry"]),
                                     float(r["sl"]), float(r["tp1"]), float(r["tp2"]),
                                     mode=mode_label, kind=state._classify(int(r["score"])),
                                     symbol=ctx.label)
        ctx.sim.persist_enabled = True                # hasil replay disimpan sekali
        ctx.sim.save()
        return len(ctx.sim.trades)

    try:
        n = _replay(state.cfg, state.sensitivity)
        replay_mode = state.sensitivity
        if n == 0 and state.sensitivity != "auto":
            from .confluence_engine import Config as _C
            from .godmode_engine import apply_mode as _am
            cfg_std = _am(_C(), sensitivity="standard",
                          exit_protocol=state.exit_protocol)
            n = _replay(cfg_std, "standard (replay)", sens="standard")
            replay_mode = "standard (replay)"
        print(f"[sim][{ctx.symbol}] replay {bars} bar selesai: {n} trade "
              f"(mode replay: {replay_mode})", flush=True)
    except Exception as e:
        print(f"[sim][{ctx.symbol}] replay gagal:", e, flush=True)


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
    ap.add_argument("--entry-tf", default=os.getenv("AT_ENTRY_TF", ""))
    ap.add_argument("--htf", default=os.getenv("AT_HTF", ""))
    ap.add_argument("--market", default=os.getenv("AT_MARKET", "spot"),
                    choices=["spot", "futures"])
    ap.add_argument("--provider", default=os.getenv("AT_PROVIDER", "binance"),
                    choices=["binance", "yahoo", "yfinance", "tradoor", "mt5"],
                    help="binance = realtime WS (fallback otomatis); "
                         "yahoo = polling raw chart API; "
                         "yfinance = polling via library yfinance (GC=F lebih stabil); "
                         "tradoor = baca partisi TDC1 tradoor self-host; "
                         "mt5 = candle langsung dari MetaTrader 5 lokal (Windows)")
    ap.add_argument("--mt5-symbol", default=os.getenv("AT_MT5_SYMBOL", ""),
                    help="simbol broker MT5 utama (kosong = auto-detect XAUUSD/XAUUSDm/GOLD)")
    ap.add_argument("--mt5-symbols", default=os.getenv("AT_MT5_SYMBOLS", ""),
                    help="daftar multi-pair MT5 dipisah koma, mis. XAUUSD,EURUSD,GBPUSD,USDJPY")
    ap.add_argument("--mt5-path", default=os.getenv("AT_MT5_PATH", ""),
                    help="opsional path ke terminal64.exe MT5")
    ap.add_argument("--mt5-transport", default=os.getenv("AT_MT5_TRANSPORT", ""),
                    choices=["", "python", "mcp"],
                    help="transport MT5: python (IPC lokal) atau mcp (HTTP MCP bawaan MT5)")
    ap.add_argument("--mt5-mcp-url", default=os.getenv("AT_MT5_MCP_URL", ""),
                    help="URL MT5 Terminal MCP Server, mis. http://127.0.0.1:22346/mcp")
    ap.add_argument("--mt5-mcp-key", default=os.getenv("AT_MT5_MCP_KEY", ""),
                    help="Bearer API key untuk MT5 Terminal MCP Server")
    ap.add_argument("--mt5-auto-arm", action="store_true",
                    default=os.getenv("AT_MT5_AUTO_ARM", "").lower() in ("1", "true", "yes"),
                    help="otomatis ARM Auto-Trade MT5 saat boot (tetap patuh pengaman DEMO ONLY)")
    ap.add_argument("--signal-high-risk", action="store_true",
                    default=os.getenv("AT_SIGNAL_HIGH_RISK", "").lower() in ("1", "true", "yes"),
                    help="aktifkan filter SIGNAL HIGH RISK: ON saat boot")
    ap.add_argument("--open-browser", action="store_true",
                    help="otomatis buka dashboard di browser setelah server siap")
    ap.add_argument("--tradoor-data", default=os.getenv("AT_TRADOOR_DATA", ""),
                    help="direktori data tradoor ATAU base URL /data/... server tradoor")
    ap.add_argument("--tradoor-market", default=os.getenv("AT_TRADOOR_MARKET", ""),
                    help="market tradoor, mis. binance-perp:BTCUSDT (default dari --symbol)")
    ap.add_argument("--bars", type=int, default=int(os.getenv("AT_BARS", "1500")))
    ap.add_argument("--sensitivity", default=os.getenv("AT_SENSITIVITY", ""),
                    help="preset mode saat boot (kosong = pakai pilihan terakhir user di UI)")
    ap.add_argument("--exit", dest="exit_protocol",
                    default=os.getenv("AT_EXIT", ""),
                    help="exit protocol saat boot (kosong = pakai pilihan terakhir user di UI)")
    ap.add_argument("--port", type=int,
                    default=int(os.getenv("AT_PORT") or os.getenv("PORT") or "8788"))
    ap.add_argument("--ws-port", type=int, default=int(os.getenv("AT_WS_PORT", "8789")))
    args = ap.parse_args()

    executor = TestnetExecutor(market=args.market)
    state = AppState(args.symbol, Config(), executor, label=args.label)
    state.provider = args.provider
    state.bars = args.bars
    # Prioritas konfigurasi: argumen CLI eksplisit > pilihan terakhir user di UI (cache/app_settings.json) > default
    saved = state.app_settings or {}
    args.entry_tf = args.entry_tf or saved.get("entry_tf") or "1h"
    args.htf = args.htf or saved.get("htf") or "4h"
    state.cfg.htf = args.htf
    cfg = state.cfg
    from .godmode_engine import SENSITIVITY_PRESETS, EXIT_PRESETS, apply_mode
    eff_sens = args.sensitivity or saved.get("sensitivity") or "auto"
    eff_exit = args.exit_protocol or saved.get("exit_protocol") or "quick"
    if eff_sens in SENSITIVITY_PRESETS or eff_sens == "auto":
        state.sensitivity = eff_sens
    if eff_exit in EXIT_PRESETS:
        state.exit_protocol = eff_exit
    if "signal_golden" in saved:
        state.signal_golden = bool(saved["signal_golden"])
    if args.signal_high_risk:
        state.signal_high_risk = True
    elif "signal_high_risk" in saved:
        state.signal_high_risk = bool(saved["signal_high_risk"])
    if state.sensitivity != "auto":
        apply_mode(state.cfg, sensitivity=state.sensitivity,
                   exit_protocol=state.exit_protocol)
    server = DashboardServer(state, args.port, args.ws_port)
    state.server = server
    await server.start()
    if args.open_browser:
        try:
            import webbrowser
            webbrowser.open(f"http://localhost:{args.port}")
        except Exception:
            pass
    state.news_feed.maybe_refresh()                  # berita awal
    state.news_feed.start_background(300)            # refresh tiap 5 menit (cek TTL)

    # Override setting MT5 bila dikirim lewat argumen CLI
    cli_mt5 = {}
    if args.mt5_symbol:
        cli_mt5["mt5_symbol"] = args.mt5_symbol
    if args.mt5_path:
        cli_mt5["mt5_terminal_path"] = args.mt5_path
    if args.mt5_transport:
        cli_mt5["mt5_transport"] = args.mt5_transport
    if args.mt5_mcp_url:
        cli_mt5["mt5_mcp_url"] = args.mt5_mcp_url
    if args.mt5_mcp_key:
        cli_mt5["mt5_mcp_key"] = args.mt5_mcp_key
    if cli_mt5:
        state.mt5.update_settings(cli_mt5)
        if state.mt5.settings.get("mt5_mcp_url"):
            state.news_feed.configure_mcp(
                state.mt5.settings.get("mt5_mcp_url"),
                state.mt5.settings.get("mt5_mcp_key"),
            )
            state.news_feed.refresh(force=True)

    # Monitor Auto-BE MT5 + siaran tick live MT5 di background (tiap 3 detik)
    async def _mt5_be_monitor():
        while True:
            try:
                if state.mt5.connected:
                    state.mt5.check_breakeven()
                    if state.provider == "mt5" and state.server and state.server.clients:
                        for ctx in list(state.contexts.values()):
                            if ctx.snapshot_data:
                                state._refresh_live_fields(ctx.snapshot_data, ctx=ctx)
                                if ctx is state._primary:
                                    state.server.broadcast_threadsafe(
                                        {"type": "snapshot", "symbol": ctx.symbol, "data": ctx.snapshot_data})
                            for tf in LIVE_TFS:
                                bar = fetch_mt5_forming_candle(
                                    state.mt5._mt5, ctx.symbol, tf)
                                if bar:
                                    state.server.broadcast_threadsafe(
                                        {"type": "candle", "symbol": ctx.symbol, "tf": tf, "candle": bar})
            except Exception as e:
                print("[mt5:be-loop]", e, flush=True)
            await asyncio.sleep(3.0)

    asyncio.get_running_loop().create_task(_mt5_be_monitor())

    if args.demo:
        state.entry_tf, state.htf = "5m", "4h"
        state.demo = True
        print(f"[run] mode DEMO — {state.label} (sintetis) | TF: {', '.join(DEMO_TFS)} | "
              f"exec={executor.mode}", flush=True)
        feed = DemoFeed(entry_tf="5m", tfs=DEMO_TFS)
    elif args.provider == "mt5":
        state.entry_tf, state.htf = args.entry_tf, args.htf
        tfs = list(dict.fromkeys(LIVE_TFS + (args.entry_tf, args.htf)))
        req_sym = args.mt5_symbol or state.mt5.settings.get("mt5_symbol") or args.symbol
        mt5_path = args.mt5_path or state.mt5.settings.get("mt5_terminal_path") or ""
        mt5_feed = state._make_mt5_feed(req_sym)
        mt5_feed.seed()
        state.symbol = mt5_feed.symbol
        state._primary.feed = mt5_feed
        state.buffers.update(mt5_feed.buffers)
        state.mt5.update_settings({"mt5_symbol": mt5_feed.symbol, "mt5_terminal_path": mt5_path})
        state.mt5.connect()
        mt5_state_lbl = "STANDBY"
        if args.mt5_auto_arm:
            ok_arm, msg_arm = state.mt5.set_armed(True)
            mt5_state_lbl = "ARMED" if ok_arm else f"STANDBY ({msg_arm})"
        print(f"[run] {state.label} MT5 ({mt5_feed.symbol}) entry={args.entry_tf} "
              f"htf={args.htf} TF={','.join(tfs)} mt5={mt5_state_lbl}", flush=True)
        seed_last_signal(state)
        buf0 = state.buffers.get(state.entry_tf)
        if state.sensitivity == "auto" and buf0 is not None and len(buf0) > 300:
            state._resolve_auto_mode(buf0)
        sim_replay(state)
        snap = build_snapshot(state.buffers, cfg, state.signals_log, state.label,
                              view_tf=args.entry_tf, htf_tf=args.htf)
        if snap:
            snap["engine_entry"] = args.entry_tf
            snap["exec_mode"] = "mt5"
        state._decorate(snap)
        state.snapshot_data = snap

        # Muat multi-pair tambahan (dari --mt5-symbols, app_settings.json, atau default Forex majors)
        extra_syms = []
        if args.mt5_symbols:
            extra_syms = [s.strip() for s in args.mt5_symbols.split(",") if s.strip()]
        elif isinstance(state.app_settings.get("mt5_symbols"), list) and state.app_settings["mt5_symbols"]:
            extra_syms = [str(s).strip() for s in state.app_settings["mt5_symbols"] if str(s).strip()]
        else:
            extra_syms = ["EURUSD", "GBPUSD", "USDJPY"]

        for es in extra_syms:
            if es.upper() == mt5_feed.symbol.upper():
                continue
            ok_add, msg_add, _ = state.add_mt5_symbol(es, start_feed=False, persist=False)
            if not ok_add:
                print(f"[pair] lewati {es}: {msg_add}", flush=True)

        # Pastikan daftar simbol terbaru tersimpan dan terpasang di semua snapshot
        for c in state.contexts.values():
            if c.snapshot_data is not None:
                c.snapshot_data["symbols"] = state.symbol_list()

        # Jalankan semua feed MT5 secara paralel
        tasks = []
        for c in state.contexts.values():
            if c.feed is not None:
                c._feed_task = asyncio.create_task(c.feed.run())
                tasks.append(c._feed_task)
        return await asyncio.gather(*tasks)
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
        buf0 = state.buffers.get(state.entry_tf)
        if state.sensitivity == "auto" and buf0 is not None and len(buf0) > 300:
            state._resolve_auto_mode(buf0)               # badge MODE langsung terisi
        sim_replay(state)                                # isi kartu SIMULASI dengan hasil replay
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
