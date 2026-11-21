// Bandingkan indikator antara Rust vs dump Python; laporkan divergensi pertama.
use alpha_trade_native::indicators::*;
use alpha_trade_native::engine::{Candle, Config};
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
    let v: Value = serde_json::from_str(&std::fs::read_to_string("tests/intermediates.json").unwrap()).unwrap();
    let times: Vec<i64> = v["times"].as_array().unwrap().iter()
        .map(|x| iso(x.as_str().unwrap())).collect();
    // rebuild candles: dump times aja — ambil OHLC dari cases file
    let c: Value = serde_json::from_str(&std::fs::read_to_string("tests/cases_edge1h_2026_filters.json").unwrap()).unwrap();
    let df: Vec<Candle> = (0..c["entry"]["time"].as_array().unwrap().len()).map(|i| {
        Candle { time_ms: iso(c["entry"]["time"][i].as_str().unwrap()),
                 open: c["entry"]["open"][i].as_f64().unwrap(),
                 high: c["entry"]["high"][i].as_f64().unwrap(),
                 low: c["entry"]["low"][i].as_f64().unwrap(),
                 close: c["entry"]["close"][i].as_f64().unwrap(),
                 volume: c["entry"]["volume"][i].as_f64().unwrap() }
    }).collect();
    let htf: Vec<Candle> = (0..c["htf"]["time"].as_array().unwrap().len()).map(|i| {
        Candle { time_ms: iso(c["htf"]["time"][i].as_str().unwrap()),
                 open: c["htf"]["open"][i].as_f64().unwrap(),
                 high: c["htf"]["high"][i].as_f64().unwrap(),
                 low: c["htf"]["low"][i].as_f64().unwrap(),
                 close: c["htf"]["close"][i].as_f64().unwrap(),
                 volume: c["htf"]["volume"][i].as_f64().unwrap() }
    }).collect();
    let cfg = Config::from_json(&c["config"]);

    let cl: Vec<f64> = df.iter().map(|x| x.close).collect();
    let hi: Vec<f64> = df.iter().map(|x| x.high).collect();
    let lo: Vec<f64> = df.iter().map(|x| x.low).collect();

    let arr = |k: &str| -> Vec<f64> {
        v[k].as_array().unwrap().iter()
            .map(|x| x.as_f64().unwrap_or(f64::NAN)).collect()
    };
    let py_rsi = arr("rsi"); let py_line = arr("macd_line"); let py_sig = arr("macd_sig");
    let py_hist = arr("hist"); let py_atr = arr("atr"); let py_poc = arr("poc");
    let py_val = arr("val"); let py_vah = arr("vah"); let py_rank = arr("rank");
    let py_adx = arr("adx"); let py_trend: Vec<i64> = v["trend"].as_array().unwrap()
        .iter().map(|x| x.as_i64().unwrap()).collect();

    let r_rsi = rsi(&cl, cfg.rsi_len);
    let (ml, ms, hist) = macd(&cl, cfg.macd_fast, cfg.macd_slow, cfg.macd_sig);
    let r_atr = atr(&hi, &lo, &cl, cfg.atr_len);
    let (poc, val, vah) = volume_profile_levels(&cl, &df.iter().map(|x| x.volume).collect::<Vec<_>>(), cfg.vp_lookback, cfg.vp_bins, cfg.vp_value_area);
    let r_rank = rolling_rank_pct(&r_atr, cfg.atr_rank_window, 100);
    let r_adx = adx_wilder(&hi, &lo, &cl, 14);

    let cmp = |name: &str, a: &[f64], b: &[f64]| {
        for i in 0..n_len(a.len(), b.len()) {
            let (x, y) = (a[i], b[i]);
            let tol = x.abs().max(y.abs()).max(1.0) * 1e-9;
            if (x.is_nan() != y.is_nan()) || (!x.is_nan() && (x - y).abs() > tol) {
                println!("DIVERGENSI {name} @ bar {i} ({}): rust={x} python={y}", times[i]);
                return;
            }
        }
        println!("{name}: MATCH penuh");
    };
    fn n_len(a: usize, b: usize) -> usize { a.min(b) }

    cmp("rsi", &r_rsi, &py_rsi);
    cmp("macd_line", &ml, &py_line);
    cmp("macd_sig", &ms, &py_sig);
    cmp("hist", &hist, &py_hist);
    cmp("atr", &r_atr, &py_atr);
    cmp("poc", &poc, &py_poc);
    cmp("val", &val, &py_val);
    cmp("vah", &vah, &py_vah);
    cmp("atr_rank", &r_rank, &py_rank);
    cmp("adx", &r_adx, &py_adx);
    // trend HTF
    let hc: Vec<f64> = htf.iter().map(|x| x.close).collect();
    let he50 = ema(&hc, 50); let he200 = ema(&hc, 200);
    let mut htrend = vec![0i32; htf.len()];
    for i in 1..htf.len() {
        if he50[i].is_nan() || he200[i].is_nan() { continue; }
        let d200 = he200[i] - he200[i - 1];
        if hc[i] > he50[i] && he50[i] > he200[i] && d200 > 0.0 { htrend[i] = 1; }
        if hc[i] < he50[i] && he50[i] < he200[i] && d200 < 0.0 { htrend[i] = -1; }
    }
    for i in 0..times.len() {
        let idx = htf.iter().position(|x| x.time_ms > times[i]).unwrap_or(htf.len());
        let t = if idx >= 2 { htrend[idx - 2] as i64 } else { 0 };
        if t != py_trend[i] {
            println!("DIVERGENSI trend @ bar {i} ({}): rust={t} python={}", times[i], py_trend[i]);
            return;
        }
    }
    println!("trend: MATCH penuh");
}
