use alpha_trade_native::engine::{Candle, Config};
use alpha_trade_native::indicators::{divergence, price_action, rsi};
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
    let c: Value = serde_json::from_str(&std::fs::read_to_string("tests/cases_edge1h_2026_filters.json").unwrap()).unwrap();
    let dp: Value = serde_json::from_str(&std::fs::read_to_string("tests/div_pa.json").unwrap()).unwrap();
    let n = c["entry"]["time"].as_array().unwrap().len();
    let df: Vec<Candle> = (0..n).map(|i| Candle {
        time_ms: iso(c["entry"]["time"][i].as_str().unwrap()),
        open: c["entry"]["open"][i].as_f64().unwrap(),
        high: c["entry"]["high"][i].as_f64().unwrap(),
        low: c["entry"]["low"][i].as_f64().unwrap(),
        close: c["entry"]["close"][i].as_f64().unwrap(),
        volume: c["entry"]["volume"][i].as_f64().unwrap() }).collect();
    let cfg = Config::default();
    let r = rsi(&df.iter().map(|x| x.close).collect::<Vec<_>>(), 14);
    let (bd, br) = divergence(&df.iter().map(|x| x.high).collect::<Vec<_>>(),
                              &df.iter().map(|x| x.low).collect::<Vec<_>>(), &r, 5);
    let pa = price_action(&df.iter().map(|x| x.open).collect::<Vec<_>>(),
                          &df.iter().map(|x| x.high).collect::<Vec<_>>(),
                          &df.iter().map(|x| x.low).collect::<Vec<_>>(),
                          &df.iter().map(|x| x.close).collect::<Vec<_>>(), 20);
    let cmp = |name: &str, a: &[bool], b: &[bool]| {
        let mut diff = 0;
        for i in 0..n.min(a.len()).min(b.len()) {
            if a[i] != b[i] { diff += 1; if diff <= 5 {
                println!("BEDA {name} bar {i} ({}): rust={} python={}", c["entry"]["time"][i].as_str().unwrap(), a[i], b[i]);
            }}
        }
        println!("{name}: {diff} beda");
    };
    let arr = |k: &str| -> Vec<bool> { dp[k].as_array().unwrap().iter().map(|x| x.as_bool().unwrap()).collect() };
    cmp("bull_div", &bd, &arr("bull_div"));
    cmp("bear_div", &br, &arr("bear_div"));
    cmp("pa_bull", &pa.bull, &arr("pa_bull"));
    cmp("pa_bear", &pa.bear, &arr("pa_bear"));
}
