use alpha_trade_native::engine::{Candle, Config};
use alpha_trade_native::indicators::*;
use serde_json::Value;
fn iso(s: &str) -> i64 {
    let y: i64 = s[0..4].parse().unwrap(); let mo: i64 = s[5..7].parse().unwrap();
    let d: i64 = s[8..10].parse().unwrap(); let h: i64 = s[11..13].parse().unwrap();
    let mi: i64 = s[14..16].parse().unwrap(); let se: i64 = s[17..19].parse().unwrap();
    let y2 = if mo <= 2 { y - 1 } else { y };
    let era = y2.div_euclid(400); let yoe = y2 - era * 400;
    let mp = if mo > 2 { mo - 3 } else { mo + 9 };
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    (era * 146_097 + doe - 719_468) * 86_400_000 + (h * 3600 + mi * 60 + se) * 1000
}
fn main() {
    let c: Value = serde_json::from_str(&std::fs::read_to_string("tests/cases_edge1h_full.json").unwrap()).unwrap();
    let n = c["entry"]["time"].as_array().unwrap().len();
    let df: Vec<Candle> = (0..n).map(|i| Candle {
        time_ms: iso(c["entry"]["time"][i].as_str().unwrap()),
        open: c["entry"]["open"][i].as_f64().unwrap(),
        high: c["entry"]["high"][i].as_f64().unwrap(),
        low: c["entry"]["low"][i].as_f64().unwrap(),
        close: c["entry"]["close"][i].as_f64().unwrap(),
        volume: c["entry"]["volume"][i].as_f64().unwrap() }).collect();
    let htf_n = c["htf"]["time"].as_array().unwrap().len();
    let htf: Vec<Candle> = (0..htf_n).map(|i| Candle {
        time_ms: iso(c["htf"]["time"][i].as_str().unwrap()),
        open: c["htf"]["open"][i].as_f64().unwrap(),
        high: c["htf"]["high"][i].as_f64().unwrap(),
        low: c["htf"]["low"][i].as_f64().unwrap(),
        close: c["htf"]["close"][i].as_f64().unwrap(),
        volume: c["htf"]["volume"][i].as_f64().unwrap() }).collect();
    let cfg = Config::from_json(&c["config"]);
    let i = 6018usize;
    let cl: Vec<f64> = df.iter().map(|x| x.close).collect();
    let hi: Vec<f64> = df.iter().map(|x| x.high).collect();
    let lo: Vec<f64> = df.iter().map(|x| x.low).collect();
    let e9 = ema(&cl, 9); let e20 = ema(&cl, 20); let e50 = ema(&cl, 50);
    let a = atr(&hi, &lo, &cl, cfg.atr_len);
    let (_poc, val, vah) = volume_profile_levels(&cl, &df.iter().map(|x| x.volume).collect::<Vec<_>>(), cfg.vp_lookback, cfg.vp_bins, cfg.vp_value_area);
    let rank = rolling_rank_pct(&a, cfg.atr_rank_window, 100);
    let r = rsi(&cl, cfg.rsi_len);
    let (_ml, ms, hist) = macd(&cl, cfg.macd_fast, cfg.macd_slow, cfg.macd_sig);
    // trend rust (fix idx-2)
    let hc: Vec<f64> = htf.iter().map(|x| x.close).collect();
    let he50 = ema(&hc, 50); let he200 = ema(&hc, 200);
    let mut htrend = vec![0i32; htf.len()];
    for k in 1..htf.len() {
        if he50[k].is_nan() || he200[k].is_nan() { continue; }
        let d200 = he200[k] - he200[k - 1];
        if hc[k] > he50[k] && he50[k] > he200[k] && d200 > 0.0 { htrend[k] = 1; }
        if hc[k] < he50[k] && he50[k] < he200[k] && d200 < 0.0 { htrend[k] = -1; }
    }
    let idx = htf.iter().position(|x| x.time_ms > df[i].time_ms).unwrap_or(htf.len());
    let trend = if idx >= 2 { htrend[idx - 2] } else { 0 };
    println!("bar {i} ({}):", c["entry"]["time"][i]);
    println!("  trend rust={trend}");
    println!("  stack_s: e9<e20<c<e50 -> {} < {} ? c {} < e50 {}", e9[i], e20[i], cl[i], e50[i]);
    println!("  pa.bull={}", {
        let pa = price_action(&df.iter().map(|x| x.open).collect::<Vec<_>>(), &hi, &lo, &cl, cfg.sweep_lookback);
        format!("{}", pa.bull[i])
    });
    println!("  rank={} gvol={}", rank[i], rank[i] >= cfg.atr_pct_low && rank[i] <= cfg.atr_pct_high);
    println!("  vp: c-val={:.2} c-vah={:.2} prox={:.3}", cl[i]-val[i], cl[i]-vah[i], cfg.vp_prox_atr*a[i]);
    println!("  hist={:.4} hist_prev={:.4} rsi={:.2}", hist[i], hist[i-1], r[i]);
}
