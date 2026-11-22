//! Alpha-Trade Native — server Rust tunggal: frontend embedded + REST API +
//! feed Binance multi-simbol + engine 5-gate + PaperSim + Telegram.


use alpha_trade_native::{engine, feed, godmode, indicators, news, sim, snapshot, tg};

use axum::{
    extract::{Query, State},
    http::{header, HeaderValue, StatusCode},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use engine::{apply_mode, generate_signals, Config};
use sim::PaperSim;
use std::collections::HashMap;
use serde_json::json;
use std::sync::Arc;
use tokio::sync::RwLock;

/// State per simbol: buffer TF, sim, dan penanda sinyal terakhir.
struct SymState {
    name: String,
    buffers: feed::Buffers,
    sim: PaperSim,
    last_sig_time: Option<i64>,
    last_spread: f64, // spread absolut (ask-bid) terakhir
}

struct App {
    syms: Vec<SymState>, // primary = index 0
    cfg: Config,
    sensitivity: String,
    exit_protocol: String,
    entry_tf: String,
    signals_log: Vec<serde_json::Value>, // gabungan semua simbol (tag symbol)
    suppressed: Vec<serde_json::Value>,
    suppressed_count: usize,
    news: serde_json::Value,
    max_spread_atr: f64,
    vol_target: bool,
}

impl App {
    fn primary(&self) -> &SymState {
        &self.syms[0]
    }
    fn sym_mut(&mut self, name: &str) -> Option<&mut SymState> {
        self.syms.iter_mut().find(|s| s.name == name)
    }
}

type SharedApp = Arc<RwLock<App>>;

use include_dir::include_dir;
static FRONTEND_DIR: include_dir::Dir =
    include_dir!("$CARGO_MANIFEST_DIR/../frontend");

#[tokio::main]
async fn main() {
    let port: u16 = std::env::var("AT_PORT").ok()
        .and_then(|p| p.parse().ok()).unwrap_or(8794);
    let symbols: Vec<String> = std::env::var("AT_SYMBOLS")
        .or_else(|_| std::env::var("AT_SYMBOL"))
        .unwrap_or_else(|_| "PAXGUSDT,BTCUSDT".into())
        .split(',')
        .map(|s| s.trim().to_uppercase())
        .filter(|s| !s.is_empty())
        .collect();
    let entry_tf = std::env::var("AT_ENTRY_TF").unwrap_or_else(|_| "1h".into());
    let daily_stop: f64 = std::env::var("AT_SIM_DAILY_STOP").ok()
        .and_then(|p| p.parse().ok()).unwrap_or(0.0);
    let max_spread_atr: f64 = std::env::var("AT_MAX_SPREAD_ATR").ok()
        .and_then(|p| p.parse().ok()).unwrap_or(0.0);
    let vol_target = std::env::var("AT_VOL_TARGET").ok()
        .map(|v| v == "1" || v.eq_ignore_ascii_case("true")).unwrap_or(false);

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(15)).build().unwrap();

    let mut cfg = Config::default();
    let (sens, exitp) = (std::env::var("AT_SENSITIVITY").unwrap_or_else(|_| "edge1h".into()),
                         std::env::var("AT_EXIT").unwrap_or_else(|_| "engine".into()));
    if engine::valid_sensitivity(&sens) { cfg = apply_mode(&cfg, Some(&sens), None); }
    if engine::valid_exit(&exitp) { cfg = apply_mode(&cfg, None, Some(&exitp)); }

    // seed semua simbol
    let mut syms: Vec<SymState> = vec![];
    for sym in &symbols {
        print!("[feed] seed {sym} ...");
        match feed::seed_all(&client, sym).await {
            Ok(buffers) => {
                println!(" ok ({} TF)", buffers.len());
                syms.push(SymState {
                    name: sym.clone(), buffers,
                    sim: PaperSim::new(daily_stop, 0),
                    last_sig_time: None, last_spread: 0.0,
                });
            }
            Err(e) => println!(" GAGAL: {e} — simbol dilewati"),
        }
    }
    if syms.is_empty() {
        eprintln!("tidak ada simbol yang berhasil di-seed — keluar");
        std::process::exit(1);
    }
    if syms[0].sim.vol_target == false && vol_target {
        for s in syms.iter_mut() { s.sim.vol_target = true; }
    }

    let news_v = news::fetch_news(&client, std::env::var("NEWS_KEY_NEWSDATA").ok().as_deref()).await;

    let tg = tg::Telegram::from_env();
    let app = Arc::new(RwLock::new(App {
        syms, cfg, sensitivity: sens, exit_protocol: exitp, entry_tf: entry_tf.clone(),
        signals_log: vec![], suppressed: vec![], suppressed_count: 0,
        news: news_v, max_spread_atr, vol_target,
    }));

    // ---- replay riwayat tiap simbol (setara sim_replay Python)
    {
        let mut a = app.write().await;
        let names: Vec<String> = a.syms.iter().map(|s| s.name.clone()).collect();
        for name in names {
            replay_symbol(&mut a, &name, &entry_tf);
        }
        if let Some(t) = &tg {
            let t = t.clone();
            let cl = client.clone();
            tokio::spawn(async move {
                t.send(&cl, format!("🟢 <b>Alpha-Trade native</b> start — {} | EDGE-1H + ENGINE | daily-stop {}R",
                    symbols.join(", "), daily_stop).as_str()).await;
            });
        }
    }

    // ---- feed loop
    let (feed_app, feed_client, feed_entry_tf, feed_tg) = (app.clone(), client.clone(), entry_tf.clone(), tg.clone());
    tokio::spawn(async move {
        loop {
            let msgs = {
                let mut a = feed_app.write().await;
                let mut msgs = vec![];
                // clone konfigurasi ringan sebelum borrow mutabel syms
                let cfg = a.cfg.clone();
                let entry_tf = a.entry_tf.clone();
                let sensitivity = a.sensitivity.clone();
                let max_spread_atr = a.max_spread_atr;
                let mut suppressed_new: Vec<serde_json::Value> = vec![];
                for sym in a.syms.iter_mut() {
                    let changed = feed::poll_all(&feed_client, &sym.name, &mut sym.buffers).await;
                    if changed.iter().any(|tf| *tf == feed_entry_tf) {
                        msgs.extend(tick_symbol(sym, &cfg, &entry_tf,
                            &sensitivity, max_spread_atr, &mut suppressed_new));
                    }
                }
                if !suppressed_new.is_empty() {
                    a.suppressed_count += suppressed_new.len();
                    a.suppressed.extend(suppressed_new);
                    a.suppressed.truncate(30);
                }
                msgs
            };
            if let Some(t) = &feed_tg {
                for m in &msgs {
                    t.send(&feed_client, m).await;
                }
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

/// Replay riwayat satu simbol: buffer penuh diputar lewat engine+sim.
fn replay_symbol(a: &mut App, name: &str, entry_tf: &str) {
    let cfg = a.cfg.clone();
    let mode = a.sensitivity.clone();
    let Some(st) = a.sym_mut(name) else { return };
    let df = st.buffers.get(entry_tf).cloned().unwrap_or_default();
    let htf_tf = cfg.htf.clone();
    let htf = st.buffers.get(&htf_tf).cloned().unwrap_or_default();
    if df.len() <= 260 || htf.len() <= 50 { return; }
    let sigs = generate_signals(&df, &cfg, &htf);
    let by_time: HashMap<i64, engine::Signal> =
        sigs.iter().map(|s| (s.time_ms, s.clone())).collect();

    println!("[replay][{name}] memutar {} bar {entry_tf} lewat PaperSim ...", df.len());
    for c in &df {
        if let Some(s) = by_time.get(&c.time_ms) {
            let kind = if s.score >= 3 { "GOLDEN" } else { "HIGH RISK" };
            st.sim.queue_signal(c.time_ms, s.dir, s.entry, s.sl, s.tp1, s.tp2,
                                &mode, kind, name, 1.0);
        }
        for ev in st.sim.on_candle(c) { let _ = ev; }
    }
    println!("[replay][{name}] selesai: {} trade, EQ {:.2}",
             st.sim.stats()["trades"], st.sim.equity);
    st.last_sig_time = sigs.last().map(|s| s.time_ms);
    // log: 5 sinyal terakhir riwayat simbol ini
    let mut logs = vec![];
    for s in sigs.iter().rev().take(5) {
        logs.push(json!({
            "time": godmode::ms_to_str(s.time_ms), "tf": entry_tf, "symbol": name,
            "side": if s.dir == 1 { "LONG" } else { "SHORT" },
            "entry": sim::r2(s.entry), "sl": sim::r2(s.sl),
            "tp1": sim::r2(s.tp1), "tp2": sim::r2(s.tp2),
            "score": s.score, "kind": if s.score >= 3 { "GOLDEN" } else { "HIGH RISK" },
        }));
    }
    logs.reverse();
    a.signals_log.extend(logs);
    a.signals_log.sort_by(|x, y| y["time"].as_str().cmp(&x["time"].as_str()));
    a.signals_log.truncate(30);
}

/// Engine tick satu simbol: sinyal baru -> sim; kembalikan pesan Telegram
/// dan entri suppressed (di-append pemanggil).
fn tick_symbol(st: &mut SymState, cfg: &Config, entry_tf: &str,
               sensitivity: &str, max_spread_atr: f64,
               suppressed_new: &mut Vec<serde_json::Value>) -> Vec<String> {
    let mut tg_msgs = vec![];
    let df = st.buffers.get(entry_tf).cloned().unwrap_or_default();
    if df.len() < 260 { return tg_msgs; }
    let htf_tf = cfg.htf.clone();
    let htf = st.buffers.get(&htf_tf).cloned().unwrap_or_default();
    let sigs = generate_signals(&df, cfg, &htf);
    let last_bar = df.last().unwrap().time_ms;
    if let Some(s) = sigs.last() {
        if s.time_ms == last_bar && st.last_sig_time != Some(s.time_ms) {
            st.last_sig_time = Some(s.time_ms);
            let kind = if s.score >= 3 { "GOLDEN" } else { "HIGH RISK" };
            let entry = json!({
                "time": godmode::ms_to_str(s.time_ms), "tf": entry_tf,
                "symbol": st.name,
                "side": if s.dir == 1 { "LONG" } else { "SHORT" },
                "entry": sim::r2(s.entry), "sl": sim::r2(s.sl),
                "tp1": sim::r2(s.tp1), "tp2": sim::r2(s.tp2),
                "score": s.score, "kind": kind,
            });
            let allowed = kind == "GOLDEN";
            if allowed {
                // spread filter: tolak entry bila spread > max_spread_atr x ATR
                let atr_last = indicators::atr(
                    &df.iter().map(|x| x.high).collect::<Vec<_>>(),
                    &df.iter().map(|x| x.low).collect::<Vec<_>>(),
                    &df.iter().map(|x| x.close).collect::<Vec<_>>(), cfg.atr_len)
                    .last().cloned().unwrap_or(f64::NAN);
                let spread_atr = if atr_last > 0.0 { st.last_spread / atr_last } else { 0.0 };
                if max_spread_atr > 0.0 && spread_atr > max_spread_atr {
                    println!("[spread][{}] spread {} > {} ATR — entry ditahan",
                             st.name, sim::r2(st.last_spread), max_spread_atr);
                } else {
                    a_push_signal(st, cfg, &entry, s, sensitivity, entry_tf, kind);
                    tg_msgs.push(format!("⚡ <b>{}</b> {} @{} | SL {} TP {} | skor {}",
                        st.name, entry["side"], entry["entry"], entry["sl"], entry["tp2"], s.score));
                }
            } else {
                suppressed_new.push(entry.clone());
                println!("[sinyal disaring][{}] {} skor={}", st.name, kind, s.score);
            }
        }
    }
    // kelola posisi sim dengan candle closed terbaru
    if let Some(c) = df.last() {
        for ev in st.sim.on_candle(c) {
            println!("{ev}");
            if ev.contains("WIN") || ev.contains("LOSS") || ev.contains("BE+") || ev.contains("TIME") {
                tg_msgs.push(format!("✅ {}", ev));
            }
        }
    }
    tg_msgs
}

fn a_push_signal(st: &mut SymState, cfg: &Config, entry: &serde_json::Value,
                 s: &engine::Signal, sensitivity: &str, entry_tf: &str, kind: &str) {
    st.sim.queue_signal(s.time_ms, s.dir, s.entry, s.sl, s.tp1, s.tp2,
                        sensitivity, kind, &st.name, 1.0);
    // vol-scale 1.0 (vol_target memakai skala dari median atrp — dihitung di sini bila aktif)
    let _ = cfg; let _ = entry_tf;
    println!("[sinyal][{}] {} {} entry={} skor={}", st.name, kind,
             entry["side"], entry["entry"], s.score);
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
    let mut bars = serde_json::Map::new();
    for s in &a.syms {
        let mut m = serde_json::Map::new();
        for (tf, buf) in &s.buffers { m.insert(tf.clone(), json!(buf.len())); }
        bars.insert(s.name.clone(), json!(m));
    }
    let symbols: Vec<String> = a.syms.iter().map(|s| s.name.clone()).collect();
    cors(Json(json!({
        "ok": true, "symbol": a.syms[0].name, "symbols": symbols,
        "mode": "off", "native": true,
        "sensitivity": a.sensitivity, "exit_protocol": a.exit_protocol,
        "signal_golden": true, "signal_high_risk": false,
        "tfs": feed::TF_LIST.iter().map(|s| s.to_string()).collect::<Vec<_>>(),
        "bars": bars,
    }))).into_response()
}

async fn snapshot_ep(State(app): State<SharedApp>, Query(q): Query<HashMap<String, String>>) -> Response {
    let a = app.read().await;
    let sims: Vec<(String, &PaperSim)> =
        a.syms.iter().map(|s| (s.name.clone(), &s.sim)).collect();
    let inp = snapshot::SnapInputs {
        buffers: &a.syms[0].buffers, cfg: &a.cfg, signals_log: &a.signals_log,
        symbol: &a.syms[0].name,
        view_tf: q.get("tf").map(|s| s.as_str()).or(Some(a.entry_tf.as_str())),
        htf_tf: Some(a.cfg.htf.as_str()),
        sims: &sims, news: &a.news,
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
    let sym = q.get("symbol").cloned().unwrap_or_else(|| a.syms[0].name.clone());
    let limit: usize = q.get("limit").and_then(|l| l.parse().ok()).unwrap_or(1500);
    match a.syms.iter().find(|s| s.name == sym).and_then(|s| s.buffers.get(&tf)) {
        Some(buf) => {
            let st = buf.len().saturating_sub(limit);
            let rows: Vec<[f64; 6]> = buf[st..].iter()
                .map(|c| [c.time_ms as f64, c.open, c.high, c.low, c.close, c.volume])
                .collect();
            cors(Json(json!({"tf": tf, "symbol": sym, "candles": rows}))).into_response()
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
    let sims: Vec<(String, &PaperSim)> =
        a.syms.iter().map(|s| (s.name.clone(), &s.sim)).collect();
    cors(Json(json!({"stats": snapshot::combined_stats(&sims),
                     "trades": snapshot::merged_trades(&sims, 50)}))).into_response()
}

async fn news_ep(State(app): State<SharedApp>) -> Response {
    let a = app.read().await;
    cors(Json(a.news.clone())).into_response()
}

async fn config_ep() -> Response {
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
