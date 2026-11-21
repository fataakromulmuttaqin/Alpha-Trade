//! Feed Binance REST polling (data-api.binance.vision) — candle CLOSED saja.

use crate::engine::Candle;
use std::collections::HashMap;

pub const TF_LIST: &[&str] = &["1m", "5m", "15m", "30m", "1h", "4h"];
const BASE: &str = "https://data-api.binance.vision/api/v3/klines";

pub async fn fetch_klines(
    client: &reqwest::Client, symbol: &str, interval: &str, limit: u32,
) -> Result<Vec<Candle>, String> {
    let url = format!("{}?symbol={}&interval={}&limit={}", BASE, symbol, interval, limit);
    let resp = client.get(&url).send().await.map_err(|e| e.to_string())?;
    if !resp.status().is_success() {
        return Err(format!("HTTP {}", resp.status()));
    }
    let rows: serde_json::Value = resp.json().await.map_err(|e| e.to_string())?;
    let arr = rows.as_array().ok_or("respons bukan array")?;
    let mut out = vec![];
    for r in arr {
        // [open_time, o, h, l, c, v, close_time, ...]
        let t = r[0].as_i64().unwrap_or(0);
        let p = |k: usize| r[k].as_str().unwrap_or("0").parse::<f64>().unwrap_or(0.0);
        out.push(Candle {
            time_ms: t, open: p(1), high: p(2), low: p(3), close: p(4), volume: p(5),
        });
    }
    Ok(out)
}

/// Merge candle baru (closed saja: buang elemen terakhir = masih open).
pub fn merge_closed(buf: &mut Vec<Candle>, mut fresh: Vec<Candle>) -> bool {
    if !fresh.is_empty() { fresh.pop(); } // buang candle yang masih berjalan
    let mut added = false;
    for c in fresh {
        if buf.last().map(|b| c.time_ms > b.time_ms).unwrap_or(true) {
            buf.push(c);
            added = true;
        }
    }
    // jaga panjang buffer
    let cap = 5000;
    if buf.len() > cap {
        let excess = buf.len() - cap;
        buf.drain(0..excess);
    }
    added
}

pub type Buffers = HashMap<String, Vec<Candle>>;

pub async fn seed_all(client: &reqwest::Client, symbol: &str) -> Result<Buffers, String> {
    let mut bufs: Buffers = HashMap::new();
    for tf in TF_LIST {
        let k = fetch_klines(client, symbol, tf, 1500).await?;
        bufs.insert(tf.to_string(), k);
    }
    Ok(bufs)
}

/// Poll semua TF; kembalikan daftar TF yang dapat candle closed baru.
pub async fn poll_all(client: &reqwest::Client, symbol: &str, bufs: &mut Buffers) -> Vec<String> {
    let mut changed = vec![];
    for tf in TF_LIST {
        match fetch_klines(client, symbol, tf, 3).await {
            Ok(fresh) => {
                let buf = bufs.entry(tf.to_string()).or_default();
                if merge_closed(buf, fresh) { changed.push(tf.to_string()); }
            }
            Err(_) => continue,
        }
    }
    changed
}
