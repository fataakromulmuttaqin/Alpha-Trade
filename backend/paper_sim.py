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

RISK_PCT = 1.0
START_EQUITY = 10_000.0
MAX_HOLD = 200


class PaperSim:
    def __init__(self, path="sim_trades.json", risk_pct=RISK_PCT):
        self.path = path
        self.risk_pct = risk_pct
        self.trades = []          # trade yang sudah selesai (history)
        self.open = []            # trade berjalan
        self.pending = None       # sinyal menunggu fill di open berikutnya
        self.equity = START_EQUITY
        self._id = 0
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
        try:
            json.dump({"trades": self.trades[-300:], "open": self.open,
                       "equity": self.equity, "_id": self._id},
                      open(self.path, "w", encoding="utf-8"), default=str)
        except Exception as e:
            print("[sim] save gagal:", e)

    # ----------------------------- signal in -------------------------------
    def queue_signal(self, t, side, entry, sl, tp1, tp2, mode="", kind=""):
        """Sinyal lolos filter -> antre; fill di OPEN candle berikutnya."""
        self.pending = {"sig_time": str(t), "side": "LONG" if side == 1 else "SHORT",
                        "sig_entry": float(entry), "sl": float(sl), "tp1": float(tp1),
                        "tp2": float(tp2), "mode": mode, "kind": kind}

    # ----------------------------- per candle ------------------------------
    def on_candle(self, t, o, h, l, c):
        """Proses satu candle yang sudah close (t, o, h, l, c)."""
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
                self.open.append({
                    "id": self._id, "time": str(t), "side": p["side"],
                    "entry": round(entry, 2), "sl": round(sl, 2),
                    "tp1": round(tp1, 2), "tp2": round(tp2, 2),
                    "risk": round(risk, 2), "r": 0.0, "bars": 0,
                    "hit1": False, "status": "OPEN",
                    "mode": p.get("mode", ""), "kind": p.get("kind", ""),
                    "sig_time": p["sig_time"],
                })
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
                tr["equity_after"] = round(self.equity * (1 + self.risk_pct / 100 * tr["r"]), 2)
                self.equity = tr["equity_after"]
                self.trades.append(tr)
            else:
                tr["r"] = round(tr["r"], 3)
                still.append(tr)
        self.open = still
        self.save()

    # ----------------------------- ringkasan -------------------------------
    def summary(self):
        done = self.trades
        rs = [t["r"] for t in done]
        wins = [r for r in rs if r > 0]
        losses = [r for r in rs if r <= 0]
        gp, gl = sum(wins), sum(abs(r) for r in losses)
        eq_curve = []
        eq = START_EQUITY
        for t in done:
            eq = t.get("equity_after", eq)
            eq_curve.append(round(eq, 2))
        peak, maxdd = eq, 0.0
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
        }

    def snapshot_list(self, n=30):
        """Trade berjalan + history terbaru (terbaru dulu) untuk chart & UI."""
        open_list = [{**t, "status": "OPEN"} for t in self.open]
        hist = list(reversed(self.trades[-n:]))
        return open_list + hist
