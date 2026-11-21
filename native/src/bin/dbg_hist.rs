use alpha_trade_native::engine::Candle;
use serde_json::Value;
fn main() {
    let c: Value = serde_json::from_str(&std::fs::read_to_string("tests/cases_edge1h_2026_filters.json").unwrap()).unwrap();
    let idx = 2789usize; let lb = 200usize; let bins = 48usize; let va = 0.70;
    let wc: Vec<f64> = (idx-lb..idx).map(|i| c["entry"]["close"][i].as_f64().unwrap()).collect();
    let wv: Vec<f64> = (idx-lb..idx).map(|i| c["entry"]["volume"][i].as_f64().unwrap()).collect();
    let lo_w = wc.iter().cloned().fold(f64::INFINITY, f64::min);
    let hi_w = wc.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    let norm = bins as f64 / (hi_w - lo_w);
    let mut hist = vec![0.0f64; bins];
    for (&cc, &vv) in wc.iter().zip(wv.iter()) {
        let mut b = ((cc - lo_w) * norm).floor() as usize;
        if b >= bins { b = bins - 1; }
        hist[b] += vv;
    }
    let total: f64 = hist.iter().sum();
    let target = total * va;
    let mut p = 0usize; for k in 1..bins { if hist[k] > hist[p] { p = k; } }
    let (mut lo, mut hi) = (p, p); let mut acc = hist[p];
    while acc < target && (lo > 0 || hi < bins-1) {
        let below = if lo > 0 { hist[lo-1] } else { -1.0 };
        let above = if hi < bins-1 { hist[hi+1] } else { -1.0 };
        if above >= below { hi += 1; acc += hist[hi]; } else { lo -= 1; acc += hist[lo]; }
    }
    println!("rs: poc_bin {p} val_bin {lo} vah_bin {hi} target {target:.2} acc {acc:.2}");
    let nz: Vec<String> = hist.iter().enumerate().filter(|(_, x)| **x > 0.0)
        .map(|(k, x)| format!("{k}: {:.1}", x)).collect();
    println!("rs: hist nonzero: {}", nz.join(", "));
    println!("rs: edges lo/hi/width: {lo_w} {hi_w} {:.15}", (hi_w-lo_w)/bins as f64);
}
