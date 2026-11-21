// Bandingkan kandidat long_ok/short_ok Rust (generate_signals_full) vs engine Python.
use alpha_trade_native::engine::{generate_signals_full, Candle, Config};
use serde_json::Value;

fn iso(s: &str) -> i64 {
    let y: i64 = s[0..4].parse().unwrap();
    let mo: i64 = s[5..7].parse().unwrap();
    let d: i64 = s[8..10].parse().unwrap();
    let h: i64 = s[11..13].parse().unwrap();
    let mi: i64 = s[14..16].parse().unwrap();
    let se: i64 = s[17..19].parse().unwrap();
    let y2 = if mo <= 2 { y - 1 } else { y };
    let era = y2.div_euclid(400);
    let yoe = y2 - era * 400;
    let mp = if mo > 2 { mo - 3 } else { mo + 9 };
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    (era * 146_097 + doe - 719_468) * 86_400_000 + (h * 3600 + mi * 60 + se) * 1000
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let case_path = args.get(1).map(|s| s.as_str()).unwrap_or("tests/cases_edge1h_2026_filters.json");
    let cand_path = args.get(2).map(|s| s.as_str()).unwrap_or("tests/candidates.json");
    let cv: Value = serde_json::from_str(&std::fs::read_to_string(case_path).unwrap()).unwrap();
    let cd: Value = serde_json::from_str(&std::fs::read_to_string(cand_path).unwrap()).unwrap();
    let n = cv["entry"]["time"].as_array().unwrap().len();
    let df: Vec<Candle> = (0..n).map(|i| Candle {
        time_ms: iso(cv["entry"]["time"][i].as_str().unwrap()),
        open: cv["entry"]["open"][i].as_f64().unwrap(),
        high: cv["entry"]["high"][i].as_f64().unwrap(),
        low: cv["entry"]["low"][i].as_f64().unwrap(),
        close: cv["entry"]["close"][i].as_f64().unwrap(),
        volume: cv["entry"]["volume"][i].as_f64().unwrap() }).collect();
    let htf_n = cv["htf"]["time"].as_array().unwrap().len();
    let htf: Vec<Candle> = (0..htf_n).map(|i| Candle {
        time_ms: iso(cv["htf"]["time"][i].as_str().unwrap()),
        open: cv["htf"]["open"][i].as_f64().unwrap(),
        high: cv["htf"]["high"][i].as_f64().unwrap(),
        low: cv["htf"]["low"][i].as_f64().unwrap(),
        close: cv["htf"]["close"][i].as_f64().unwrap(),
        volume: cv["htf"]["volume"][i].as_f64().unwrap() }).collect();
    let cfg = Config::from_json(&cv["config"]);
    let (_sigs, rust_long, rust_short) = generate_signals_full(&df, &cfg, &htf);

    let py_long: Vec<bool> = cd["long_ok"].as_array().unwrap().iter()
        .map(|x| x.as_bool().unwrap()).collect();
    let py_short: Vec<bool> = cd["short_ok"].as_array().unwrap().iter()
        .map(|x| x.as_bool().unwrap()).collect();
    let py_times: Vec<&str> = cd["times"].as_array().unwrap().iter()
        .map(|x| x.as_str().unwrap()).collect();

    let mut diff = 0;
    for i in 0..n.min(py_long.len()) {
        if py_long[i] != rust_long[i] || py_short[i] != rust_short[i] {
            diff += 1;
            if diff <= 8 {
                let gates = ["n_l", "n_s", "g_pa_l", "g_pa_s", "g_vol", "g_vp_l", "g_vp_s",
                             "g_mom_l", "g_mom_s", "trend"];
                let detail: Vec<String> = gates.iter().filter_map(|k| {
                    let v = cd.get(*k)?.as_array()?.get(i)?;
                    Some(format!("{k}={}", v))
                }).collect();
                println!("BEDA bar {i} ({}): py L{} S{} | rust L{} S{}  [{}]",
                    py_times[i], py_long[i], py_short[i], rust_long[i], rust_short[i],
                    detail.join(" "));
            }
        }
    }
    println!("total bar kandidat beda: {diff} / {n}");
}
