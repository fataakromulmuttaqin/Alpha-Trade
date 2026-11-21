// Cetak sinyal Rust utk kasus JSON (debug validasi)
use alpha_trade_native::engine::{generate_signals, Candle, Config};
use serde_json::Value;

fn load(v: &Value) -> Vec<Candle> {
    let t = v["time"].as_array().unwrap();
    (0..t.len()).map(|i| {
        let s = t[i].as_str().unwrap();
        Candle { time_ms: iso(s), open: v["open"][i].as_f64().unwrap(),
                 high: v["high"][i].as_f64().unwrap(), low: v["low"][i].as_f64().unwrap(),
                 close: v["close"][i].as_f64().unwrap(), volume: v["volume"][i].as_f64().unwrap() }
    }).collect()
}
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
    let path = std::env::args().nth(1).unwrap();
    let v: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
    let df = load(&v["entry"]); let htf = load(&v["htf"]);
    let cfg = Config::from_json(&v["config"]);
    let sigs = generate_signals(&df, &cfg, &htf);
    for s in &sigs { println!("{} {} entry={:.2} sl={:.2} tp1={:.2} tp2={:.2} score={}", s.time_ms, s.dir, s.entry, s.sl, s.tp1, s.tp2, s.score); }
}
