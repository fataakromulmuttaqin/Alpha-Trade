"""
Paper simulator Alpha-Trade — mengeksekusi sinyal engine secara internal
-------------------------------------------------------------------------
Aturan (konsisten dengan backtest.py):
  - entry di OPEN bar berikutnya setelah sinyal
  - SL dan TP tersentuh di bar yang sama -> dianggap SL dulu (konservatif)
  - TP1 (50% posisi) tercapai -> stop pindah ke breakeven, sisa menuju TP2
  - time stop: 200 bar -> tutup di close
  - risiko per trade: risk_pct% dari ekuitas (default 1%)

Trade record:
  {id, time, side, entry, sl, tp1, tp2, risk, status: OPEN|WIN|LOSS|BE+|TIME,
   exit_time, exit_price, r (realized R total), bars, mode, kind}
Ekuitas bergerak: equity *= 1 + risk_pct/100 * r_total
Persist ke JSON agar history bertahan antar restart.
"""
import json
import os

from .godmode_engine import price_digits

RISK_PCT = 1.0
START_EQUITY = 10_000.0
MAX_HOLD = 200

# Re-entry setelah stop-out kena sweep (opsi 3): bila SL kena di bar yang
# JUGA menyapu likuiditas (high > high 20 bar sebelumnya untuk SHORT, atau
# low < low 20 bar sebelumnya untuk LONG) tapi gagal ditahan (close kembali
# di dalam range) -> arahkan re-entry arah sama di open bar berikutnya dengan
# SL di luar extreme sweep ± buffer ATR. Satu generasi saja (re-entry tidak
# memancing re-entry lagi) dan tetap lolos gate risiko 1-2.5 x ATR.
REENTRY_LOOKBACK = 20
REENTRY_BUF_ATR = 0.5
REENTRY_MIN_RISK_ATR = 1.0
REENTRY_MAX_RISK_ATR = 2.5
REENTRY_TP1_RR = 1.5
REENTRY_TP2_RR = 3.0


class PaperSim:
    def __init__(self, path="sim_trades.json", risk_pct=RISK_PCT,
                 daily_stop_r=0.0, daily_max_trades=0, default_symbol=""):
        self.path = path
        self.risk_pct = risk_pct
        self.daily_stop_r = daily_stop_r        # berhenti entry setelah -X R/hari
        self.daily_max_trades = daily_max_trades  # batas entry per hari (0 = off)
        self.default_symbol = default_symbol
        self.trades = []          # trade yang sudah selesai (history)
        self.open = []            # trade berjalan
        self.pending = None       # sinyal menunggu fill di open berikutnya
        self.equity = START_EQUITY
        self._id = 0
        self.persist_enabled = True   # False saat replay: jangan tulis file 25rbx
        self.reentry = None       # re-entry ter-arm setelah stop-out kena sweep
        self._atr = None          # ATR streaming (Wilder) untuk sizing re-entry
        self._prev_c = None
        self._hist = []           # (h, l, c) N bar terakhir untuk deteksi sweep
        self.load()

    # ----------------------------- persistence -----------------------------
    def load(self):
        try:
            if os.path.exists(self.path):
                d = json.load(open(self.path, encoding="utf-8"))
                self.trades = d.get("trades", [])
                self.open = d.get("open", [])
                self.equity = d.get("equity", START_EQUITY)
                self._id = d.get("_id", 0)
        except Exception as e:
            print("[sim] load gagal:", e)

    def save(self):
        if not self.persist_enabled:
            return
        try:
            json.dump({"trades": self.trades[-300:], "open": self.open,
                       "equity": self.equity, "_id": self._id},
                      open(self.path, "w", encoding="utf-8"), default=str)
        except Exception as e:
            print("[sim] save gagal:", e)

    # ----------------------------- signal in -------------------------------
    def queue_signal(self, t, side, entry, sl, tp1, tp2, mode="", kind="", symbol=""):
        """Sinyal lolos filter -> antre; fill di OPEN candle berikutnya.
        Gate risiko harian: daily stop (rugi kumulatif hari entry) & batas
        jumlah trade per hari — trade yang sedang berjalan tidak dihitung."""
        day = str(t)[:10]
        if self.daily_stop_r > 0:
            day_r = sum(float(x.get("r", 0) or 0) for x in self.trades
                        if str(x.get("time", ""))[:10] == day)
            if day_r <= -self.daily_stop_r:
                print(f"[sim] daily stop {day}: {day_r:.2f}R <= "
                      f"-{self.daily_stop_r}R — sinyal dilewati", flush=True)
                return
        if self.daily_max_trades > 0:
            day_n = sum(1 for x in self.trades
                        if str(x.get("time", ""))[:10] == day)
            if day_n >= self.daily_max_trades:
                print(f"[sim] batas {self.daily_max_trades} trade/hari tercapai "
                      f"({day}) — sinyal dilewati", flush=True)
                return
        self.pending = {"sig_time": str(t), "side": "LONG" if side == 1 else "SHORT",
                        "sig_entry": float(entry), "sl": float(sl), "tp1": float(tp1),
                        "tp2": float(tp2), "mode": mode, "kind": kind,
                        "symbol": symbol or self.default_symbol}

    def reset_stream(self):
        """Reset state streaming (ATR/history/re-entry) — dipakai sim_replay."""
        self.reentry, self._atr, self._prev_c, self._hist = None, None, None, []

    # ----------------------------- re-entry sweep --------------------------
    def _update_stream(self, h, l, c):
        tr = h - l if self._prev_c is None else max(
            h - l, abs(h - self._prev_c), abs(l - self._prev_c))
        self._atr = tr if self._atr is None else self._atr + (tr - self._atr) / 14
        self._prev_c = c

    def _try_arm_reentry(self, tr, t, h, l, c):
        """Stop-out di bar yang menyapu likuiditas tapi gagal ditahan -> arm
        re-entry arah sama, SL di luar extreme sweep. Hanya untuk LOSS asli
        (bukan BE+) dan bukan trade re-entry sendiri (satu generasi)."""
        if tr.get("kind") == "RE-ENTRY" or tr.get("status") != "LOSS":
            return
        if self._atr is None or len(self._hist) < REENTRY_LOOKBACK:
            return
        prior = self._hist[-REENTRY_LOOKBACK:]
        if tr["side"] == "SHORT":
            hi = max(b[0] for b in prior)
            if not (h > hi and c < hi):          # sweep buy-side yang gagal
                return
            extreme = h
        else:
            lo = min(b[1] for b in prior)
            if not (l < lo and c > lo):          # sweep sell-side yang gagal
                return
            extreme = l
        self.reentry = {"side": tr["side"], "extreme": extreme, "atr": self._atr,
                        "sig_time": str(t), "mode": tr.get("mode", ""),
                        "symbol": tr.get("symbol") or self.default_symbol,
                        "reentry_of": tr.get("id")}
        print(f"[sim] re-entry ter-arm: {tr['side']} setelah sweep {round(extreme, 2)} "
              f"(SL di luar extreme +/- {REENTRY_BUF_ATR}xATR)", flush=True)

    def _fill_reentry(self, t, o):
        """Eksekusi re-entry ter-arm di OPEN candle berikutnya."""
        r, self.reentry = self.reentry, None
        if r is None:
            return
        atr = r["atr"]
        d = 1 if r["side"] == "LONG" else -1
        sl = r["extreme"] - d * REENTRY_BUF_ATR * atr   # SHORT: atas sweep-high, LONG: bawah sweep-low
        dist = d * (o - sl)
        if not (REENTRY_MIN_RISK_ATR * atr <= dist <= REENTRY_MAX_RISK_ATR * atr):
            print(f"[sim] re-entry {r['side']} dilewati: jarak SL {round(dist, 2)} "
                  f"di luar {REENTRY_MIN_RISK_ATR}-{REENTRY_MAX_RISK_ATR}xATR", flush=True)
            return
        self._id += 1
        dp = price_digits(o)
        tp1 = o + d * REENTRY_TP1_RR * dist
        tp2 = o + d * REENTRY_TP2_RR * dist
        tp3 = tp2 + (tp2 - tp1)
        risk_usd = round(self.equity * (self.risk_pct / 100.0), 2)
        units = round(risk_usd / max(dist, 1e-9), 3)
        self.open.append({
            "id": self._id, "time": str(t),
            "symbol": r.get("symbol") or self.default_symbol,
            "side": r["side"],
            "entry": round(o, dp), "sl": round(sl, dp),
            "tp1": round(tp1, dp), "tp2": round(tp2, dp), "tp3": round(tp3, dp),
            "risk": round(dist, dp), "risk_usd": risk_usd, "units": units,
            "pl_usd": 0.0, "floating_r": 0.0,
            "r": 0.0, "bars": 0, "hit1": False, "status": "OPEN",
            "mode": r.get("mode", ""), "kind": "RE-ENTRY",
            "reason": f"Re-entry pasca sweep — menunggu TP1 ({round(tp1, dp)})",
            "sig_time": r.get("sig_time"), "reentry_of": r.get("reentry_of"),
        })
        self.save()
        print(f"[sim] re-entry {r['side']} fill @{round(o, 2)} sl={round(sl, 2)} "
              f"(stop di luar extreme sweep)", flush=True)

    # ----------------------------- per candle ------------------------------
    def on_candle(self, t, o, h, l, c):
        """Proses satu candle yang sudah close (t, o, h, l, c)."""
        self._update_stream(h, l, c)
        # 0) re-entry hasil sweep stop-out: fill di open candle ini
        self._fill_reentry(t, o)
        # 1) isi pending di open candle ini
        if self.pending is not None:
            p = self.pending
            self.pending = None
            side = 1 if p["side"] == "LONG" else -1
            entry = float(o)
            sl, tp1, tp2 = (p["sl"], p["tp1"], p["tp2"])
            shift = entry - p["sig_entry"]
            sl, tp1, tp2 = sl + shift, tp1 + shift, tp2 + shift
            risk = abs(entry - sl)
            if risk > 0:
                self._id += 1
                dp = price_digits(entry)
                tp3 = tp2 + (tp2 - tp1)
                risk_usd = round(self.equity * (self.risk_pct / 100.0), 2)
                units = round(risk_usd / max(risk, 1e-9), 3)
                self.open.append({
                    "id": self._id, "time": str(t),
                    "symbol": p.get("symbol") or self.default_symbol,
                    "side": p["side"],
                    "entry": round(entry, dp), "sl": round(sl, dp),
                    "tp1": round(tp1, dp), "tp2": round(tp2, dp), "tp3": round(tp3, dp),
                    "risk": round(risk, dp), "risk_usd": risk_usd, "units": units,
                    "pl_usd": 0.0, "floating_r": 0.0,
                    "r": 0.0, "bars": 0, "hit1": False, "status": "OPEN",
                    "mode": p.get("mode", ""), "kind": p.get("kind", ""),
                    "reason": f"Posisi berjalan — menunggu TP1 ({round(tp1, dp)})",
                    "sig_time": p["sig_time"],
                })
                self.save()
        # 2) evaluasi posisi berjalan (SL didahulukan bila kena bersamaan)
        still = []
        for tr in self.open:
            d = 1 if tr["side"] == "LONG" else -1
            risk = tr["risk"] or 1e-9
            done = None
            stop_hit = (l <= tr["sl"]) if d == 1 else (h >= tr["sl"])
            tp1_hit = (h >= tr["tp1"]) if d == 1 else (l <= tr["tp1"])
            tp2_hit = (h >= tr["tp2"]) if d == 1 else (l <= tr["tp2"])
            if stop_hit:                                   # SL dulu (konservatif)
                part = d * (tr["sl"] - tr["entry"]) / risk
                tr["r"] += (0.5 if tr["hit1"] else 1.0) * part
                done = ("LOSS" if tr["r"] < 0 else "BE+")
                tr["exit_time"], tr["exit_price"] = str(t), tr["sl"]
            else:
                if not tr["hit1"] and tp1_hit:
                    tr["hit1"] = True
                    tr["r"] += 0.5 * d * (tr["tp1"] - tr["entry"]) / risk
                    tr["sl"] = tr["entry"]                 # breakeven
                if tr["hit1"] and tp2_hit:
                    tr["r"] += 0.5 * d * (tr["tp2"] - tr["entry"]) / risk
                    done = "WIN"
                    tr["exit_time"], tr["exit_price"] = str(t), tr["tp2"]
            tr["bars"] += 1
            if done is None and tr["bars"] >= MAX_HOLD:    # time stop
                tr["r"] += (0.5 if tr["hit1"] else 1.0) * d * (c - tr["entry"]) / risk
                done = "TIME"
                tr["exit_time"], tr["exit_price"] = str(t), c
            if done:
                tr["status"] = done
                tr["r"] = round(tr["r"], 3)
                eq_before = self.equity
                tr["risk_usd"] = round(eq_before * (self.risk_pct / 100.0), 2)
                tr["units"] = round(tr["risk_usd"] / max(risk, 1e-9), 3)
                tr["equity_after"] = round(eq_before * (1 + self.risk_pct / 100.0 * tr["r"]), 2)
                tr["pl_usd"] = round(tr["equity_after"] - eq_before, 2)
                dp = price_digits(tr["entry"])
                tr["tp3"] = round(tr["tp2"] + (tr["tp2"] - tr["tp1"]), dp)
                tr["symbol"] = tr.get("symbol") or self.default_symbol
                if done == "WIN":
                    tr["reason"] = "TP2 tercapai — target penuh tercapai"
                elif done == "BE+":
                    tr["reason"] = "TP1 tercapai (+50%), sisa keluar di breakeven — rugi 0"
                elif done == "LOSS":
                    tr["reason"] = "SL tersentuh sebelum TP1 — harga berbalik arah"
                elif done == "TIME":
                    tr["reason"] = f"Time-stop {MAX_HOLD} bar — keluar di harga close"
                self.equity = tr["equity_after"]
                self.trades.append(tr)
                if done == "LOSS":
                    self._try_arm_reentry(tr, t, h, l, c)
                self.save()                                # simpan hanya saat trade selesai
            else:
                tr["r"] = round(tr["r"], 3)
                rem_w = 0.5 if tr["hit1"] else 1.0
                flt_r = tr["r"] + rem_w * d * (float(c) - tr["entry"]) / risk
                risk_usd = tr.get("risk_usd") or round(self.equity * (self.risk_pct / 100.0), 2)
                tr["risk_usd"] = risk_usd
                tr["units"] = tr.get("units") or round(risk_usd / max(risk, 1e-9), 3)
                tr["tp3"] = tr.get("tp3") or round(tr["tp2"] + (tr["tp2"] - tr["tp1"]), 2)
                tr["floating_r"] = round(flt_r, 3)
                tr["pl_usd"] = round(risk_usd * flt_r, 2)
                tr["reason"] = (
                    f"TP1 kena (+50%) · SL di BE ({tr['sl']}) · mengejar TP2 ({tr['tp2']})"
                    if tr["hit1"]
                    else f"Posisi berjalan · menunggu TP1 ({tr['tp1']}) / SL ({tr['sl']})"
                )
                still.append(tr)
        self.open = still
        self._hist.append((h, l, c))
        self._hist = self._hist[-25:]

    def update_open_floating(self, live_price):
        """Segarkan floating P/L & R posisi OPEN memakai harga live terbaru."""
        if live_price is None:
            return
        px = float(live_price)
        for tr in self.open:
            d = 1 if tr["side"] == "LONG" else -1
            risk = tr.get("risk") or abs(tr["entry"] - tr["sl"]) or 1e-9
            rem_w = 0.5 if tr.get("hit1") else 1.0
            flt_r = float(tr.get("r", 0.0)) + rem_w * d * (px - tr["entry"]) / risk
            risk_usd = tr.get("risk_usd") or round(self.equity * (self.risk_pct / 100.0), 2)
            tr["risk_usd"] = risk_usd
            tr["units"] = tr.get("units") or round(risk_usd / max(risk, 1e-9), 3)
            tr["tp3"] = tr.get("tp3") or round(tr["tp2"] + (tr["tp2"] - tr["tp1"]), 2)
            tr["floating_r"] = round(flt_r, 3)
            tr["pl_usd"] = round(risk_usd * flt_r, 2)

    # ----------------------------- ringkasan -------------------------------
    def summary(self):
        done = self.trades
        rs = [t["r"] for t in done]
        wins = [r for r in rs if r > 0]
        losses = [r for r in rs if r <= 0]
        gp, gl = sum(wins), sum(abs(r) for r in losses)
        eq_curve = []
        eq = START_EQUITY
        pl_win_usd = 0.0
        pl_loss_usd = 0.0
        for t in done:
            prev_eq = eq
            eq = t.get("equity_after", eq)
            eq_curve.append(round(eq, 2))
            pl = t.get("pl_usd")
            if pl is None:
                pl = eq - prev_eq
            if t["r"] > 0:
                pl_win_usd += float(pl)
            else:
                pl_loss_usd += float(pl)
        peak, maxdd = START_EQUITY, 0.0
        for v in eq_curve:
            peak = max(peak, v)
            maxdd = min(maxdd, (v / peak - 1) * 100) if peak else maxdd
        return {
            "equity": round(self.equity, 2),
            "trades": len(done),
            "open": len(self.open),
            "wins": len(wins), "losses": len(losses),
            "winrate": round(len(wins) / len(done) * 100, 1) if done else 0.0,
            "avg_r": round(sum(rs) / len(rs), 3) if rs else 0.0,
            "profit_factor": round(gp / gl, 2) if gl > 0 else None,
            "max_dd": round(maxdd, 2),
            "return_pct": round((self.equity / START_EQUITY - 1) * 100, 2),
            "pl_win_usd": round(pl_win_usd, 2),
            "pl_loss_usd": round(pl_loss_usd, 2),
            "net_usd": round(self.equity - START_EQUITY, 2),
        }

    def _enrich_item(self, t, is_open=False):
        item = dict(t)
        if is_open:
            item["status"] = "OPEN"
        risk = item.get("risk") or abs(item.get("entry", 0) - item.get("sl", 0)) or 1e-9
        risk_usd = item.get("risk_usd")
        if risk_usd is None:
            risk_usd = round(self.equity * (self.risk_pct / 100.0), 2)
        item["risk_usd"] = round(float(risk_usd), 2)
        item["units"] = round(float(item.get("units") or (risk_usd / max(risk, 1e-9))), 3)
        dp = price_digits(item.get("entry") or 0)
        if item.get("tp3") is None and item.get("tp1") is not None and item.get("tp2") is not None:
            item["tp3"] = round(item["tp2"] + (item["tp2"] - item["tp1"]), dp)
        if item.get("pl_usd") is None:
            item["pl_usd"] = round(item["risk_usd"] * float(item.get("r", 0.0)), 2)
        item["symbol"] = item.get("symbol") or self.default_symbol
        if not item.get("reason"):
            st = item.get("status")
            if st == "OPEN":
                item["reason"] = (
                    f"TP1 kena (+50%) · SL di BE ({item.get('sl')}) · mengejar TP2 ({item.get('tp2')})"
                    if item.get("hit1")
                    else f"Posisi berjalan · menunggu TP1 ({item.get('tp1')}) / SL ({item.get('sl')})"
                )
            elif st == "WIN":
                item["reason"] = "TP2 tercapai — target penuh tercapai"
            elif st == "BE+":
                item["reason"] = "TP1 tercapai (+50%), sisa keluar di breakeven"
            elif st == "LOSS":
                item["reason"] = "SL tersentuh sebelum TP1"
            elif st == "TIME":
                item["reason"] = f"Time-stop {MAX_HOLD} bar"
        return item

    def snapshot_list(self, n=30):
        """Trade berjalan + history terbaru (terbaru dulu) untuk chart & UI."""
        open_list = [self._enrich_item(t, is_open=True) for t in self.open]
        hist = [self._enrich_item(t, is_open=False) for t in reversed(self.trades[-n:])]
        return open_list + hist
