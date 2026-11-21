//! Alpha-Trade Native — server Rust tunggal: frontend embedded + REST API +
//! feed Binance + engine 5-gate + PaperSim. Menggantikan backend Python.

use alpha_trade_native::engine;
use alpha_trade_native::feed;
use alpha_trade_native::godmode;
use alpha_trade_native::indicators;
use alpha_trade_native::news;
use alpha_trade_native::sim;
use alpha_trade_native::snapshot;

use axum::{
    extract::{Query, State},
    http::{header, HeaderValue, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use engine::{apply_mode, generate_signals, Config};
use feed::Buffers;
use sim::PaperSim;
use snapshot::SnapInputs;
use std::collections::HashMap;
use std::sync::Arc;
use serde_json::json;
use tokio::sync::RwLock;

struct App {
    buffers: Buffers,
    cfg: Config,
    sensitivity: String,
    exit_protocol: String,
    sim: PaperSim,
    signals_log: Vec<serde_json::Value>,
    suppressed: Vec<serde_json::Value>,
    suppressed_count: usize,
    last_sig_time: Option<i64>,
    news: serde_json::Value,
    symbol: String,
    entry_tf: String,
}

type SharedApp = Arc<RwLock<App>>;

use include_dir::include_dir;
static FRONTEND_DIR: include_dir::Dir =
    include_dir!("$CARGO_MANIFEST_DIR/../frontend");

#[tokio::main]
async fn main() {
    let port: u16 = std::env::var("AT_PORT").ok()
        .and_then(|p| p.parse().ok()).unwrap_or(8794);
    let symbol = std::env::var("AT_SYMBOL").unwrap_or_else(|_| "PAXGUSDT".into());
    let entry_tf = std::env::var("AT_ENTRY_TF").unwrap_or_else(|_| "1h".into());
    let daily_stop: f64 = std::env::var("AT_SIM_DAILY_STOP").ok()
        .and_then(|p| p.parse().ok()).unwrap_or(0.0);

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(15)).build().unwrap();

    print!("[feed] seed klines {} ...", symbol);
    let buffers = feed::seed_all(&client, &symbol).await
        .expect("seed klines gagal — cek koneksi data-api.binance.vision");
    println!(" ok ({} TF)", buffers.len());

    let mut cfg = Config::default();
    let (sens, exitp) = (std::env::var("AT_SENSITIVITY").unwrap_or_else(|_| "edge1h".into()),
                         std::env::var("AT_EXIT").unwrap_or_else(|_| "engine".into()));
    if engine::valid_sensitivity(&sens) {
        cfg = apply_mode(&cfg, Some(&sens), None);
    }
    if engine::valid_exit(&exitp) {
        cfg = apply_mode(&cfg, None, Some(&exitp));
    }

    let news = news::fetch_news(&client, std::env::var("NEWS_KEY_NEWSDATA").ok().as_deref()).await;

    let sim = PaperSim::new(daily_stop, 0);
    let app = Arc::new(RwLock::new(App {
        buffers, cfg, sensitivity: sens, exit_protocol: exitp, sim,
        signals_log: vec![], suppressed: vec![], suppressed_count: 0,
        last_sig_time: None, news, symbol: symbol.clone(), entry_tf: entry_tf.clone(),
    }));

    // seed sinyal terakhir + proses candle terakhir oleh sim
    {
        let mut a = app.write().await;
        handle_engine_tick(&mut a, &entry_tf);
    }

    // ---- feed loop
    let (feed_app, feed_client, feed_symbol, feed_entry_tf) =
        (app.clone(), client.clone(), symbol.clone(), entry_tf.clone());
    tokio::spawn(async move {
        loop {
            let changed = {
                let mut a = feed_app.write().await;
                feed::poll_all(&feed_client, &feed_symbol, &mut a.buffers).await
            };
            if changed.iter().any(|tf| *tf == feed_entry_tf) {
                let mut a = feed_app.write().await;
                handle_engine_tick(&mut a, &feed_entry_tf);
            }
            tokio::time::sleep(std::time::Duration::from_secs(4)).await;
        }
    });

    // ---- news loop tiap 10 menit
    let (news_app, news_client) = (app.clone(), client.clone());
    tokio::spawn(async move {
        loop {
            tokio::time::sleep(std::time::Duration::from_secs(600)).await;
            let n = news::fetch_news(&news_client, None).await;
            news_app.write().await.news = n;
        }
    });

    let url = format!("http://127.0.0.1:{port}");
    println!("[http] dashboard: {url}");
    let _ = std::process::Command::new("cmd").args(["/c", "start", "", &url]).spawn();

    let app_router = Router::new()
        .route("/api/health", get(health))
        .route("/api/snapshot", get(snapshot_ep))
        .route("/api/candles", get(candles_ep))
        .route("/api/signals", get(signals_ep))
        .route("/api/sim", get(sim_ep))
        .route("/api/news", get(news_ep))
        .route("/api/config", get(config_ep))
        .route("/api/settings", post(settings_ep))
        .fallback(static_files)
        .with_state(app);

    let listener = tokio::net::TcpListener::bind(("0.0.0.0", port)).await.unwrap();
    axum::serve(listener, app_router).await.unwrap();
}

/// hitung ulang sinyal pada buffer entry TF; sinyal BARU dieksekusi sim.
fn handle_engine_tick(a: &mut App, entry_tf: &str) {
    let Some(df) = a.buffers.get(entry_tf).cloned() else { return };
    if df.len() < 260 { return; }
    let htf = a.buffers.get(&a.cfg.htf).cloned().unwrap_or_default();
    let sigs = generate_signals(&df, &a.cfg, &htf);
    let last_bar = df.last().unwrap().time_ms;
    if let Some(s) = sigs.last() {
        if s.time_ms == last_bar && a.last_sig_time != Some(s.time_ms) {
            a.last_sig_time = Some(s.time_ms);
            let kind = if s.score >= 3 { "GOLDEN" } else { "HIGH RISK" };
            let entry = json!({
                "time": godmode::ms_to_str(s.time_ms), "tf": entry_tf,
                "side": if s.dir == 1 { "LONG" } else { "SHORT" },
                "entry": sim::r2(s.entry), "sl": sim::r2(s.sl),
                "tp1": sim::r2(s.tp1), "tp2": sim::r2(s.tp2),
                "score": s.score, "kind": kind,
            });
            let allowed = kind == "GOLDEN";
            if allowed {
                a.signals_log.insert(0, entry.clone());
                a.signals_log.truncate(30);
                let mode = a.sensitivity.clone();
                a.sim.queue_signal(s.time_ms, s.dir, s.entry, s.sl, s.tp1, s.tp2,
                                   &mode, kind);
                println!("[sinyal] {} {} entry={} skor={}", kind, entry["side"],
                         entry["entry"], s.score);
            } else {
                a.suppressed.insert(0, entry.clone());
                a.suppressed.truncate(30);
                a.suppressed_count += 1;
                println!("[sinyal disaring] {} skor={}", kind, s.score);
            }
        }
    }
    // kelola posisi sim dengan candle closed terbaru
    if let Some(c) = df.last() {
        for ev in a.sim.on_candle(c) { println!("{ev}"); }
    }
}

// ----------------------------- handlers -------------------------------------
fn cors<T: IntoResponse>(resp: T) -> Response {
    let mut resp = resp.into_response();
    resp.headers_mut().insert(header::ACCESS_CONTROL_ALLOW_ORIGIN,
                              HeaderValue::from_static("*"));
    resp
}

async fn health(State(app): State<SharedApp>) -> Response {
    let a = app.read().await;
    let bars: HashMap<&String, usize> =
        a.buffers.iter().map(|(k, v)| (k, v.len())).collect();
    cors(Json(json!({
        "ok": true, "symbol": a.symbol, "mode": "off", "native": true,
        "sensitivity": a.sensitivity, "exit_protocol": a.exit_protocol,
        "signal_golden": true, "signal_high_risk": false,
        "tfs": feed::TF_LIST.iter().map(|s| s.to_string()).collect::<Vec<_>>(),
        "bars": bars,
    }))).into_response()
}

async fn snapshot_ep(State(app): State<SharedApp>, Query(q): Query<HashMap<String, String>>) -> Response {
    let a = app.read().await;
    let inp = SnapInputs {
        buffers: &a.buffers, cfg: &a.cfg, signals_log: &a.signals_log,
        symbol: &a.symbol, view_tf: q.get("tf").map(|s| s.as_str()).or(Some(a.entry_tf.as_str())),
        htf_tf: Some(a.cfg.htf.as_str()),
        sim: &a.sim, news: &a.news,
        sensitivity: &a.sensitivity, exit_protocol: &a.exit_protocol,
        suppressed: &a.suppressed, suppressed_count: a.suppressed_count,
        now_ms: news::now_ms(),
    };
    match snapshot::build_snapshot(&inp) {
        Some(s) => cors(Json(s)).into_response(),
        None => cors((StatusCode::SERVICE_UNAVAILABLE,
                      Json(json!({"error": "buffer belum siap"})))).into_response(),
    }
}

async fn candles_ep(State(app): State<SharedApp>, Query(q): Query<HashMap<String, String>>) -> Response {
    let a = app.read().await;
    let tf = q.get("tf").cloned().unwrap_or_else(|| "5m".into());
    let limit: usize = q.get("limit").and_then(|l| l.parse().ok()).unwrap_or(1500);
    match a.buffers.get(&tf) {
        Some(buf) => {
            let st = buf.len().saturating_sub(limit);
            let rows: Vec<[f64; 6]> = buf[st..].iter()
                .map(|c| [c.time_ms as f64, c.open, c.high, c.low, c.close, c.volume])
                .collect();
            cors(Json(json!({"tf": tf, "candles": rows}))).into_response()
        }
        None => cors((StatusCode::NOT_FOUND,
                      Json(json!({"error": format!("tf '{tf}' tidak tersedia")}))))
            .into_response(),
    }
}

async fn signals_ep(State(app): State<SharedApp>) -> Response {
    let a = app.read().await;
    cors(Json(json!({"signals": a.signals_log}))).into_response()
}

async fn sim_ep(State(app): State<SharedApp>) -> Response {
    let a = app.read().await;
    cors(Json(json!({"stats": a.sim.stats(), "trades": a.sim.snapshot_list(50)})))
        .into_response()
}

async fn news_ep(State(app): State<SharedApp>) -> Response {
    let a = app.read().await;
    cors(Json(a.news.clone())).into_response()
}

async fn config_ep() -> Response {
    // ws_port 0 → frontend WS gagal 2x → otomatis polling (fallback tersedia)
    cors(Json(json!({"http_port": 8794, "ws_port": 0}))).into_response()
}

#[derive(serde::Deserialize)]
struct Settings {
    sensitivity: Option<String>,
    exit_protocol: Option<String>,
}

async fn settings_ep(State(app): State<SharedApp>, Json(body): Json<Settings>) -> Response {
    let mut a = app.write().await;
    let mut applied = vec![];
    if let Some(s) = &body.sensitivity {
        if engine::valid_sensitivity(s) {
            a.cfg = apply_mode(&a.cfg, Some(s), None);
            a.sensitivity = s.clone();
            applied.push(format!("sensitivity:{s}"));
        }
    }
    if let Some(e) = &body.exit_protocol {
        if engine::valid_exit(e) {
            a.cfg = apply_mode(&a.cfg, None, Some(e));
            a.exit_protocol = e.clone();
            applied.push(format!("exit:{e}"));
        }
    }
    println!("[settings] {applied:?}");
    cors(Json(json!({
        "applied": applied, "sensitivity": a.sensitivity,
        "exit_protocol": a.exit_protocol,
    }))).into_response()
}

async fn static_files(uri: axum::http::Uri) -> Response {
    let path = uri.path().trim_start_matches('/');
    let path = if path.is_empty() { "index.html" } else { path };
    match FRONTEND_DIR.get_file(path) {
        Some(f) => {
            let mime = match path.rsplit('.').next() {
                Some("html") => "text/html",
                Some("js") => "application/javascript",
                Some("css") => "text/css",
                Some("json") => "application/json",
                Some("svg") => "image/svg+xml",
                Some("png") => "image/png",
                Some("ico") => "image/x-icon",
                _ => "application/octet-stream",
            };
            let mut resp = ([(header::CONTENT_TYPE, mime)], f.contents().to_vec()).into_response();
            resp.headers_mut().insert(header::CACHE_CONTROL,
                                      HeaderValue::from_static("no-cache"));
            resp
        }
        None => (StatusCode::NOT_FOUND, "404").into_response(),
    }
}
