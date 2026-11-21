use serde_json::Value;
// Gate-by-gate di bar tertentu (vs nilai Python manual).
use alpha_trade_native::engine::{Candle, Config};
use alpha_trade_native::indicators::*;
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
fn recent(mask: &[bool], w: usize) -> Vec<bool> {
    let n = mask.len(); let mut out = vec![false; n]; let mut cnt = 0usize;
    for i in 0..n {
        if i >= w && mask[i - w] { cnt -= 1; }
        if mask[i] { cnt += 1; }
        out[i] = cnt > 0;
    }
    out
}
fn main() {
    let c: Value = serde_json::from_str(&std::fs::read_to_string("tests/cases_edge1h_2026_filters.json").unwrap()).unwrap();
    let n = c["entry"]["time"].as_array().unwrap().len();
    let df: Vec<Candle> = (0..n).map(|i| Candle {
        time_ms: iso(c["entry"]["time"][i].as_str().unwrap()),
        open: c["entry"]["open"][i].as_f64().unwrap(),
        high: c["entry"]["high"][i].as_f64().unwrap(),
        low: c["entry"]["low"][i].as_f64().unwrap(),
        close: c["entry"]["close"][i].as_f64().unwrap(),
        volume: c["entry"]["volume"][i].as_f64().unwrap() }).collect();
    let cfg = Config::from_json(&c["config"]);
    let cl: Vec<f64> = df.iter().map(|x| x.close).collect();
    let hi: Vec<f64> = df.iter().map(|x| x.high).collect();
    let lo: Vec<f64> = df.iter().map(|x| x.low).collect();
    let e9 = ema(&cl, 9); let e20 = ema(&cl, 20); let e50 = ema(&cl, 50);
    let r = rsi(&cl, cfg.rsi_len);
    let (_ml, ms, hist) = macd(&cl, cfg.macd_fast, cfg.macd_slow, cfg.macd_sig);
    let a = atr(&hi, &lo, &cl, cfg.atr_len);
    let (poc, val, vah) = volume_profile_levels(&cl, &df.iter().map(|x| x.volume).collect::<Vec<_>>(), cfg.vp_lookback, cfg.vp_bins, cfg.vp_value_area);
    let (bd, br) = divergence(&hi, &lo, &r, cfg.pivot_len);
    let pa = price_action(&df.iter().map(|x| x.open).collect::<Vec<_>>(), &hi, &lo, &cl, cfg.sweep_lookback);
    let rank = rolling_rank_pct(&a, cfg.atr_rank_window, 100);
    let w = cfg.confirm_window;
    // momentum short
    let mut rrs_raw = vec![false; n];
    for i in 1..n { rrs_raw[i] = r[i-1] > cfg.rsi_pull_short && r[i] <= cfg.rsi_pull_short; }
    let rrs = recent(&or2(&rrs_raw, &br), w);
    let mut ms_raw = vec![false; n];
    for i in 1..n { ms_raw[i] = (ms[i] < ms[i-1] && ms[i-1] >= ms[i-1]) || (hist[i] < 0.0 && hist[i] < hist[i-1]); }
    let ms_rec = recent(&ms_raw, w);
    let or2 = |a: &[bool], b: &[bool]| -> Vec<bool> { a.iter().zip(b.iter()).map(|(x, y)| *x || *y).collect() };
    let _ = &or2;
    let i = 679usize;
    println!("bar 679:");
    println!("  gt_s: trend? e9<e20<c<e50: e9={} e20={} c={} e50={}", e9[i], e20[i], cl[i], e50[i]);
    println!("  rrs(rec)={} ms_raw(rec)={} => gm_s={}", rrs[i], ms_rec[i], rrs[i] && ms_rec[i]);
    let mask_s = or2(&rrs_raw, &br);
    println!("  mask_s 675..679: {:?}", (675..=679).map(|k| mask_s[k]).collect::<Vec<_>>());
    println!("  br 675..679:    {:?}", (675..=679).map(|k| br[k]).collect::<Vec<_>>());
    println!("  rrs_raw 675..679: {:?}", (675..=679).map(|k| rrs_raw[k]).collect::<Vec<_>>());
    println!("  vp: |c-poc|={:.2} |c-val|={:.2} |c-vah|={:.2} prox={:.2}", (cl[i]-poc[i]).abs(), (cl[i]-val[i]).abs(), (cl[i]-vah[i]).abs(), cfg.vp_prox_atr*a[i]);
    println!("  gvol(rank)={} rank={:.4}", rank[i] >= cfg.atr_pct_low && rank[i] <= cfg.atr_pct_high, rank[i]);
    println!("  pa_s={} hist={:.4} hist_prev={:.4}", pa.bear[i], hist[i], hist[i-1]);
}
fn or2(a: &[bool], b: &[bool]) -> Vec<bool> { a.iter().zip(b.iter()).map(|(x, y)| *x || *y).collect() }
// EOF marker tidak dipakai
