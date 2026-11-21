//! Port setia backend.confluence_engine (5-gate) — semantik persis Python.

use crate::indicators::*;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug)]
pub struct Candle {
    pub time_ms: i64, // epoch ms (open time)
    pub open: f64, pub high: f64, pub low: f64, pub close: f64, pub volume: f64,
}

#[derive(Clone, Debug, Deserialize)]
pub struct Config {
    pub htf: String,
    pub rsi_len: usize,
    pub rsi_pull_long: f64,
    pub rsi_pull_short: f64,
    pub macd_fast: usize,
    pub macd_slow: usize,
    pub macd_sig: usize,
    pub atr_len: usize,
    pub sl_atr_min: f64,
    pub sl_atr_max: f64,
    pub sl_buffer_atr: f64,
    pub tp1_rr: f64,
    pub tp2_rr: f64,
    pub confirm_window: usize,
    pub vp_lookback: usize,
    pub vp_bins: usize,
    pub vp_value_area: f64,
    pub vp_prox_atr: f64,
    pub pivot_len: usize,
    pub sweep_lookback: usize,
    pub atr_pct_low: f64,
    pub atr_pct_high: f64,
    pub min_confluence: usize,
    pub gate_trend: bool,
    pub allow_long: bool,
    pub allow_short: bool,
    pub session_utc: Option<(usize, usize)>,
    pub adx_min: f64,
    pub fib_gate: bool,
    pub fib_lookback: usize,
    pub round_gate: bool,
    pub round_prox_atr: f64,
    pub round_entry_gate: bool,
    pub atr_rank_window: usize,
    pub cooldown: usize,
    pub risk_pct: f64,
}

impl Default for Config {
    fn default() -> Self {
        Config {
            htf: "4h".into(), rsi_len: 14, rsi_pull_long: 40.0, rsi_pull_short: 60.0,
            macd_fast: 12, macd_slow: 26, macd_sig: 9, atr_len: 14,
            sl_atr_min: 1.0, sl_atr_max: 2.5, sl_buffer_atr: 0.5,
            tp1_rr: 1.5, tp2_rr: 3.0, confirm_window: 3,
            vp_lookback: 200, vp_bins: 48, vp_value_area: 0.70, vp_prox_atr: 0.75,
            pivot_len: 5, sweep_lookback: 20, atr_pct_low: 0.20, atr_pct_high: 0.95,
            min_confluence: 4, gate_trend: true,
            allow_long: true, allow_short: true, session_utc: None, adx_min: 0.0,
            fib_gate: false, fib_lookback: 120,
            round_gate: false, round_prox_atr: 0.25, round_entry_gate: true,
            atr_rank_window: 500, cooldown: 10, risk_pct: 1.0,
        }
    }
}

impl Config {
    pub fn from_json(v: &serde_json::Value) -> Config {
        let mut c = Config::default();
        macro_rules! set {
            ($k:expr, $f:expr) => { if let Some(x) = v.get($k) { $f(x) } };
        }
        set!("htf", |x: &serde_json::Value| c.htf = x.as_str().unwrap_or("4h").into());
        set!("rsi_len", |x: &serde_json::Value| c.rsi_len = x.as_u64().unwrap() as usize);
        set!("rsi_pull_long", |x: &serde_json::Value| c.rsi_pull_long = x.as_f64().unwrap());
        set!("rsi_pull_short", |x: &serde_json::Value| c.rsi_pull_short = x.as_f64().unwrap());
        set!("macd_fast", |x: &serde_json::Value| c.macd_fast = x.as_u64().unwrap() as usize);
        set!("macd_slow", |x: &serde_json::Value| c.macd_slow = x.as_u64().unwrap() as usize);
        set!("macd_sig", |x: &serde_json::Value| c.macd_sig = x.as_u64().unwrap() as usize);
        set!("atr_len", |x: &serde_json::Value| c.atr_len = x.as_u64().unwrap() as usize);
        set!("sl_atr_min", |x: &serde_json::Value| c.sl_atr_min = x.as_f64().unwrap());
        set!("sl_atr_max", |x: &serde_json::Value| c.sl_atr_max = x.as_f64().unwrap());
        set!("sl_buffer_atr", |x: &serde_json::Value| c.sl_buffer_atr = x.as_f64().unwrap());
        set!("tp1_rr", |x: &serde_json::Value| c.tp1_rr = x.as_f64().unwrap());
        set!("tp2_rr", |x: &serde_json::Value| c.tp2_rr = x.as_f64().unwrap());
        set!("confirm_window", |x: &serde_json::Value| c.confirm_window = x.as_u64().unwrap() as usize);
        set!("vp_lookback", |x: &serde_json::Value| c.vp_lookback = x.as_u64().unwrap() as usize);
        set!("vp_bins", |x: &serde_json::Value| c.vp_bins = x.as_u64().unwrap() as usize);
        set!("vp_value_area", |x: &serde_json::Value| c.vp_value_area = x.as_f64().unwrap());
        set!("vp_prox_atr", |x: &serde_json::Value| c.vp_prox_atr = x.as_f64().unwrap());
        set!("pivot_len", |x: &serde_json::Value| c.pivot_len = x.as_u64().unwrap() as usize);
        set!("sweep_lookback", |x: &serde_json::Value| c.sweep_lookback = x.as_u64().unwrap() as usize);
        set!("atr_pct_low", |x: &serde_json::Value| c.atr_pct_low = x.as_f64().unwrap());
        set!("atr_pct_high", |x: &serde_json::Value| c.atr_pct_high = x.as_f64().unwrap());
        set!("min_confluence", |x: &serde_json::Value| c.min_confluence = x.as_u64().unwrap() as usize);
        set!("gate_trend", |x: &serde_json::Value| c.gate_trend = x.as_bool().unwrap());
        set!("allow_long", |x: &serde_json::Value| c.allow_long = x.as_bool().unwrap());
        set!("allow_short", |x: &serde_json::Value| c.allow_short = x.as_bool().unwrap());
        set!("session_utc", |x: &serde_json::Value| {
            if let Some(a) = x.as_array() {
                if a.len() == 2 {
                    c.session_utc = Some((a[0].as_u64().unwrap_or(0) as usize,
                                          a[1].as_u64().unwrap_or(24) as usize));
                }
            }
        });
        set!("adx_min", |x: &serde_json::Value| c.adx_min = x.as_f64().unwrap());
        set!("fib_gate", |x: &serde_json::Value| c.fib_gate = x.as_bool().unwrap());
        set!("fib_lookback", |x: &serde_json::Value| c.fib_lookback = x.as_u64().unwrap() as usize);
        set!("round_gate", |x: &serde_json::Value| c.round_gate = x.as_bool().unwrap());
        set!("round_prox_atr", |x: &serde_json::Value| c.round_prox_atr = x.as_f64().unwrap());
        set!("round_entry_gate", |x: &serde_json::Value| c.round_entry_gate = x.as_bool().unwrap());
        set!("atr_rank_window", |x: &serde_json::Value| c.atr_rank_window = x.as_u64().unwrap() as usize);
        set!("cooldown", |x: &serde_json::Value| c.cooldown = x.as_u64().unwrap() as usize);
        set!("risk_pct", |x: &serde_json::Value| c.risk_pct = x.as_f64().unwrap());
        c
    }

    pub fn to_json(&self) -> serde_json::Value {
        serde_json::json!({
            "htf": self.htf, "rsi_len": self.rsi_len,
            "rsi_pull_long": self.rsi_pull_long, "rsi_pull_short": self.rsi_pull_short,
            "macd_fast": self.macd_fast, "macd_slow": self.macd_slow,
            "macd_sig": self.macd_sig, "atr_len": self.atr_len,
            "sl_atr_min": self.sl_atr_min, "sl_atr_max": self.sl_atr_max,
            "sl_buffer_atr": self.sl_buffer_atr, "tp1_rr": self.tp1_rr,
            "tp2_rr": self.tp2_rr, "confirm_window": self.confirm_window,
            "vp_lookback": self.vp_lookback, "vp_bins": self.vp_bins,
            "vp_value_area": self.vp_value_area, "vp_prox_atr": self.vp_prox_atr,
            "pivot_len": self.pivot_len, "sweep_lookback": self.sweep_lookback,
            "atr_pct_low": self.atr_pct_low, "atr_pct_high": self.atr_pct_high,
            "min_confluence": self.min_confluence, "gate_trend": self.gate_trend,
            "allow_long": self.allow_long, "allow_short": self.allow_short,
            "session_utc": self.session_utc.map(|(a, b)| vec![a as u64, b as u64]),
            "adx_min": self.adx_min, "fib_gate": self.fib_gate,
            "fib_lookback": self.fib_lookback, "round_gate": self.round_gate,
            "round_prox_atr": self.round_prox_atr, "round_entry_gate": self.round_entry_gate,
            "atr_rank_window": self.atr_rank_window, "cooldown": self.cooldown,
            "risk_pct": self.risk_pct,
        })
    }
}

// ----------------------------- Preset mode (ala GODMODE) ---------------------
const SENSITIVITY_PRESETS: &[(&str, &str)] = &[
    ("golden", r#"{"min_confluence":4,"confirm_window":3,"cooldown":10,"rsi_pull_long":40,"rsi_pull_short":60,"vp_prox_atr":0.75,"atr_pct_low":0.20,"atr_pct_high":0.95,"gate_trend":true}"#),
    ("aggressive", r#"{"min_confluence":1,"confirm_window":1,"cooldown":5,"rsi_pull_long":50,"rsi_pull_short":50,"atr_pct_low":0.05,"gate_trend":true}"#),
    ("standard", r#"{"min_confluence":3,"confirm_window":2,"cooldown":8,"rsi_pull_long":40,"rsi_pull_short":60,"gate_trend":true}"#),
    ("precision", r#"{"min_confluence":4,"confirm_window":4,"cooldown":20,"vp_prox_atr":0.5,"atr_pct_low":0.10,"atr_pct_high":0.90,"rsi_pull_long":35,"rsi_pull_short":65,"gate_trend":true}"#),
    ("pingpong", r#"{"min_confluence":3,"confirm_window":2,"cooldown":6,"gate_trend":false,"rsi_pull_long":30,"rsi_pull_short":70,"vp_prox_atr":1.0,"atr_pct_low":0.05}"#),
    ("edge1h", r#"{"min_confluence":1,"confirm_window":2,"cooldown":8,"gate_trend":true,"session_utc":[7,21],"adx_min":20.0,"sl_atr_min":1.96,"sl_atr_max":3.10,"tp1_rr":2.34,"tp2_rr":3.68,"rsi_pull_long":46.0,"rsi_pull_short":69.8,"vp_prox_atr":0.44}"#),
];
const EXIT_PRESETS: &[(&str, &str)] = &[
    ("engine", "{}"),
    ("quick", r#"{"tp1_rr":1.5,"tp2_rr":3.0,"sl_atr_max":2.5,"sl_atr_min":1.0}"#),
    ("nitro", r#"{"tp1_rr":1.0,"tp2_rr":1.5,"sl_atr_max":2.0,"sl_atr_min":0.8}"#),
    ("hold", r#"{"tp1_rr":2.0,"tp2_rr":4.0,"sl_atr_max":3.0,"sl_atr_min":1.2,"cooldown":30}"#),
];

pub fn valid_sensitivity(name: &str) -> bool {
    SENSITIVITY_PRESETS.iter().any(|(k, _)| *k == name)
}
pub fn valid_exit(name: &str) -> bool {
    EXIT_PRESETS.iter().any(|(k, _)| *k == name)
}

/// apply_mode ala Python: semua kunci preset di-RESET ke default Config dulu.
pub fn apply_mode(base: &Config, sensitivity: Option<&str>, exit_protocol: Option<&str>) -> Config {
    let mut patch = serde_json::Map::new();
    for (name, table) in [(sensitivity, SENSITIVITY_PRESETS), (exit_protocol, EXIT_PRESETS)] {
        if let Some(s) = name {
            if let Some((_, body)) = table.iter().find(|(k, _)| *k == s) {
                let v: serde_json::Value = serde_json::from_str(body).unwrap();
                if let Some(m) = v.as_object() {
                    for (k, val) in m { patch.insert(k.clone(), val.clone()); }
                }
            }
        }
    }
    let mut obj = base.to_json().as_object().cloned().unwrap();
    // reset kunci yang dikelola preset ke default
    let defaults = Config::default().to_json();
    let d = defaults.as_object().unwrap();
    for k in patch.keys() {
        if let Some(v) = d.get(k) { obj.insert(k.clone(), v.clone()); }
    }
    for (k, v) in patch { obj.insert(k, v); }
    Config::from_json(&serde_json::Value::Object(obj))
}

#[derive(Debug, Clone, Serialize)]
pub struct Signal {
    pub time_ms: i64,
    pub dir: i32,
    pub entry: f64,
    pub sl: f64,
    pub tp1: f64,
    pub tp2: f64,
    pub score: i64,
}

fn recent(mask: &[bool], w: usize) -> Vec<bool> {
    let n = mask.len();
    let mut out = vec![false; n];
    let mut cnt = 0usize;
    for i in 0..n {
        if i >= w && mask[i - w] { cnt -= 1; }
        if mask[i] { cnt += 1; }
        out[i] = cnt > 0;
    }
    out
}
fn orv(a: &[bool], b: &[bool]) -> Vec<bool> {
    a.iter().zip(b.iter()).map(|(x, y)| *x || *y).collect()
}
fn andv(a: &[bool], b: &[bool]) -> Vec<bool> {
    a.iter().zip(b.iter()).map(|(x, y)| *x && *y).collect()
}
fn median(x: &[f64]) -> f64 {
    let mut v: Vec<f64> = x.iter().cloned().filter(|f| f.is_finite()).collect();
    if v.is_empty() { return 0.0; }
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    v[v.len() / 2]
}
fn hour_utc(ms: i64) -> usize {
    (((ms / 3_600_000) % 24) + 24) as usize % 24
}
fn round_major(ref_price: f64) -> f64 {
    if ref_price >= 500.0 { 100.0 } else if ref_price >= 50.0 { 10.0 } else { 1.0 }
}

/// generate_signals — input candle CLOSED entry-TF + HTF closed (native).
pub fn generate_signals(df: &[Candle], cfg: &Config, htf: &[Candle]) -> Vec<Signal> {
    generate_signals_full(df, cfg, htf).0
}

/// Versi lengkap: (sinyal, long_ok pasca-filter, short_ok pasca-filter)
/// untuk validasi bar-per-bar terhadap Python.
pub fn generate_signals_full(df: &[Candle], cfg: &Config, htf: &[Candle])
    -> (Vec<Signal>, Vec<bool>, Vec<bool>)
{
    let n = df.len();
    if n == 0 { return (vec![], vec![], vec![]); }
    let o: Vec<f64> = df.iter().map(|c| c.open).collect();
    let h: Vec<f64> = df.iter().map(|c| c.high).collect();
    let l: Vec<f64> = df.iter().map(|c| c.low).collect();
    let c: Vec<f64> = df.iter().map(|c| c.close).collect();
    let v: Vec<f64> = df.iter().map(|x| x.volume).collect();
    let times: Vec<i64> = df.iter().map(|x| x.time_ms).collect();

    let ema9 = ema(&c, 9);
    let ema20 = ema(&c, 20);
    let ema50 = ema(&c, 50);
    let ema200 = ema(&c, 200);
    let rsi_s = rsi(&c, cfg.rsi_len);
    let (macd_line, macd_sig, hist) = macd(&c, cfg.macd_fast, cfg.macd_slow, cfg.macd_sig);
    let atr_s = atr(&h, &l, &c, cfg.atr_len);
    let (poc, val, vah) =
        volume_profile_levels(&c, &v, cfg.vp_lookback, cfg.vp_bins, cfg.vp_value_area);
    let (bull_div, bear_div) = divergence(&h, &l, &rsi_s, cfg.pivot_len);
    let pa = price_action(&o, &h, &l, &c, cfg.sweep_lookback);

    // HTF trend: +1/-1/0 dari EMA50 vs EMA200 HTF; bar yang dipakai = bar
    // HTF SEBELUM bar berjalan (Python: shift(1) + reindex ffill)
    let hc: Vec<f64> = htf.iter().map(|x| x.close).collect();
    let he50 = ema(&hc, 50);
    let he200 = ema(&hc, 200);
    let mut htrend = vec![0i32; htf.len()];
    for i in 1..htf.len() {
        if he50[i].is_nan() || he200[i].is_nan() { continue; }
        let d200 = he200[i] - he200[i - 1];
        if hc[i] > he50[i] && he50[i] > he200[i] && d200 > 0.0 { htrend[i] = 1; }
        if hc[i] < he50[i] && he50[i] < he200[i] && d200 < 0.0 { htrend[i] = -1; }
    }
    let mut trend = vec![0i32; n];
    for i in 0..n {
        // idx-1 = bar HTF yang MEMUAT times[i]; Python memakai shift(1) =
        // trend bar SEBELUMNYA (sudah close saat bar entry terbentuk)
        let idx = htf.iter().position(|x| x.time_ms > times[i]).unwrap_or(htf.len());
        if idx >= 2 { trend[i] = htrend[idx - 2]; }
    }

    // 1) TREND
    let mut g_trend_l = vec![false; n];
    let mut g_trend_s = vec![false; n];
    for i in 0..n {
        let stack_l = ema9[i] > ema20[i] && c[i] > ema50[i];
        let stack_s = ema9[i] < ema20[i] && c[i] < ema50[i];
        g_trend_l[i] = trend[i] == 1 && stack_l;
        g_trend_s[i] = trend[i] == -1 && stack_s;
    }

    // 2) MOMENTUM
    let w = cfg.confirm_window;
    let mut rsi_rec_l_raw = vec![false; n];
    let mut rsi_rec_s_raw = vec![false; n];
    for i in 1..n {
        rsi_rec_l_raw[i] = rsi_s[i - 1] < cfg.rsi_pull_long && rsi_s[i] >= cfg.rsi_pull_long;
        rsi_rec_s_raw[i] = rsi_s[i - 1] > cfg.rsi_pull_short && rsi_s[i] <= cfg.rsi_pull_short;
    }
    let rsi_rec_l = recent(&orv(&rsi_rec_l_raw, &bull_div), w);
    let rsi_rec_s = recent(&orv(&rsi_rec_s_raw, &bear_div), w);
    let mut macd_cross_up = vec![false; n];
    let mut macd_cross_dn = vec![false; n];
    let mut hist_up = vec![false; n];
    let mut hist_dn = vec![false; n];
    for i in 1..n {
        macd_cross_up[i] = macd_line[i] > macd_sig[i] && macd_line[i - 1] <= macd_sig[i - 1];
        macd_cross_dn[i] = macd_line[i] < macd_sig[i] && macd_line[i - 1] >= macd_sig[i - 1];
        hist_up[i] = hist[i] > 0.0 && hist[i] > hist[i - 1];
        hist_dn[i] = hist[i] < 0.0 && hist[i] < hist[i - 1];
    }
    // Python: recent(cross, w) | (hist<0 & hist turun SEKARANG) — syarat hist
    // TIDAK ikut recent (beda semantik, krusial utk kesetiaan)
    let macd_l = orv(&recent(&macd_cross_up, w), &hist_up);
    let macd_s = orv(&recent(&macd_cross_dn, w), &hist_dn);
    let g_mom_l: Vec<bool> = andv(&rsi_rec_l, &macd_l);
    let g_mom_s: Vec<bool> = andv(&rsi_rec_s, &macd_s);

    // 3) VP
    let mut g_vp_l = vec![false; n];
    let mut g_vp_s = vec![false; n];
    for i in 0..n {
        let prox = cfg.vp_prox_atr * atr_s[i];
        let near_sup = (c[i] - poc[i]).abs() <= prox || (c[i] - val[i]).abs() <= prox;
        let near_res = (c[i] - poc[i]).abs() <= prox || (c[i] - vah[i]).abs() <= prox;
        g_vp_l[i] = near_sup && c[i] >= val[i] - prox;
        g_vp_s[i] = near_res && c[i] <= vah[i] + prox;
    }

    // 4) PA
    let g_pa_l = pa.bull.clone();
    let g_pa_s = pa.bear.clone();

    // 5) VOLATILITY
    let atr_rank = rolling_rank_pct(&atr_s, cfg.atr_rank_window, 100);
    let mut g_vol = vec![false; n];
    for i in 0..n {
        g_vol[i] = atr_rank[i] >= cfg.atr_pct_low && atr_rank[i] <= cfg.atr_pct_high;
    }

    // gabungan gate
    let mut long_ok = vec![false; n];
    let mut short_ok = vec![false; n];
    for i in 0..n {
        let n_l = [g_mom_l[i], g_vp_l[i], g_pa_l[i], g_vol[i]].iter().filter(|&&x| x).count();
        let n_s = [g_mom_s[i], g_vp_s[i], g_pa_s[i], g_vol[i]].iter().filter(|&&x| x).count();
        if cfg.gate_trend {
            long_ok[i] = g_trend_l[i] && n_l >= cfg.min_confluence;
            short_ok[i] = g_trend_s[i] && n_s >= cfg.min_confluence;
        } else {
            long_ok[i] = n_l >= cfg.min_confluence;
            short_ok[i] = n_s >= cfg.min_confluence;
        }
    }

    // filter desain
    let adx_s = if cfg.adx_min > 0.0 { adx_wilder(&h, &l, &c, 14) } else { vec![] };
    for i in 0..n {
        if !cfg.allow_long { long_ok[i] = false; }
        if !cfg.allow_short { short_ok[i] = false; }
        if let Some((h0, h1)) = cfg.session_utc {
            let hh = hour_utc(times[i]);
            let in_sess = hh >= h0 && hh < h1;
            long_ok[i] &= in_sess;
            short_ok[i] &= in_sess;
        }
        if cfg.adx_min > 0.0 {
            long_ok[i] &= adx_s[i] >= cfg.adx_min;
            short_ok[i] &= adx_s[i] >= cfg.adx_min;
        }
        if cfg.fib_gate {
            let look = cfg.fib_lookback;
            if i + 1 >= look {
                let rh = h[i + 1 - look..=i].iter().cloned().fold(f64::NEG_INFINITY, f64::max);
                let rl = l[i + 1 - look..=i].iter().cloned().fold(f64::INFINITY, f64::min);
                let rng = rh - rl;
                if rng > 0.0 {
                    let eq = rl + 0.5 * rng;
                    long_ok[i] &= c[i] <= eq;
                    short_ok[i] &= c[i] >= eq;
                }
            }
        }
        if cfg.round_gate {
            let major = round_major(median(&c));
            let rprox = cfg.round_prox_atr * atr_s[i];
            if cfg.round_entry_gate {
                let lvl_above = (c[i] / major).ceil() * major;
                let lvl_below = (c[i] / major).floor() * major;
                long_ok[i] &= !(lvl_above > c[i] && lvl_above - c[i] <= rprox);
                short_ok[i] &= !(c[i] > lvl_below && c[i] - lvl_below <= rprox);
            }
        }
    }

    // SL/TP geometry
    let buf: Vec<f64> = atr_s.iter().map(|a| cfg.sl_buffer_atr * a).collect();
    let min_dist: Vec<f64> = atr_s.iter().map(|a| cfg.sl_atr_min * a).collect();
    let mut sw_low = vec![f64::NAN; n];
    let mut sw_high = vec![f64::NAN; n];
    // Python: rolling(5) TANPA shift — window 5 bar TERMASUK bar sekarang
    for i in 4..n {
        sw_low[i] = l[i - 4..=i].iter().cloned().fold(f64::INFINITY, f64::min);
        sw_high[i] = h[i - 4..=i].iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    }
    for i in 0..n {
        if long_ok[i] {
            let raw = c[i] - (sw_low[i] - buf[i]);
            long_ok[i] &= raw.is_finite() && raw >= min_dist[i];
        }
        if short_ok[i] {
            let raw = (sw_high[i] + buf[i]) - c[i];
            short_ok[i] &= raw.is_finite() && raw >= min_dist[i];
        }
    }

    // cooldown
    let mut sig = vec![0i32; n];
    let mut last: i64 = -10_000_000_000;
    for i in 0..n {
        if long_ok[i] { sig[i] = 1; } else if short_ok[i] { sig[i] = -1; }
        if sig[i] != 0 {
            if (i as i64) - last < cfg.cooldown as i64 { sig[i] = 0; } else { last = i as i64; }
        }
    }

    // skor kualitas (sw_bull/sw_bear = sweep saja, bukan PA gabungan)
    let bull_div_rec = recent(&bull_div, w);
    let bear_div_rec = recent(&bear_div, w);

    let major_round = round_major(median(&c));
    let mut out = Vec::new();
    for i in 0..n {
        if sig[i] == 0 { continue; }
        let long = sig[i] == 1;
        let raw = if long { c[i] - (sw_low[i] - buf[i]) } else { (sw_high[i] + buf[i]) - c[i] };
        let dist = raw.clamp(min_dist[i], cfg.sl_atr_max * atr_s[i]);
        let entry = c[i];
        let mut sl = if long { entry - dist } else { entry + dist };
        let tp1 = if long { entry + cfg.tp1_rr * dist } else { entry - cfg.tp1_rr * dist };
        let mut tp2 = if long { entry + cfg.tp2_rr * dist } else { entry - cfg.tp2_rr * dist };
        if cfg.round_gate {
            let rprox = cfg.round_prox_atr * atr_s[i];
            let rbuf = cfg.sl_buffer_atr * atr_s[i];
            if long {
                let lvl_lo = (sl / major_round).floor() * major_round;
                let lvl_hi = lvl_lo + major_round;
                let lvl = if sl - lvl_lo <= rprox { Some(lvl_lo) }
                    else if lvl_hi - sl <= rprox { Some(lvl_hi) } else { None };
                if let Some(lv) = lvl {
                    if lv < entry { sl = sl.min(lv - rbuf); }
                }
                let rl = (tp2 / major_round).floor() * major_round;
                if rl > entry && tp2 - rl <= rprox && rl - rbuf > entry { tp2 = rl - rbuf; }
            } else {
                let lvl_hi = (sl / major_round).ceil() * major_round;
                let lvl_lo = lvl_hi - major_round;
                let lvl = if lvl_hi - sl <= rprox { Some(lvl_hi) }
                    else if sl - lvl_lo <= rprox { Some(lvl_lo) } else { None };
                if let Some(lv) = lvl {
                    if lv > entry { sl = sl.max(lv + rbuf); }
                }
                let rl = (tp2 / major_round).ceil() * major_round;
                if rl < entry && rl - tp2 <= rprox && rl + rbuf < entry { tp2 = rl + rbuf; }
            }
        }
        let score = if long {
            (pa.sweep_bull[i] as i64) + (bull_div_rec[i] as i64)
                + (c[i] > poc[i]) as i64 + (rsi_s[i] > 50.0) as i64
        } else {
            (pa.sweep_bear[i] as i64) + (bear_div_rec[i] as i64)
                + (c[i] < poc[i]) as i64 + (rsi_s[i] < 50.0) as i64
        };
        out.push(Signal { time_ms: times[i], dir: sig[i], entry, sl, tp1, tp2, score });
    }
    (out, long_ok, short_ok)
}
