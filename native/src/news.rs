//! News feed v1 native: newsdata.io langsung (key user, env override).
//! Verifikasi silang & fallback provider: iterasi berikutnya.

use serde_json::json;

const DEFAULT_KEY: &str = "pub_f9baa82bf10240d4bc29c431ef0817d0";
const KEYWORDS: &[(&str, i64)] = &[
    ("gold", 3), ("xauusd", 3), ("fed", 3), ("fomc", 3), ("inflation", 2),
    ("cpi", 3), ("dollar", 2), ("usd", 1), ("interest rate", 3), ("nfp", 3),
    ("payroll", 2), ("retail sales", 2), ("central bank", 2), ("ecb", 2),
    ("boj", 2), ("boe", 2), ("rate cut", 3), ("rate hike", 3), ("powell", 3),
    ("recession", 2), ("stimulus", 2), ("tariff", 2), ("geopolitic", 2), ("war", 2),
];

fn score(title: &str) -> i64 {
    let t = title.to_lowercase();
    KEYWORDS.iter().filter(|(k, _)| t.contains(k)).map(|(_, w)| *w).sum()
}

pub async fn fetch_news(client: &reqwest::Client, key: Option<&str>) -> serde_json::Value {
    let key = key.unwrap_or(DEFAULT_KEY);
    let url = format!("https://newsdata.io/api/1/latest?apikey={key}&category=business&language=en&size=10");
    let Ok(resp) = client.get(&url).send().await else {
        return json!({"items": [], "count": 0, "updated": "fetch gagal"});
    };
    let Ok(v) = resp.json::<serde_json::Value>().await else {
        return json!({"items": [], "count": 0, "updated": "parse gagal"});
    };
    let mut items = vec![];
    if let Some(results) = v["results"].as_array() {
        for r in results {
            let title = r["title"].as_str().unwrap_or("").to_string();
            if title.is_empty() { continue; }
            let t = r["pubDate"].as_str().unwrap_or("").to_string();
            let s = score(&title);
            items.push(json!({
                "time": t, "title": title,
                "source": r["source_id"].as_str().unwrap_or("newsdata"),
                "url": r["link"].as_str().unwrap_or(""),
                "provider": "newsdata", "score": s, "verified": false,
            }));
        }
    }
    // urutkan skor desc lalu waktu desc
    items.sort_by(|a, b| {
        b["score"].as_i64().unwrap_or(0).cmp(&a["score"].as_i64().unwrap_or(0))
            .then(b["time"].as_str().unwrap_or("").cmp(a["time"].as_str().unwrap_or("")))
    });
    let n = items.len();
    json!({"items": items, "count": n, "updated": now_utc_str()})
}

pub fn now_utc_str() -> String {
    let ms = now_ms();
    crate::godmode::ms_to_str(ms)
}

pub fn now_ms() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as i64).unwrap_or(0)
}
