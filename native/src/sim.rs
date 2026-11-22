//! PaperSim — port inti backend/paper_sim.py: entry open berikutnya,
//! SL-first, TP1 50% -> BE, TP2, time stop 200 bar, equity 1%/trade,
//! daily stop. (Re-entry sweep: iterasi berikutnya.)

use crate::engine::Candle;
use serde_json::json;

#[derive(Clone)]
pub struct SimTrade {
    pub id: u64,
    pub time_ms: i64,
    pub symbol: String,
    pub side: &'static str,
    pub entry: f64, pub sl: f64, pub tp1: f64, pub tp2: f64,
    pub risk0: f64,            // |entry - sl awal| saat fill
    pub eff_risk: f64,         // risiko efektif % (vol-targeted)
    pub r: f64,                // R terakumulasi (realized)
    pub bars: i64, pub hit1: bool,
    pub status: &'static str,  // OPEN / WIN / LOSS / BE+ / TIME
    pub mode: String, pub kind: String,
    pub exit_time_ms: Option<i64>, pub exit_price: Option<f64>,
    pub equity_after: f64,
}

#[derive(Clone)]
pub struct Pending {
    pub sig_time_ms: i64, pub side: i32,
    pub entry: f64, pub sl: f64, pub tp1: f64, pub tp2: f64,
    pub mode: String, pub kind: String, pub symbol: String,
    pub vol_scale: f64,
}

pub struct PaperSim {
    pub trades: Vec<SimTrade>,
    pub pending: Option<Pending>,
    pub equity: f64,
    pub start_equity: f64,
    pub risk_pct: f64,
    pub daily_stop_r: f64,
    pub daily_max_trades: usize,
    pub max_hold: i64,
    pub next_id: u64,
    pub vol_target: bool,
}

impl PaperSim {
    pub fn new(daily_stop_r: f64, daily_max_trades: usize) -> Self {
        PaperSim {
            trades: vec![], pending: None, equity: 10_000.0, start_equity: 10_000.0,
            risk_pct: 1.0, daily_stop_r, daily_max_trades, max_hold: 200, next_id: 1,
            vol_target: false,
        }
    }

    pub fn queue_signal(&mut self, t_ms: i64, side: i32, entry: f64, sl: f64,
                        tp1: f64, tp2: f64, mode: &str, kind: &str,
                        symbol: &str, vol_scale: f64) {
        let day = t_ms.div_euclid(86_400_000);
        if self.daily_stop_r > 0.0 {
            let day_r: f64 = self.trades.iter()
                .filter(|x| x.time_ms.div_euclid(86_400_000) == day)
                .map(|x| x.r).sum();
            if day_r <= -self.daily_stop_r { return; }
        }
        if self.daily_max_trades > 0 {
            let n = self.trades.iter()
                .filter(|x| x.time_ms.div_euclid(86_400_000) == day).count();
            if n >= self.daily_max_trades { return; }
        }
        self.pending = Some(Pending {
            sig_time_ms: t_ms, side, entry, sl, tp1, tp2,
            mode: mode.into(), kind: kind.into(), symbol: symbol.into(),
            vol_scale,
        });
    }

    /// Proses satu candle CLOSED; kembalikan log console.
    pub fn on_candle(&mut self, c: &Candle) -> Vec<String> {
        let mut events = vec![];

        // fill pending di OPEN candle BERIKUTNYA
        let mut pending = self.pending.take();
        if let Some(p) = pending.as_mut() {
            if c.time_ms > p.sig_time_ms {
                let shift = c.open - p.entry;
                let risk0 = (p.entry - p.sl).abs().max(1e-9);
                let eff = if self.vol_target { self.risk_pct * p.vol_scale } else { self.risk_pct };
                self.trades.push(SimTrade {
                    id: self.next_id, time_ms: c.time_ms,
                    symbol: p.symbol.clone(),
                    side: if p.side == 1 { "LONG" } else { "SHORT" },
                    entry: c.open, sl: p.sl + shift, tp1: p.tp1 + shift,
                    tp2: p.tp2 + shift, risk0, eff_risk: eff, r: 0.0, bars: 0,
                    hit1: false, status: "OPEN", mode: p.mode.clone(),
                    kind: p.kind.clone(), exit_time_ms: None, exit_price: None,
                    equity_after: self.equity,
                });
                self.next_id += 1;
                events.push(format!("[sim][{}] fill {} @{}", p.symbol, p.kind, c.open));
            } else {
                self.pending = Some(p.clone());
            }
        }
        let _ = &mut pending;

        // kelola posisi terbuka
        for t in self.trades.iter_mut().filter(|t| t.status == "OPEN") {
            t.bars += 1;
            let long = t.side == "LONG";
            let mut closed = false;

            // SL-first (konservatif)
            let hit_sl = if long { c.low <= t.sl } else { c.high >= t.sl };
            if hit_sl {
                let part = if t.hit1 { 0.5 } else { 1.0 };
                let dir = if long { 1.0 } else { -1.0 };
                t.r += part * dir * (t.sl - t.entry) / t.risk0;
                t.status = if t.hit1 { "BE+" } else { "LOSS" };
                t.exit_time_ms = Some(c.time_ms);
                t.exit_price = Some(t.sl);
                closed = true;
            } else {
                // TP1: 50% realized, stop pindah ke breakeven
                if !t.hit1 && (if long { c.high >= t.tp1 } else { c.low <= t.tp1 }) {
                    let dir = if long { 1.0 } else { -1.0 };
                    t.r += 0.5 * dir * (t.tp1 - t.entry) / t.risk0;
                    t.hit1 = true;
                    t.sl = t.entry; // breakeven
                }
                if t.hit1 && (if long { c.high >= t.tp2 } else { c.low <= t.tp2 }) {
                    let dir = if long { 1.0 } else { -1.0 };
                    t.r += 0.5 * dir * (t.tp2 - t.entry) / t.risk0;
                    t.status = "WIN";
                    t.exit_time_ms = Some(c.time_ms);
                    t.exit_price = Some(t.tp2);
                    closed = true;
                } else if t.bars >= self.max_hold {
                    let part = if t.hit1 { 0.5 } else { 1.0 };
                    let dir = if long { 1.0 } else { -1.0 };
                    t.r += part * dir * (c.open - t.entry) / t.risk0;
                    t.status = "TIME";
                    t.exit_time_ms = Some(c.time_ms);
                    t.exit_price = Some(c.open);
                    closed = true;
                }
            }
            if closed {
                self.equity *= 1.0 + t.eff_risk / 100.0 * t.r;
                t.equity_after = self.equity;
                events.push(format!("[sim][{}] {} {} R={:.2} (EQ {:.2})",
                    t.symbol, t.side, t.status, t.r, self.equity));
            }
        }
        events
    }

    pub fn stats(&self) -> serde_json::Value {
        let closed: Vec<&SimTrade> = self.trades.iter().filter(|t| t.status != "OPEN").collect();
        let open = self.trades.len() - closed.len();
        let wins = closed.iter().filter(|t| t.r > 0.0).count();
        let losses = closed.iter().filter(|t| t.r <= 0.0).count();
        let wr = if closed.is_empty() { 0.0 } else { wins as f64 / closed.len() as f64 * 100.0 };
        let avg_r = if closed.is_empty() { 0.0 } else { closed.iter().map(|t| t.r).sum::<f64>() / closed.len() as f64 };
        let gp: f64 = closed.iter().filter(|t| t.r > 0.0).map(|t| t.r).sum::<f64>();
        let gl: f64 = -closed.iter().filter(|t| t.r < 0.0).map(|t| t.r).sum::<f64>();
        let pf = if gl > 0.0 { gp / gl } else { f64::INFINITY };
        let mut peak = self.start_equity;
        let mut max_dd = 0.0f64;
        for t in &self.trades {
            peak = peak.max(t.equity_after);
            let dd = (t.equity_after / peak - 1.0) * 100.0;
            if dd < max_dd { max_dd = dd; }
        }
        let ret = (self.equity / self.start_equity - 1.0) * 100.0;
        json!({
            "equity": r2(self.equity), "trades": closed.len(), "open": open,
            "wins": wins, "losses": losses, "winrate": r1(wr),
            "avg_r": r3(avg_r),
            "profit_factor": if pf.is_infinite() { json!("∞") } else { json!(r2(pf)) },
            "max_dd": r2(max_dd), "return_pct": r2(ret),
        })
    }

    pub fn snapshot_list(&self, n: usize) -> serde_json::Value {
        let list: Vec<serde_json::Value> = self.trades.iter().rev().take(n).map(|t| {
            json!({
                "id": t.id, "time": crate::godmode::ms_to_str(t.time_ms), "symbol": t.symbol,
                "side": t.side, "entry": r2(t.entry), "sl": r2(t.sl),
                "tp1": r2(t.tp1), "tp2": r2(t.tp2),
                "risk": r2((t.entry - t.sl).abs()),
                "r": r3(t.r), "bars": t.bars, "hit1": t.hit1, "status": t.status,
                "mode": t.mode, "kind": t.kind,
                "exit_time": t.exit_time_ms.map(crate::godmode::ms_to_str),
                "exit_price": t.exit_price.map(r2),
                "equity_after": r2(t.equity_after),
            })
        }).collect();
        json!(list)
    }
}

pub fn r2(v: f64) -> f64 { (v * 100.0).round() / 100.0 }
pub fn r1(v: f64) -> f64 { (v * 10.0).round() / 10.0 }
pub fn r3(v: f64) -> f64 { (v * 1000.0).round() / 1000.0 }

impl PaperSim {
    /// (gp, gl, wins, losses, trades) utk penggabungan antar simbol.
    pub fn gp_gl(&self) -> (f64, f64, usize, usize, usize) {
        let closed: Vec<&SimTrade> = self.trades.iter().filter(|t| t.status != "OPEN").collect();
        let gp: f64 = closed.iter().filter(|t| t.r > 0.0).map(|t| t.r).sum();
        let gl: f64 = -closed.iter().filter(|t| t.r < 0.0).map(|t| t.r).sum::<f64>();
        let wins = closed.iter().filter(|t| t.r > 0.0).count();
        let losses = closed.iter().filter(|t| t.r <= 0.0).count();
        (gp, gl, wins, losses, closed.len())
    }
}
