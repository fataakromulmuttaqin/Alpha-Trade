//! Validasi kesetiaan port: jalankan engine Rust pada kasus hasil dump Python,
//! bandingkan waktu sinyal (harus identik) + harga (toleransi 1e-6 relatif).

use alpha_trade_native::engine::{generate_signals, Candle, Config};
use serde_json::Value;

fn iso_to_ms_pub(s: &str) -> i64 { iso_to_ms(s) }

fn load_candles(v: &Value) -> Vec<Candle> {
    let t = v["time"].as_array().unwrap();
    (0..t.len())
        .map(|i| {
            // "2026-11-21 09:00:00+00:00" -> epoch ms
            let s = t[i].as_str().unwrap();
            Candle {
                time_ms: iso_to_ms(s),
                open: v["open"][i].as_f64().unwrap(),
                high: v["high"][i].as_f64().unwrap(),
                low: v["low"][i].as_f64().unwrap(),
                close: v["close"][i].as_f64().unwrap(),
                volume: v["volume"][i].as_f64().unwrap(),
            }
        })
        .collect()
}

fn iso_to_ms(s: &str) -> i64 {
    // parse manual "YYYY-MM-DD HH:MM:SS+00:00"
    let y: i64 = s[0..4].parse().unwrap();
    let mo: i64 = s[5..7].parse().unwrap();
    let d: i64 = s[8..10].parse().unwrap();
    let h: i64 = s[11..13].parse().unwrap();
    let mi: i64 = s[14..16].parse().unwrap();
    let se: i64 = s[17..19].parse().unwrap();
    // days since epoch (Hinnant)
    let y_adj = if mo <= 2 { y - 1 } else { y };
    let era = y_adj.div_euclid(400);
    let yoe = y_adj - era * 400;
    let mp = if mo > 2 { mo - 3 } else { mo + 9 };
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146_097 + doe - 719_468;
    (days * 86400 + h * 3600 + mi * 60 + se) * 1000
}

fn run_case(path: &str) {
    let txt = std::fs::read_to_string(path).unwrap();
    let v: Value = serde_json::from_str(&txt).unwrap();
    let df = load_candles(&v["entry"]);
    let htf = load_candles(&v["htf"]);
    let cfg = Config::from_json(&v["config"]);
    let expected = v["signals"].as_array().unwrap();

    let got = generate_signals(&df, &cfg, &htf);
    println!("{}: python={} sinyal, rust={} sinyal", path, expected.len(), got.len());
    assert_eq!(got.len(), expected.len(),
        "JUMLAH sinyal berbeda — port tidak setia");

    for (i, (g, e)) in got.iter().zip(expected.iter()).enumerate() {
        assert_eq!(g.time_ms, crate::iso_to_ms_pub(e["time"].as_str().unwrap()),
            "waktu sinyal #{i} berbeda: rust={} vs python={}",
            g.time_ms, e["time"]);
        assert_eq!(g.dir, e["dir"].as_i64().unwrap() as i32, "arah #{i}");
        for (name, gv, ev) in [
            ("entry", g.entry, e["entry"].as_f64().unwrap()),
            ("sl", g.sl, e["sl"].as_f64().unwrap()),
            ("tp1", g.tp1, e["tp1"].as_f64().unwrap()),
            ("tp2", g.tp2, e["tp2"].as_f64().unwrap()),
        ] {
            let tol = ev.abs().max(1.0) * 1e-6;
            assert!((gv - ev).abs() <= tol,
                "{name} #{i} berbeda: rust={gv} vs python={ev}");
        }
    }
    println!("  -> N/N MATCH");
}

#[test]
fn validate_edge1h_full() {
    run_case(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/cases_edge1h_full.json"));
}

#[test]
fn validate_filters_2026() {
    run_case(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/cases_edge1h_2026_filters.json"));
}
