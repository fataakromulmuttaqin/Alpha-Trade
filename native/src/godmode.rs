//! Port backend.godmode_engine + analytics helper — panel & lapisan GODMODE.

use crate::engine::{Candle, Config};
use crate::indicators::*;
use serde_json::json;

pub fn fmt2(v: f64) -> f64 { (v * 100.0).round() / 100.0 }

pub fn closes(d: &[Candle]) -> Vec<f64> { d.iter().map(|c| c.close).collect() }

// ----------------------------- volume flow ----------------------------------
pub fn volume_flow(d: &[Candle], n: usize) -> serde_json::Value {
    let st = d.len().saturating_sub(n);
    let w = &d[st..];
    let mut buy = 0.0f64;
    let mut sell = 0.0f64;
    for c in w {
        let delta = (c.close - c.open).signum() * c.volume;
        if delta > 0.0 { buy += delta; } else { sell -= delta; }
    }
    let tot = buy + sell;
    let net = buy - sell;
    let bp = if tot > 0.0 { (buy / tot * 100.0).round() as i64 } else { 50 };
    let small = if sell < buy { "SELL" } else { "BUY" };
    let absorption = if tot > 0.0 && buy.max(sell) > 0.0
        && buy.min(sell) / buy.max(sell) > 0.4
    { format!("ABSORPTION ({small})") } else { "FLOW NORMAL".into() };
    json!({"buy": buy.round() as i64, "sell": sell.round() as i64,
           "net_cvd": net.round() as i64, "buyers_pct": bp,
           "sellers_pct": 100 - bp, "absorption": absorption})
}

// ----------------------------- zona S/D tunggal -----------------------------
fn window_idx(d: &[Candle], lookback: usize) -> usize {
    d.len().saturating_sub(lookback)
}

/// Swing terakhir terkonfirmasi (pivot L) dalam lookback bar.
fn last_swing(d: &[Candle], kind: &str, l: usize, lookback: usize) -> Option<(usize, f64, f64, f64)> {
    // returns (index_in_d, price, top, bottom)
    let st = window_idx(d, lookback);
    let arr: Vec<f64> = d[st..].iter().map(|c| if kind == "low" { c.low } else { c.high }).collect();
    if arr.len() < 2 * l + 2 { return None; }
    for p in (l..arr.len() - l - 1).rev() {
        let is_pivot = match kind {
            "low" => {
                let m = (p - l..=p + l).map(|k| arr[k]).fold(f64::INFINITY, f64::min);
                arr[p] == m
            }
            _ => {
                let m = (p - l..=p + l).map(|k| arr[k]).fold(f64::NEG_INFINITY, f64::max);
                arr[p] == m
            }
        };
        if is_pivot {
            let i = st + p;
            let row = &d[i];
            let (top, bottom) = if kind == "low" {
                (row.open.max(row.close), row.low)
            } else {
                (row.high, row.open.min(row.close))
            };
            return Some((i, arr[p], top, bottom));
        }
    }
    None
}

pub fn zones(d: &[Candle], a: f64, l: usize, lookback: usize) -> serde_json::Value {
    let c = d.last().map(|x| x.close).unwrap_or(0.0);
    let mut out = serde_json::Map::new();
    for (kind, name) in [("high", "supply"), ("low", "demand")] {
        let Some((pi, price, mut top, mut bottom)) = last_swing(d, kind, l, lookback) else { continue };
        // strength: jumlah bar berimpit yang menyamai ekstrem di window pivot
        let lo = pi.saturating_sub(l);
        let hi = (pi + l + 1).min(d.len());
        let strength = if kind == "high" {
            let mx = d[lo..hi].iter().map(|x| x.high).fold(f64::NEG_INFINITY, f64::max);
            d[lo..hi].iter().filter(|x| x.high == mx).count()
        } else {
            let mn = d[lo..hi].iter().map(|x| x.low).fold(f64::INFINITY, f64::min);
            d[lo..hi].iter().filter(|x| x.low == mn).count()
        };
        // cvd delta saat formasi
        let seg = &d[lo..hi];
        let cvd_at: f64 = seg.iter()
            .map(|x| (x.close - x.open).signum() * x.volume).sum();
        let status;
        if name == "supply" {
            bottom = bottom.max(top - 1.2 * a);
            let inside = bottom <= c && c <= top;
            let near = (c - bottom) >= 0.0 && (c - bottom) <= 0.5 * a;
            status = if inside { "PRICE IN ZONE" } else if near { "AT SUPPLY".into() } else { "ENTRY SELL".into() };
        } else {
            top = top.min(bottom + 1.2 * a);
            let inside = bottom <= c && c <= top;
            let near = (top - c) >= 0.0 && (top - c) <= 0.5 * a;
            status = if inside { "PRICE IN ZONE" } else if near { "AT DEMAND".into() } else { "ENTRY BUY".into() };
        }
        out.insert(name.to_string(), json!({
            "top": fmt2(top), "bottom": fmt2(bottom),
            "strength": format!("{strength} candles"),
            "cvd": cvd_at.round() as i64, "status": status}));
    }
    serde_json::Value::Object(out)
}

// ----------------------------- struktur -------------------------------------
pub struct StructureEvents {
    pub bos_bull: Vec<bool>, pub bos_bear: Vec<bool>,
    pub choch_bull: Vec<bool>, pub choch_bear: Vec<bool>,
}
pub fn structure_events(d: &[Candle], l: usize) -> StructureEvents {
    let n = d.len();
    let mut ev = StructureEvents {
        bos_bull: vec![false; n], bos_bear: vec![false; n],
        choch_bull: vec![false; n], choch_bear: vec![false; n],
    };
    let mut swing_hi: Option<f64> = None;
    let mut swing_lo: Option<f64> = None;
    let mut trend = 0i32;
    if n < 2 * l + 1 { return ev; }
    for i in 2 * l..n {
        let p = i - l;
        // pivot high: h[p] == max(h[p-L..p+L])
        if p >= l && p + l < n {
            let m = (p - l..=p + l).map(|k| d[k].high).fold(f64::NEG_INFINITY, f64::max);
            if d[p].high == m { swing_hi = Some(d[p].high); }
            let m = (p - l..=p + l).map(|k| d[k].low).fold(f64::INFINITY, f64::min);
            if d[p].low == m { swing_lo = Some(d[p].low); }
        }
        if let Some(sh) = swing_hi {
            if d[i].close > sh {
                if trend == -1 { ev.choch_bull[i] = true; } else { ev.bos_bull[i] = true; }
                trend = 1;
                swing_hi = None;
                continue;
            }
        }
        if let Some(sl_) = swing_lo {
            if d[i].close < sl_ {
                if trend == 1 { ev.choch_bear[i] = true; } else { ev.bos_bear[i] = true; }
                trend = -1;
                swing_lo = None;
            }
        }
    }
    ev
}

pub fn structure_label(d: &[Candle], l: usize) -> (String, String) {
    let n = d.len();
    let mut his: Vec<f64> = vec![];
    let mut los: Vec<f64> = vec![];
    if n > 2 * l + 1 {
        for p in l..n - l {
            let m = (p - l..=p + l).map(|k| d[k].high).fold(f64::NEG_INFINITY, f64::max);
            if d[p].high == m { his.push(d[p].high); }
            let m = (p - l..=p + l).map(|k| d[k].low).fold(f64::INFINITY, f64::min);
            if d[p].low == m { los.push(d[p].low); }
        }
    }
    let hh = his.len() >= 2 && his[his.len() - 1] > his[his.len() - 2];
    let hl = los.len() >= 2 && los[los.len() - 1] > los[los.len() - 2];
    let lh = his.len() >= 2 && his[his.len() - 1] < his[his.len() - 2];
    let ll = los.len() >= 2 && los[los.len() - 1] < los[los.len() - 2];
    if hh && hl { return ("BULLISH".into(), "HH + HL STRUCTURE".into()); }
    if lh && ll { return ("BEARISH".into(), "LH + LL STRUCTURE".into()); }
    if hh || hl {
        return ("BULLISH".into(), if hh { "HH + HL STRUCTURE".into() } else { "HL PENDING HH".into() });
    }
    if lh || ll {
        return ("BEARISH".into(), if ll { "LH + LL STRUCTURE".into() } else { "LH PENDING LL".into() });
    }
    ("RANGING".into(), "RANGE — TUNGGU BREAK".into())
}

pub fn structure_state(d: &[Candle], l: usize) -> (String, String) {
    let n = d.len();
    let mut his: Vec<f64> = vec![];
    let mut los: Vec<f64> = vec![];
    if n > 2 * l + 1 {
        for p in l..n - l {
            let m = (p - l..=p + l).map(|k| d[k].high).fold(f64::NEG_INFINITY, f64::max);
            if d[p].high == m { his.push(d[p].high); }
            let m = (p - l..=p + l).map(|k| d[k].low).fold(f64::INFINITY, f64::min);
            if d[p].low == m { los.push(d[p].low); }
        }
    }
    if his.len() >= 2 && los.len() >= 2 {
        let r_now = his[his.len() - 1] - los[los.len() - 1];
        let r_prev = his[his.len() - 2] - los[los.len() - 2];
        if r_now > r_prev {
            ("EXPANDING".into(), "HH + LL — VOLATILE".into())
        } else {
            ("CONTRACTING".into(), "RANGE MENGECEL — QUIET".into())
        }
    } else {
        ("WARMING UP".into(), "DATA KURANG".into())
    }
}

// ----------------------------- fib ------------------------------------------
pub fn fib_state(d: &[Candle], lookback: usize) -> serde_json::Value {
    if d.is_empty() { return serde_json::Value::Null; }
    let st = window_idx(d, lookback);
    let w = &d[st..];
    let hi = w.iter().map(|x| x.high).fold(f64::NEG_INFINITY, f64::max);
    let lo = w.iter().map(|x| x.low).fold(f64::INFINITY, f64::min);
    let rng = hi - lo;
    if rng <= 0.0 { return serde_json::Value::Null; }
    let c = d.last().unwrap().close;
    let pos = (c - lo) / rng;
    json!({
        "high": hi, "low": lo, "eq": lo + 0.5 * rng,
        "gp": hi - 0.618 * rng, "ote": hi - 0.786 * rng,
        "state": if pos < 0.5 { "DISCOUNT" } else { "PREMIUM" },
        "equilibrium": fmt2(lo + 0.5 * rng),
    })
}

// ----------------------------- zona multi S1-S3 / D1-D3 ---------------------
pub fn multi_zones(d: &[Candle], a: f64, n_levels: usize, l: usize, lookback: usize) -> serde_json::Value {
    let st = window_idx(d, lookback);
    let w = &d[st..];
    let mut highs: Vec<(i64, f64)> = vec![];
    let mut lows: Vec<(i64, f64)> = vec![];
    if w.len() > 2 * l + 1 {
        for p in l..w.len() - l {
            let m = (p - l..=p + l).map(|k| w[k].high).fold(f64::NEG_INFINITY, f64::max);
            if w[p].high == m { highs.push((w[p + l].time_ms, w[p].high)); }
            let m = (p - l..=p + l).map(|k| w[k].low).fold(f64::INFINITY, f64::min);
            if w[p].low == m { lows.push((w[p + l].time_ms, w[p].low)); }
        }
    }
    let c = d.last().map(|x| x.close).unwrap_or(0.0);
    let band = 0.5 * a;
    let mut sup: Vec<_> = highs.iter().rev().take(n_levels)
        .map(|(t, p)| json!({"top": fmt2(p + band), "bottom": fmt2(p - band), "time": *t})).collect();
    let mut dem: Vec<_> = lows.iter().rev().take(n_levels)
        .map(|(t, p)| json!({"top": fmt2(p + band), "bottom": fmt2(p - band), "time": *t})).collect();
    sup.sort_by(|x, y| y["top"].as_f64().unwrap().partial_cmp(&x["top"].as_f64().unwrap()).unwrap());
    dem.sort_by(|x, y| y["bottom"].as_f64().unwrap().partial_cmp(&x["bottom"].as_f64().unwrap()).unwrap());
    let mut sup_out = vec![];
    for (i, z) in sup.iter().enumerate() {
        let (top, bottom) = (z["top"].as_f64().unwrap(), z["bottom"].as_f64().unwrap());
        sup_out.push(json!({"level": format!("S{}", i + 1), "top": fmt2(top),
            "bottom": fmt2(bottom), "strength_pts": fmt2(top - bottom),
            "dist_pts": fmt2(bottom - c), "time": z["time"]}));
    }
    let mut dem_out = vec![];
    for (i, z) in dem.iter().enumerate() {
        let (top, bottom) = (z["top"].as_f64().unwrap(), z["bottom"].as_f64().unwrap());
        dem_out.push(json!({"level": format!("D{}", i + 1), "top": fmt2(top),
            "bottom": fmt2(bottom), "strength_pts": fmt2(top - bottom),
            "dist_pts": fmt2(c - top), "time": z["time"]}));
    }
    json!({"supply": sup_out, "demand": dem_out})
}

// ----------------------------- lamps / kondisi pasar ------------------------
pub fn trend_lamps(d: &[Candle], d_daily: Option<&[Candle]>) -> serde_json::Value {
    let c = closes(d);
    let lamp = |e: &[f64]| -> &str {
        if c.len() < 2 || e.len() < 2 { return "FLAT"; }
        let p = c[c.len() - 1];
        let slope = if e.len() > 3 { e[e.len() - 1] - e[e.len() - 3] } else { e[e.len() - 1] - e[e.len() - 2] };
        if p > e[e.len() - 1] && slope > 0.0 { "UP" }
        else if p < e[e.len() - 1] && slope < 0.0 { "DOWN" }
        else { "FLAT" }
    };
    let daily = match d_daily {
        Some(dd) if dd.len() > 60 => {
            let dc = closes(dd);
            let e = ema(&dc, 50);
            if dc[dc.len() - 1] > e[e.len() - 1] { "UP" } else { "DOWN" }
        }
        _ => "FLAT",
    };
    json!({
        "Micro (EMA 9)": lamp(&ema(&c, 9)),
        "Mini (EMA 20)": lamp(&ema(&c, 20)),
        "Macro (EMA 50)": lamp(&ema(&c, 50)),
        "Daily Trend": daily,
    })
}

pub fn market_condition(d: &[Candle], lookback: usize) -> String {
    if d.is_empty() { return "SIDEWAYS".into(); }
    let st = window_idx(d, lookback);
    let w = &d[st..];
    let hi = w.iter().map(|x| x.high).fold(f64::NEG_INFINITY, f64::max);
    let lo = w.iter().map(|x| x.low).fold(f64::INFINITY, f64::min);
    let rng = hi - lo;
    if rng <= 0.0 { return "SIDEWAYS".into(); }
    let c = closes(d);
    let adx = adx_wilder(&w.iter().map(|x| x.high).collect::<Vec<_>>(),
                         &w.iter().map(|x| x.low).collect::<Vec<_>>(),
                         &c[st..], 14);
    let last = adx.last().cloned().unwrap_or(f64::NAN);
    if last.is_nan() {
        // fallback heuristik range-vs-ATR
        let a = atr(&w.iter().map(|x| x.high).collect::<Vec<_>>(),
                    &w.iter().map(|x| x.low).collect::<Vec<_>>(),
                    &c[st..], 14).last().cloned().unwrap_or(f64::NAN);
        let pos = (c[c.len() - 1] - lo) / rng;
        let narrow = a.is_finite() && rng < 6.0 * a;
        let sideways = narrow && (0.35..=0.65).contains(&pos);
        if sideways { "SIDEWAYS".into() } else { "TRENDING".into() }
    } else if last >= 25.0 { "TRENDING".into() }
    else if last <= 18.0 { "SIDEWAYS".into() }
    else { "TRENDING".into() }
}

// ----------------------------- events GODMODE -------------------------------
pub fn events_full(d: &[Candle], l: usize, sweep_lookback: usize, max_events: usize) -> serde_json::Value {
    let n = d.len();
    let ev = structure_events(d, l);
    let mut rows: Vec<(i64, f64, String, String, String)> = vec![];
    for i in 0..n {
        if ev.choch_bull[i] {
            rows.push((d[i].time_ms, d[i].close, "CHOCH".into(),
                       "Bearish-Bullish flip — LH broken upward".into(), "UP".into()));
        }
        if ev.choch_bear[i] {
            rows.push((d[i].time_ms, d[i].close, "CHOCH".into(),
                       "Bullish-Bearish flip — HL broken downward".into(), "DOWN".into()));
        }
    }
    let mut last_hi: Option<usize> = None;
    let mut last_lo: Option<usize> = None;
    if n > 2 * l + 1 {
        for p in l..n - l {
            let conf = d[p + l].time_ms;
            let mh = (p - l..=p + l).map(|k| d[k].high).fold(f64::NEG_INFINITY, f64::max);
            if d[p].high == mh {
                if let Some(lh) = last_hi {
                    if d[p].high > d[lh].high {
                        rows.push((conf, d[p].high, "HH".into(), "Higher High — Last bullish swing high".into(), "UP".into()));
                    } else if d[p].high < d[lh].high {
                        rows.push((conf, d[p].high, "LH".into(), "Lower High — Bearish resistance level".into(), "DOWN".into()));
                    }
                }
                last_hi = Some(p);
            }
            let m = (p - l..=p + l).map(|k| d[k].low).fold(f64::INFINITY, f64::min);
            if d[p].low == m {
                if let Some(ll) = last_lo {
                    if d[p].low > d[ll].low {
                        rows.push((conf, d[p].low, "HL".into(), "Higher Low — Bullish support level".into(), "UP".into()));
                    } else if d[p].low < d[ll].low {
                        rows.push((conf, d[p].low, "LL".into(), "Lower Low — Last bearish swing low".into(), "DOWN".into()));
                    }
                }
                last_lo = Some(p);
            }
        }
    }
    if n > sweep_lookback {
        for i in (sweep_lookback + 1..n).rev() {
            let prev_lo = (i - sweep_lookback..i).map(|k| d[k].low).fold(f64::INFINITY, f64::min);
            let prev_hi = (i - sweep_lookback..i).map(|k| d[k].high).fold(f64::NEG_INFINITY, f64::max);
            if d[i].low < prev_lo && d[i].close > prev_lo {
                rows.push((d[i].time_ms, prev_lo, "SWEEP".into(),
                           format!("Liquidity grabbed below {prev_lo:.2} — bullish reversal signal"), "UP".into()));
                break;
            }
        }
        for i in (sweep_lookback + 1..n).rev() {
            let prev_hi = (i - sweep_lookback..i).map(|k| d[k].high).fold(f64::NEG_INFINITY, f64::max);
            let prev_lo = (i - sweep_lookback..i).map(|k| d[k].low).fold(f64::INFINITY, f64::min);
            if d[i].high > prev_hi && d[i].close < prev_hi {
                rows.push((d[i].time_ms, prev_hi, "SWEEP".into(),
                           format!("Liquidity grabbed above {prev_hi:.2} — bearish reversal signal"), "DOWN".into()));
                break;
            }
        }
    }
    // TC label kedua
    for i in 0..n {
        if ev.choch_bull[i] {
            rows.push((d[i].time_ms, d[i].close, "TC".into(),
                       "Bullish trend change — LL to HL structure flip".into(), "UP".into()));
        }
        if ev.choch_bear[i] {
            rows.push((d[i].time_ms, d[i].close, "TC".into(),
                       "Bearish trend change — HH to LH structure flip".into(), "DOWN".into()));
        }
    }
    rows.sort_by_key(|r| r.0);
    let tail: Vec<_> = rows.iter().rev().take(max_events).rev().collect();
    json!(tail.iter().map(|(t, p, tag, desc, dir)| json!({
        "time": ms_to_str(*t), "tag": tag, "price": fmt2(*p), "desc": desc, "dir": dir
    })).collect::<Vec<_>>())
}

pub fn ms_to_str(ms: i64) -> String {
    // format mirip str(Timestamp pandas): "YYYY-MM-DD HH:MM:SS+00:00"
    let secs = ms.div_euclid(1000);
    let days = secs.div_euclid(86400);
    let sod = secs.rem_euclid(86400);
    let (h, m, s) = (sod / 3600, (sod % 3600) / 60, sod % 60);
    // civil date dari days sejak epoch (Howard Hinnant algorithm)
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let dd = doy - (153 * mp + 2) / 5 + 1;
    let mm = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if mm <= 2 { y + 1 } else { y };
    format!("{y:04}-{mm:02}-{dd:02} {h:02}:{m:02}:{s:02}+00:00")
}

// ----------------------------- confluence GODMODE ---------------------------
pub fn heatmap_bias(d: &[Candle]) -> String {
    let c = closes(d);
    if c.len() < 50 { return "RANG".into(); }
    let (e9, e20, e50) = (ema(&c, 9), ema(&c, 20), ema(&c, 50));
    let i = c.len() - 1;
    if e9[i] > e20[i] && e20[i] > e50[i] { "BULL".into() }
    else if e9[i] < e20[i] && e20[i] < e50[i] { "BEAR".into() }
    else { "RANG".into() }
}

pub fn confluence_godmode(
    d: &[Candle], htf: &[Candle], cfg: &Config, zm: &serde_json::Value,
    vol_flow: &serde_json::Value, htf_label: &str,
) -> serde_json::Value {
    let n = d.len();
    // htf trend bar terakhir (shift 1 dihitung dari sinyal engine helper):
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
    // bar HTF terakhir yang closed sebelum bar terakhir entry:
    let last_open = d[n - 1].time_ms;
    let idx = htf.iter().position(|x| x.time_ms > last_open).unwrap_or(htf.len());
    let htf_dir = if idx >= 2 { htrend[idx - 1] } else { 0 };
    let c = d[n - 1].close;

    let a = atr(&d.iter().map(|x| x.high).collect::<Vec<_>>(),
                &d.iter().map(|x| x.low).collect::<Vec<_>>(),
                &closes(d), cfg.atr_len).last().cloned().unwrap_or(0.0);
    let in_zone = |side: &str| -> bool {
        zm[side].as_array().map(|arr| arr.iter().any(|z| {
            let (b, t) = (z["bottom"].as_f64().unwrap_or(0.0), z["top"].as_f64().unwrap_or(0.0));
            b <= c && c <= t
        })).unwrap_or(false)
    };
    let near_zone = |side: &str| -> bool {
        zm[side].as_array().map(|arr| arr.iter().any(|z| {
            let dp = z["dist_pts"].as_f64().unwrap_or(1e9);
            -0.6 * a <= dp && dp <= 0.5 * a
        })).unwrap_or(false)
    };
    let at_supply = in_zone("supply");
    let at_demand = in_zone("demand");
    let near_supply = near_zone("supply");
    let near_demand = near_zone("demand");
    let strong_zone = ["supply", "demand"].iter().any(|side| {
        zm[side].as_array().map(|arr| arr.iter().any(|z| {
            z["strength_pts"].as_f64().unwrap_or(0.0) > a
        })).unwrap_or(false)
    });
    let zone_pts = if strong_zone && (at_supply || at_demand || near_supply || near_demand) { 30 }
        else if at_supply || at_demand || near_supply || near_demand { 20 } else { 0 };

    let look = cfg.sweep_lookback + 2;
    let st = n.saturating_sub(look);
    let w = &d[st..];
    let (mut p_lo, mut p_hi) = (f64::INFINITY, f64::NEG_INFINITY);
    if n > cfg.sweep_lookback {
        p_lo = (n - 1 - cfg.sweep_lookback..n - 1).map(|k| d[k].low).fold(f64::INFINITY, f64::min);
        p_hi = (n - 1 - cfg.sweep_lookback..n - 1).map(|k| d[k].high).fold(f64::NEG_INFINITY, f64::max);
    }
    let sweep_bull = w.iter().any(|x| x.low < p_lo);
    let sweep_bear = w.iter().any(|x| x.high > p_hi);
    let sweep_ok = sweep_bull || sweep_bear;

    let (o, cl) = (d[n - 1].open, d[n - 1].close);
    let (po, pcl) = (d[n - 2].open, d[n - 2].close);
    let two_bull = cl > o && pcl < po && cl >= po;
    let two_bear = cl < o && pcl > po && cl <= po;
    let two_ok = two_bull || two_bear;

    let checks = json!([
        {"label": format!("HTF {} Trend", htf_label.to_uppercase()),
         "val": if htf_dir == 1 { "BULLISH" } else if htf_dir == -1 { "BEARISH" } else { "RANGING" },
         "ok": htf_dir != 0, "pts": if htf_dir != 0 { 20 } else { 0 }},
        {"label": "Zone",
         "val": if at_supply || near_supply { "AT SUPPLY" } else if at_demand || near_demand { "AT DEMAND" } else { "NEUTRAL" },
         "ok": zone_pts > 0, "pts": zone_pts},
        {"label": "Sweep",
         "val": if sweep_bear { "BEARISH SWEEP" } else if sweep_bull { "BULLISH SWEEP" } else { "WAITING" },
         "ok": sweep_ok, "pts": if sweep_ok { 20 } else { 0 }},
        {"label": "2-Candle", "val": if two_ok { "CONFIRMED" } else { "WAITING" },
         "ok": two_ok, "pts": if two_ok { 30 } else { 0 }},
    ]);
    let base: i64 = checks.as_array().unwrap().iter()
        .map(|x| x["pts"].as_i64().unwrap_or(0)).sum();
    let (struct_lbl, _) = structure_label(d, 5);
    let mut bonus = 0i64;
    if struct_lbl == "BULLISH" && htf_dir == 1 { bonus += 10; }
    if struct_lbl == "BEARISH" && htf_dir == -1 { bonus += 10; }
    let buyers = vol_flow["buyers_pct"].as_i64().unwrap_or(50);
    if (htf_dir == 1 && buyers >= 60) || (htf_dir == -1 && buyers <= 40) { bonus += 8; }
    let bias = heatmap_bias(d);
    if (htf_dir == 1 && bias == "BULL") || (htf_dir == -1 && bias == "BEAR") { bonus += 10; }
    let score = (base + bonus).min(100);
    let action = if score >= 75 { "EXECUTE" } else if score >= 50 { "MONITOR" } else { "WAIT" };
    json!({"score": score, "action": action, "checks": checks, "bonus": bonus,
           "htf_dir": htf_dir, "at_supply": at_supply || near_supply,
           "at_demand": at_demand || near_demand, "sweep_ok": sweep_ok,
           "two_ok": two_ok})
}

pub fn classify_setup(cf: &serde_json::Value, choch_recent: bool, div_recent: bool) -> String {
    let aligned = cf["htf_dir"].as_i64().unwrap_or(0) != 0;
    let so = cf["sweep_ok"].as_bool().unwrap_or(false);
    let to = cf["two_ok"].as_bool().unwrap_or(false);
    let at_s = cf["at_supply"].as_bool().unwrap_or(false);
    let at_d = cf["at_demand"].as_bool().unwrap_or(false);
    let score = cf["score"].as_i64().unwrap_or(0);
    if aligned && so && to && score >= 90 && div_recent { return "A+ SNIPER CID".into(); }
    if aligned && so && (at_s || at_d) && to { return "GOLDEN SETUP".into(); }
    if aligned && (at_s || at_d) { return "HTF SNIPER ENTRY".into(); }
    if choch_recent { return "SWING REVERSAL".into(); }
    "STANDARD".into()
}

pub fn choch_recent(d: &[Candle], l: usize, window: usize) -> bool {
    let ev = structure_events(d, l);
    let n = d.len();
    ev.choch_bull.iter().zip(ev.choch_bear.iter()).enumerate()
        .any(|(i, (&a, &b))| i >= n.saturating_sub(window) && (a || b))
}

pub fn rsi_divergence_recent(d: &[Candle], l: usize, window: usize) -> bool {
    let c = closes(d);
    let r = rsi(&c, 14);
    let n = d.len();
    if n < 2 * l + 1 { return false; }
    for p in (n.saturating_sub(window)).max(l)..n - l {
        let m = (p - l..=p + l).map(|k| d[k].low).fold(f64::INFINITY, f64::min);
        if d[p].low == m && r[p] > r[p - l] { return true; }
        let m = (p - l..=p + l).map(|k| d[k].high).fold(f64::NEG_INFINITY, f64::max);
        if d[p].high == m && r[p] < r[p - l] { return true; }
    }
    false
}

pub fn two_stage(d: &[Candle], zm: &serde_json::Value) -> serde_json::Value {
    let n = d.len();
    let c = d[n - 1].close;
    let (o, cl) = (d[n - 1].open, d[n - 1].close);
    let (po, pcl) = (d[n - 2].open, d[n - 2].close);
    let in_zone = |side: &str| zm[side].as_array().map(|arr| arr.iter().any(|z| {
        let (b, t) = (z["bottom"].as_f64().unwrap_or(0.0), z["top"].as_f64().unwrap_or(0.0));
        b <= c && c <= t
    })).unwrap_or(false);
    let reject_bull = cl > o && cl > po;
    let reject_bear = cl < o && cl < po;
    if in_zone("demand") {
        return if reject_bull { json!({"stage": 2, "label": "VALID SIGNAL (BUY)", "detail": "Rejection di Demand terkonfirmasi"}) }
            else { json!({"stage": 1, "label": "GET READY (BUY)", "detail": "Harga di Demand — tunggu candle rejection"}) };
    }
    if in_zone("supply") {
        return if reject_bear { json!({"stage": 2, "label": "VALID SIGNAL (SELL)", "detail": "Rejection di Supply terkonfirmasi"}) }
            else { json!({"stage": 1, "label": "GET READY (SELL)", "detail": "Harga di Supply — tunggu candle rejection"}) };
    }
    json!({"stage": 0, "label": "SCANNING", "detail": "Harga di No Trade Zone (tengah range)"})
}

pub fn session_info(now_utc_ms: i64) -> serde_json::Value {
    let wib_hour = (((now_utc_ms / 3_600_000 + 7) % 24) + 24) as i64 % 24;
    let sessions: &[(&str, i64, i64, &str, &str)] = &[
        ("Asia (Tokyo / Sydney)", 6, 14, "Rendah", "Hindari, sinyal sedikit"),
        ("London (Eropa)", 14, 19, "Tinggi", "Waktu terbaik untuk trading"),
        ("New York (US Overlap)", 19, 3, "Sangat Tinggi", "Potensi besar, hati-hati"),
        ("Rollover (Dini Hari)", 3, 6, "Rendah", "Spread lebar / volatilitas rendah"),
    ];
    for (name, a, b, vol, rec) in sessions {
        let inside = if a < b { wib_hour >= *a && wib_hour < *b } else { wib_hour >= *a || wib_hour < *b };
        if inside {
            return json!({"session": name, "volatility": vol, "recommendation": rec, "wib_hour": wib_hour});
        }
    }
    json!({"session": "Antar-sesi", "volatility": "Rendah", "recommendation": "Tunggu sesi London/NY", "wib_hour": wib_hour})
}

pub fn ai_reasoning(d: &[Candle], zm: &serde_json::Value, ts: &serde_json::Value, cf: &serde_json::Value) -> serde_json::Value {
    let cond = market_condition(d, 60);
    let c = d.last().map(|x| x.close).unwrap_or(0.0);
    let situasi = format!("GOLDEN SMC: {} — harga {:.2}",
        if cond == "TRENDING" { "[TRENDING]" } else { "[SIDEWAYS]" }, c);
    let stage = ts["stage"].as_i64().unwrap_or(0);
    let (karena, maka) = match stage {
        0 => ("Karena harga berada di tengah (No Trade Zone) dan belum ada pola manipulasi (Sweep) dari area Supply/Demand yang valid.".to_string(),
              "Maka STANDBY. Biarkan harga mendatangi zona institusional terlebih dahulu.".to_string()),
        1 => {
            let label = ts["label"].as_str().unwrap_or("");
            let sisi = label.split('(').nth(1).map(|x| x.trim_end_matches(')')).unwrap_or("");
            (format!("Karena harga sudah menyentuh zona {sisi} namun belum ada rejection 2-candle."),
             "Maka GET READY. Jangan entry dulu — tunggu candle rejection di dalam zona.".to_string())
        }
        _ => {
            let label = ts["label"].as_str().unwrap_or("");
            let sisi = label.split('(').nth(1).map(|x| x.trim_end_matches(')')).unwrap_or("");
            let score = cf["score"].as_i64().unwrap_or(0);
            let action = cf["action"].as_str().unwrap_or("WAIT");
            (format!("Karena rejection terkonfirmasi di zona {sisi} dan skor confluence {score}/100."),
             format!("Maka {action}. Eksekusi sesuai setup {label} dengan SL/TP dari engine."))
        }
    };
    json!({"situasi": situasi, "karena": karena, "maka": maka})
}

pub fn fvg_zones(d: &[Candle], max_age: usize, max_gaps: usize) -> serde_json::Value {
    let n = d.len();
    let mut out: Vec<serde_json::Value> = vec![];
    for i in 2..n {
        let (kind, top, bottom) = if d[i].low > d[i - 2].high {
            ("bull", d[i].low, d[i - 2].high)
        } else if d[i].high < d[i - 2].low {
            ("bear", d[i - 2].low, d[i].high)
        } else { continue };
        if i + 1 < n {
            let future = &d[i + 1..];
            if kind == "bull" && future.iter().map(|x| x.low).fold(f64::INFINITY, f64::min) <= bottom { continue; }
            if kind == "bear" && future.iter().map(|x| x.high).fold(f64::NEG_INFINITY, f64::max) >= top { continue; }
        }
        let age = n - 1 - i;
        if age <= max_age {
            out.push(json!({"kind": kind, "top": fmt2(top), "bottom": fmt2(bottom), "age": age as i64}));
        }
    }
    out.sort_by_key(|g| g["age"].as_i64().unwrap_or(0));
    json!(out.into_iter().take(max_gaps).collect::<Vec<_>>())
}

pub fn order_blocks(d: &[Candle], l: usize, max_blocks: usize, lookback: usize) -> serde_json::Value {
    let st = window_idx(d, lookback);
    let w = &d[st..];
    let n = w.len();
    let ev = structure_events(w, l);
    let mut blocks: Vec<(String, f64, f64, i64)> = vec![];
    for (kind, arr) in [("bull", &ev.bos_bull), ("bull", &ev.choch_bull),
                        ("bear", &ev.bos_bear), ("bear", &ev.choch_bear)] {
        let bars: Vec<usize> = (0..n).filter(|&i| arr[i]).collect();
        for &i in bars.iter().rev().take(2) {
            let mut j = i as i64 - 1;
            while j >= 0 {
                let cc = w[j as usize].close;
                let oo = w[j as usize].open;
                let opposite = if kind == "bull" { cc < oo } else { cc > oo };
                if opposite { break; }
                j -= 1;
            }
            if j < 0 { continue; }
            let j = j as usize;
            let (top, bottom) = (w[j].high, w[j].low);
            if i + 1 < n {
                let future = &w[i + 1..];
                if kind == "bull" && future.iter().map(|x| x.low).fold(f64::INFINITY, f64::min) <= bottom { continue; }
                if kind == "bear" && future.iter().map(|x| x.high).fold(f64::NEG_INFINITY, f64::max) >= top { continue; }
            }
            blocks.push((kind.to_string(), top, bottom, w[j].time_ms));
        }
    }
    let mut seen = std::collections::HashSet::<(String, String, String)>::new();
    let mut uniq = vec![];
    for (kind, top, bottom, t) in blocks {
        let key = (kind.clone(), format!("{}", fmt2(top)), format!("{}", fmt2(bottom)));
        if seen.insert(key) {
            uniq.push(json!({"kind": kind, "top": fmt2(top), "bottom": fmt2(bottom), "time": ms_to_str(t)}));
        }
    }
    json!(uniq.into_iter().take(max_blocks).collect::<Vec<_>>())
}

pub fn forecast(d: &[Candle], entry_hint: Option<f64>) -> serde_json::Value {
    let c = closes(d);
    let e20 = ema(&c, 20);
    let e50 = ema(&c, 50);
    let i = c.len() - 1;
    let slope = e20[i] - if i >= 4 { e20[i - 4] } else { e20[0] };
    let above = c[i] > e50[i];
    let direction = if slope > 0.0 && above { "BULLISH" }
        else if slope < 0.0 && !above { "BEARISH" } else { "NEUTRAL" };
    let a = atr(&d.iter().map(|x| x.high).collect::<Vec<_>>(),
                &d.iter().map(|x| x.low).collect::<Vec<_>>(), &c, 14)
        .last().cloned().unwrap_or(0.0);
    let base = entry_hint.unwrap_or(c[i]);
    let (tp1, tp2, tp3, sl) = if direction == "BEARISH" {
        (base - a, base - 2.0 * a, base - 3.0 * a, base + 1.5 * a)
    } else {
        (base + a, base + 2.0 * a, base + 3.0 * a, base - 1.5 * a)
    };
    json!({"direction": direction, "slope_ema20": (slope * 10000.0).round() / 10000.0,
           "above_ema50": above, "atr": fmt2(a), "tp1": fmt2(tp1), "tp2": fmt2(tp2),
           "tp3": fmt2(tp3), "sl": fmt2(sl)})
}

/// Previous Day High/Low dari candle 1h (grup tanggal UTC) — implementasi
/// native tanpa library smartmoneyconcepts.
pub fn pdh_pdl(d1h: &[Candle]) -> serde_json::Value {
    if d1h.len() < 48 { return serde_json::Value::Null; }
    // tanggal UTC dari open time
    let day_of = |ms: i64| ms.div_euclid(86_400_000);
    let last = d1h.last().unwrap();
    let today = day_of(last.time_ms);
    let prev_bars: Vec<&Candle> = d1h.iter().filter(|c| day_of(c.time_ms) == today - 1).collect();
    if prev_bars.is_empty() { return serde_json::Value::Null; }
    let ph = prev_bars.iter().map(|c| c.high).fold(f64::NEG_INFINITY, f64::max);
    let pl = prev_bars.iter().map(|c| c.low).fold(f64::INFINITY, f64::min);
    if !ph.is_finite() || !pl.is_finite() { return serde_json::Value::Null; }
    let today_bars: Vec<&Candle> = d1h.iter().filter(|c| day_of(c.time_ms) == today).collect();
    let cur_hi = today_bars.iter().map(|c| c.high).fold(f64::NEG_INFINITY, f64::max);
    let cur_lo = today_bars.iter().map(|c| c.low).fold(f64::INFINITY, f64::min);
    json!({"pdh": fmt2(ph), "pdl": fmt2(pl),
           "broken_high": cur_hi > ph, "broken_low": cur_lo < pl})
}

/// Heatmap MTF per TF (Trend/Zone/Momentum/CVD Flow/Breakout).
pub fn heatmap_row(d: &[Candle]) -> serde_json::Value {
    let c = closes(d);
    let i = c.len() - 1;
    let (e9, e20, e50) = (ema(&c, 9), ema(&c, 20), ema(&c, 50));
    let r = rsi(&c, 14);
    let ev = structure_events(d, 5);
    let stack_bull = e9[i] > e20[i] && e20[i] > e50[i];
    let stack_bear = e9[i] < e20[i] && e20[i] < e50[i];
    let (state, _) = structure_state(d, 5);
    let trend = if stack_bull { "BULL" } else if stack_bear { "BEAR" }
        else if state == "EXPANDING" { "EXP" } else { "RANG" };
    let a = atr(&d.iter().map(|x| x.high).collect::<Vec<_>>(),
                &d.iter().map(|x| x.low).collect::<Vec<_>>(), &c, 14)
        .last().cloned().unwrap_or(0.0);
    let z = zones(d, a, 5, 200);
    let zstat = |side: &str| z[side]["status"].as_str().map(|s| {
        s == "PRICE IN ZONE" || s == "AT SUPPLY" || s == "AT DEMAND"
    }).unwrap_or(false);
    let zone = if zstat("supply") { "SUPPLY" }
        else if zstat("demand") { "DEMAND" } else { "NEUT" };
    let mom = if r[i] > 55.0 { "BULL" } else if r[i] < 45.0 { "BEAR" } else { "NEUT" };
    // cvd slope 10 bar
    let cvd_at = |k: usize| -> f64 {
        d[..=k].iter().map(|x| (x.close - x.open).signum() * x.volume).sum()
    };
    let slope = if i >= 10 { cvd_at(i) - cvd_at(i - 10) } else { 0.0 };
    let flow = if slope > 0.0 { "BUY" } else if slope < 0.0 { "SELL" } else { "MIXED" };
    let tail_ok = |arr: &[bool]| arr.iter().rev().take(10).any(|&x| x);
    let any10 = |a: &[bool], b: &[bool]| -> bool {
        let n = a.len();
        (n.saturating_sub(10)..n).any(|i| a[i] || b[i])
    };
    let brk = if any10(&ev.bos_bull, &ev.choch_bull) { "CH-BULL" }
        else if any10(&ev.bos_bear, &ev.choch_bear) { "CH-BEAR" } else { "WAIT" };
    json!({"Trend": trend, "Zone": zone, "Momentum": mom, "CVD Flow": flow, "Breakout": brk})
}
