//! build_snapshot — JSON kompatibel frontend (renderSnapshot + panel).

use crate::engine::{generate_signals, Candle, Config, Signal};
use crate::godmode::*;
use crate::sim::PaperSim;
use serde_json::{json, Value};

const TF_ORDER: &[(&str, i64)] = &[
    ("1m", 1), ("3m", 2), ("5m", 5), ("15m", 15), ("30m", 30), ("1h", 60),
    ("4h", 240), ("1d", 1440),
];
fn order_of(tf: &str) -> i64 {
    TF_ORDER.iter().find(|(t, _)| *t == tf).map(|(_, o)| *o).unwrap_or(0)
}

pub struct SnapInputs<'a> {
    pub buffers: &'a std::collections::HashMap<String, Vec<Candle>>,
    pub cfg: &'a Config,
    pub signals_log: &'a [Value],
    pub symbol: &'a str,
    pub symbols: &'a [String],
    pub view_tf: Option<&'a str>,
    pub htf_tf: Option<&'a str>,
    pub sims: &'a [(String, &'a PaperSim)],
    pub news: &'a Value,
    pub sensitivity: &'a str,
    pub exit_protocol: &'a str,
    pub suppressed: &'a [Value],
    pub suppressed_count: usize,
    pub now_ms: i64,
}

pub fn build_snapshot(inp: &SnapInputs) -> Option<Value> {
    let mut tfs: Vec<&String> = inp.buffers.keys().collect();
    tfs.sort_by_key(|t| order_of(t));
    let tf_entry = match inp.view_tf {
        Some(v) if inp.buffers.contains_key(v) => v.to_string(),
        _ => tfs.first()?.to_string(),
    };
    let higher: Vec<&&String> = tfs.iter().filter(|t| order_of(t) > order_of(&tf_entry)).collect();
    let tf_htf = match inp.htf_tf {
        Some(h) if inp.buffers.contains_key(h) => h.to_string(),
        _ => higher.first().map(|x| x.to_string()).unwrap_or_else(|| tf_entry.clone()),
    };
    let df = inp.buffers.get(&tf_entry)?;
    let df_htf = inp.buffers.get(&tf_htf);
    if df.len() < 260 { return None; }

    let c_series = closes(df);
    let high_s: Vec<f64> = df.iter().map(|x| x.high).collect();
    let low_s: Vec<f64> = df.iter().map(|x| x.low).collect();
    let a = crate::indicators::atr(&high_s, &low_s, &c_series, inp.cfg.atr_len)
        .last().cloned().unwrap_or(0.0);
    let fib = fib_state(df, 120);
    let z = zones(df, a, 5, 200);
    let vf = volume_flow(df, 20);
    let zm = multi_zones(df, a, 3, 5, 300);
    let cond = market_condition(df, 60);
    let lamps = trend_lamps(df, df_htf.map(|x| x.as_slice()));
    let ts_info = two_stage(df, &zm);
    let empty: Vec<Candle> = vec![];
    let sig = generate_signals(df, inp.cfg, df_htf.unwrap_or(&empty));

    let cf = confluence_godmode(df, df_htf.unwrap_or(&empty), inp.cfg, &zm, &vf, &tf_htf);
    let choch_ok = choch_recent(df, 5, 10);
    let div_ok = rsi_divergence_recent(df, 5, 15);
    let label = classify_setup(&cf, choch_ok, div_ok);

    // ---- setup dari sinyal terakhir
    let mut setup: Option<Value> = None;
    let last_sig: Option<&Signal> = sig.last();
    if let Some(row) = last_sig {
        let risk = (row.entry - row.sl).abs().max(1e-9);
        let rr = ((row.tp2 - row.entry).abs() / risk).round() as i64;
        let fresh = row.time_ms == df.last().unwrap().time_ms;
        setup = Some(json!({
            "side": if row.dir == -1 { "SELL" } else { "BUY" },
            "time": ms_to_str(row.time_ms),
            "entry": fmt2(row.entry), "sl": fmt2(row.sl),
            "tp1": fmt2(row.tp1), "tp2": fmt2(row.tp2),
            "rr": format!("1:{rr}"), "score": row.score,
            "reason": if row.dir == 1 { "TREND+MOM+VP+PA+VOL long" } else { "TREND+MOM+VP+PA+VOL short" },
            "label": if fresh { Some(label.clone()) } else { None },
            "fresh": fresh,
        }));
    }

    let price_now = fmt2(df.last().unwrap().close);
    let fresh_score = setup.as_ref().and_then(|s| s["fresh"].as_bool()).unwrap_or(false)
        .then(|| setup.as_ref().and_then(|s| s["score"].as_i64()).unwrap_or(-1));
    let golden_state = match fresh_score {
        Some(sc) if sc >= 3 => "EXECUTE (GOLDEN)",
        Some(_) => "EXECUTE",
        None => "STANDBY",
    };
    let high_state = match fresh_score {
        Some(sc) if sc < 3 => "EXECUTE (HIGH RISK)",
        _ => "WAIT",
    };
    let golden = json!({"price": price_now, "state": golden_state, "sub": "GET READY (GOLDEN)"});
    let high_risk = json!({"price": price_now, "state": high_state,
        "sub": format!("HIGH RISK {}", if high_state.starts_with("EXECUTE") { "ON" } else { "OFF" })});

    // ---- header stats
    let r = crate::indicators::rsi(&c_series, inp.cfg.rsi_len).last().cloned().unwrap_or(f64::NAN);
    let (label_s, label_sub) = structure_label(df, 5);
    let rsi_label = if (45.0..=55.0).contains(&r) { "Neutral" }
        else if r > 55.0 { "Bullish" } else { "Bearish" };
    let ai_score = cf["score"].as_i64().unwrap_or(0);
    let stats = json!({
        "price": price_now, "rsi": r1v(r),
        "rsi_label": rsi_label, "atr": fmt2(a),
        "atr_label": "Volatilitas TF entry",
        "structure": label_s, "structure_sub": label_sub,
        "ai_score": ai_score,
        "ai_label": if ai_score >= 75 { "HIGH" } else if ai_score >= 50 { "MED" } else { "LOW" },
        "fib": fib["state"].as_str().unwrap_or("-"),
        "equilibrium": fib["equilibrium"].clone(),
    });

    // ---- heatmap semua TF
    let mut hm_rows = serde_json::Map::new();
    for key in ["Trend", "Zone", "Momentum", "CVD Flow", "Breakout"] {
        let mut arr = vec![];
        for tf in &tfs {
            let b = &inp.buffers[tf.as_str()];
            if b.len() >= 60 {
                let row = heatmap_row(b);
                arr.push(row[key].clone());
            }
        }
        hm_rows.insert(key.to_string(), json!(arr));
    }
    let heatmap = json!({"tfs": tfs.iter().map(|t| t.to_uppercase()).collect::<Vec<_>>(),
                         "rows": hm_rows});

    let fvg = fvg_zones(df, 80, 5);
    let obs = order_blocks(df, 5, 4, 250);
    let pdh = inp.buffers.get("1h").map(|b| pdh_pdl(b)).unwrap_or(Value::Null);

    Some(json!({
        "symbol": inp.symbol,
        "symbols": inp.symbols,
        "ts": crate::news::now_utc_str(),
        "tf_entry": tf_entry, "tf_htf": tf_htf,
        "engine_entry": inp.view_tf.unwrap_or(&tf_entry),
        "stats": stats,
        "market_condition": cond,
        "trend_lamps": lamps,
        "volume_flow": vf,
        "zones": z,
        "zones_multi": zm,
        "pdh_pdl": pdh,
        "events": events_full(df, 5, inp.cfg.sweep_lookback, 8),
        "heatmap": heatmap,
        "setup": setup, "setup_label": label,
        "golden": golden, "high_risk": high_risk,
        "confluence": {"confidence": cf["score"], "action": cf["action"],
                       "checks": cf["checks"], "bonus": cf["bonus"]},
        "two_stage": ts_info,
        "session": session_info(inp.now_ms),
        "ai_reasoning": ai_reasoning(df, &zm, &ts_info, &cf),
        "forecast": forecast(df, None),
        "fvg": fvg,
        "order_blocks": obs,
        "overlays": {
            "eq": fib["eq"].clone(), "gp": fib["gp"].clone(), "ote": fib["ote"].clone(),
            "range_high": fib["high"].clone(), "range_low": fib["low"].clone(),
            "supply": z["supply"]["top"].as_f64().map(|t| vec![t, z["supply"]["bottom"].as_f64().unwrap_or(0.0)]),
            "demand": z["demand"]["top"].as_f64().map(|t| vec![t, z["demand"]["bottom"].as_f64().unwrap_or(0.0)]),
            "zones_multi": zm,
        },
        "signals_log": inp.signals_log,
        "sim": {"stats": combined_stats(inp.sims), "trades": merged_trades(inp.sims, 50)},
        "news": inp.news,
        "mode": {"sensitivity": inp.sensitivity, "exit_protocol": inp.exit_protocol},
        "filters": {"golden": true, "high_risk": false,
                    "suppressed": inp.suppressed, "suppressed_count": inp.suppressed_count},
    }))
}

fn r1v(v: f64) -> f64 { (v * 10.0).round() / 10.0 }

/// Statistik gabungan multi-simbol (tiap simbol = akun 10k independen).
pub fn combined_stats(sims: &[(String, &PaperSim)]) -> Value {
    let mut equity = 0.0f64;
    let mut start = 0.0f64;
    let mut trades = 0usize;
    let mut open = 0usize;
    let mut wins = 0usize;
    let mut losses = 0usize;
    let mut gp = 0.0f64;
    let mut gl = 0.0f64;
    let mut dd_min = 0.0f64;
    let mut avg_r_acc = 0.0f64;
    for (_, s) in sims {
        let st = s.stats();
        equity += st["equity"].as_f64().unwrap_or(0.0);
        start += s.start_equity;
        trades += st["trades"].as_u64().unwrap_or(0) as usize;
        open += st["open"].as_u64().unwrap_or(0) as usize;
        wins += st["wins"].as_u64().unwrap_or(0) as usize;
        losses += st["losses"].as_u64().unwrap_or(0) as usize;
        let (g, l, _, _, cnt) = s.gp_gl();
        gp += g;
        gl += l;
        let ar = st["avg_r"].as_f64().unwrap_or(0.0);
        avg_r_acc += ar * cnt as f64;
        dd_min = dd_min.min(st["max_dd"].as_f64().unwrap_or(0.0));
    }
    let wr = if trades > 0 { wins as f64 / trades as f64 * 100.0 } else { 0.0 };
    let pf = if gl > 0.0 { gp / gl } else { f64::INFINITY };
    let avg_r = if trades > 0 { avg_r_acc / trades as f64 } else { 0.0 };
    let ret = (equity / start - 1.0) * 100.0;
    json!({
        "equity": crate::sim::r2(equity), "start": crate::sim::r2(start),
        "trades": trades, "open": open, "wins": wins, "losses": losses,
        "winrate": crate::sim::r1(wr), "avg_r": crate::sim::r3(avg_r),
        "profit_factor": if pf.is_infinite() { json!("∞") } else { json!(crate::sim::r2(pf)) },
        "max_dd": crate::sim::r2(dd_min), "return_pct": crate::sim::r2(ret),
    })
}

/// Gabungkan daftar trade semua simbol, terbaru dulu.
pub fn merged_trades(sims: &[(String, &PaperSim)], n: usize) -> Value {
    let mut all: Vec<Value> = vec![];
    for (_, s) in sims {
        if let Some(arr) = s.snapshot_list(n).as_array() {
            all.extend(arr.clone());
        }
    }
    all.sort_by(|a, b| b["time"].as_str().cmp(&a["time"].as_str()));
    json!(all.into_iter().take(n).collect::<Vec<_>>())
}
