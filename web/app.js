/* ALPHA-TRADE dashboard client
   Chart: lightweight-charts v4 (vendored). Data: REST /api/* + WS broadcast. */
"use strict";

const state = {
  chart: null, series: null, candles: [], activeTf: null,
  priceLines: [], ws: null, wsBackoff: 1000, snap: null,
  toggles: { RR: true, OB: true, CVD: true, FVG: true },
};

const $ = (id) => document.getElementById(id);
const fmt = (v, d = 2) => (v === null || v === undefined || isNaN(v)) ? "--" : Number(v).toFixed(d);
const cls = (v) => String(v || "").toUpperCase();

/* ============================== CHART ============================== */
function initChart() {
  state.chart = LightweightCharts.createChart($("chart"), {
    autoSize: true,
    layout: { background: { type: "solid", color: "transparent" }, textColor: "#8b93a1", fontSize: 11, attributionLogo: false },
    grid: { vertLines: { color: "rgba(255,255,255,0.04)" }, horzLines: { color: "rgba(255,255,255,0.05)" } },
    crosshair: { mode: 1 },
    rightPriceScale: { borderColor: "#1f2630" },
    timeScale: { borderColor: "#1f2630", timeVisible: true, secondsVisible: false },
  });
  state.series = state.chart.addSeries(LightweightCharts.CandlestickSeries, {
    upColor: "#16c784", downColor: "#ea3943", borderVisible: false,
    wickUpColor: "#16c784", wickDownColor: "#ea3943",
  });
}

async function loadCandles(tf) {
  const r = await fetch(`/api/candles?tf=${tf}&limit=1500`);
  if (!r.ok) { console.warn("candles", tf, "tidak tersedia"); return false; }
  const j = await r.json();
  state.candles = j.candles;
  state.series.setData(state.candles.map(c => ({
    time: c[0] / 1000, open: c[1], high: c[2], low: c[3], close: c[4],
  })));
  state.activeTf = tf;
  document.querySelectorAll(".tf-btn").forEach(b => b.classList.toggle("on", b.dataset.tf === tf));
  $("active-tf").textContent = tf.toUpperCase();
  fitBars();
  applyOverlays(state.snap, tf);
  return true;
}

function fitBars() {
  const n = state.candles.length;
  const vis = currentBars();
  if (!vis || vis >= n) { state.chart.timeScale().fitContent(); return; }
  state.chart.timeScale().setVisibleLogicalRange({ from: n - vis, to: n + 3 });
}
let barCount = 80;
function currentBars() { return barCount || 0; }

/* ============================== OVERLAYS ============================== */
function clearLines() {
  state.priceLines.forEach(l => { try { state.series.removePriceLine(l); } catch (e) {} });
  state.priceLines = [];
}
function line(price, { color = "#7c8694", title = "", style = 2, width = 1 } = {}) {
  if (price === null || price === undefined || isNaN(price)) return;
  state.priceLines.push(state.series.createPriceLine({
    price, color, lineWidth: width, lineStyle: style,
    axisLabelVisible: true, title,
  }));
}
function applyOverlays(snap, tf) {
  clearLines();
  if (!snap) { setMarkers([]); return; }
  const entryTf = (snap.tf_entry || "").toUpperCase();
  const isEntry = !tf || tf.toUpperCase() === entryTf;
  const o = snap.overlays || {};
  if (state.toggles.OB) {
    if (o.supply) { line(o.supply[0], { color: "#ea3943", title: "SUPPLY", style: 2 }); line(o.supply[1], { color: "#ea3943", title: "ENTRY SELL", style: 2 }); }
    if (o.demand) { line(o.demand[0], { color: "#16c784", title: "ENTRY BUY", style: 2 }); line(o.demand[1], { color: "#16c784", title: "SUPPORT", style: 2 }); }
  }
  line(o.eq, { color: "#d9b64a", title: "EQ", style: 0, width: 1 });
  if (state.toggles.FVG) {
    line(o.gp, { color: "#2dd4bf", title: "Fib 0.618 (GP)", style: 2 });
    line(o.ote, { color: "#2dd4bf", title: "Fib 0.786 (OTE)", style: 2 });
  }
  const su = snap.setup;
  if (su && isEntry && state.toggles.RR) {
    line(su.entry, { color: "#e6e9ee", title: "Entry", style: 0, width: 2 });
    line(su.sl, { color: "#ea3943", title: "SL", style: 0, width: 1 });
    line(su.tp1, { color: "#16c784", title: "TP 1", style: 2 });
    line(su.tp2, { color: "#16c784", title: "TP 2", style: 2 });
  }
  /* markers: CHoCH + sinyal */
  const markers = [];
  (snap.events || []).forEach(ev => {
    if (ev.tag !== "CHoCH") return;
    const t = new Date(ev.time).getTime() / 1000;
    markers.push({
      time: t, position: ev.dir === "UP" ? "belowBar" : "aboveBar",
      color: ev.dir === "UP" ? "#16c784" : "#ea3943",
      shape: ev.dir === "UP" ? "arrowUp" : "arrowDown", text: "CHoCH",
    });
  });
  (snap.signals_log || []).forEach(s => {
    const t = new Date(s.time).getTime() / 1000;
    markers.push({
      time: t, position: s.side === "LONG" ? "belowBar" : "aboveBar",
      color: s.side === "LONG" ? "#d9b64a" : "#d9b64a",
      shape: s.side === "LONG" ? "arrowUp" : "arrowDown",
      text: `${s.side} ${fmt(s.entry)}`,
    });
  });
  markers.sort((a, b) => a.time - b.time);
  setMarkers(isEntry ? markers : []);
}

/* v5: marker lewat helper createSeriesMarkers (bukan series.setMarkers) */
function setMarkers(markers) {
  if (!state.markersApi) {
    state.markersApi = LightweightCharts.createSeriesMarkers(state.series, []);
  }
  state.markersApi.setMarkers(markers);
}

/* ============================== PANELS ============================== */
function heatClass(v) {
  v = cls(v);
  if (["BULL", "BUY", "CH-BULL", "DEMAND"].includes(v)) return "v-chip bull";
  if (["BEAR", "SELL", "CH-BEAR", "SUPPLY"].includes(v)) return "v-chip bear";
  if (["EXP", "RANG", "RANGING"].includes(v)) return "v-chip gold";
  return "v-chip neut";
}

function renderSnapshot(snap) {
  if (!snap) return;
  state.snap = snap;
  const st = snap.stats;

  $("st-price").textContent = fmt(st.price);
  $("st-symbol").textContent = `${snap.symbol} Live${snap.demo ? " — DEMO" : ""}`;
  $("st-rsi").textContent = fmt(st.rsi, 1);
  $("st-rsi-sub").textContent = st.rsi_label;
  $("st-atr").textContent = fmt(st.atr);
  $("st-structure").textContent = st.structure;
  $("st-structure-sub").textContent = st.structure_sub;
  $("st-ai").innerHTML = `${st.ai_score}<small>/100</small>`;
  $("st-ai-sub").textContent = st.ai_label;
  $("st-fib").textContent = st.fib;
  $("st-fib-sub").textContent = `Equilibrium: ${fmt(st.equilibrium)}`;

  $("chart-title").textContent = `${(snap.tf_entry || "").toUpperCase()} INSTITUTIONAL MATRIX — ${snap.symbol}`;
  $("chart-structure").textContent = st.structure;

  /* golden / high risk */
  $("golden-price").textContent = fmt(snap.golden.price);
  $("golden-state").textContent = snap.golden.state;
  $("highrisk-price").textContent = fmt(snap.high_risk.price);
  $("highrisk-state").textContent = snap.high_risk.state;
  $("highrisk-sub").textContent = snap.high_risk.sub;

  /* confluence */
  const cf = snap.confluence;
  $("conf-level").textContent = cf.confidence >= 75 ? "HIGH" : cf.confidence >= 50 ? "MED" : "LOW";
  $("conf-val").textContent = cf.confidence;
  $("gauge-fg").style.strokeDashoffset = 214 - 214 * (cf.confidence / 100);
  $("conf-high").textContent = cf.confidence >= 75 ? "HIGH" : cf.confidence >= 50 ? "MEDIUM" : "LOW";
  $("conf-action").textContent = cf.action;
  $("conf-checks").innerHTML = cf.checks.map(ch => `
    <div class="conf-row ${ch.ok ? "ok" : "no"}">
      <span class="dim-text">${ch.label}</span>
      <span class="val">${ch.val}</span>
      <span class="pts">+${ch.pts}</span>
    </div>`).join("");
  const su = snap.setup;
  const ts2 = snap.two_stage;
  $("setup-note").textContent = su
    ? `SETUP ${su.side} DETECTED!${su.label ? " [" + su.label + "]" : ""} ${su.reason} — skor ${su.score}${su.fresh ? " (candle terakhir)" : ""}`
    : (ts2 ? `[${ts2.label}] ${ts2.detail}` : "Menunggu konfirmasi 5 gate…");

  /* lampu konfluensi 4 lapis */
  const lamps = snap.trend_lamps || {};
  $("lamps").innerHTML = Object.entries(lamps).map(([k, v]) =>
    `<span class="lamp ${v === "UP" ? "up" : v === "DOWN" ? "down" : ""}">${k}: ${v}</span>`).join("");

  /* reasoning SITUASI / KARENA / MAKA */
  const ar = snap.ai_reasoning;
  $("ai-reason").innerHTML = ar
    ? `<b>SITUASI</b> ${ar.situasi}<br><b>KARENA</b> ${ar.karena}<br><b>MAKA</b> ${ar.maka}`
    : "";

  /* chip sesi + kondisi pasar */
  const ses = snap.session;
  $("session-chip").textContent = ses ? `SESI: ${ses.session.toUpperCase()} (${ses.volatility.toUpperCase()})` : "SESI: --";
  $("events-state").textContent = snap.market_condition || st.structure;

  /* volume flow */
  const vf = snap.volume_flow;
  $("vf-absorption").textContent = "⚡ " + vf.absorption;
  $("vf-buy").textContent = vf.buy;
  $("vf-sell").textContent = vf.sell;
  $("vf-cvd").textContent = (vf.net_cvd >= 0 ? "+" : "") + vf.net_cvd;
  $("vf-buyers-bar").style.width = vf.buyers_pct + "%";
  $("vf-buyers").textContent = vf.buyers_pct + "%";
  $("vf-sellers").textContent = vf.sellers_pct + "%";

  /* zone map */
  const z = snap.zones;
  if (z.supply) {
    $("sup-top").textContent = fmt(z.supply.top);
    $("sup-bottom").textContent = fmt(z.supply.bottom);
    $("sup-strength").textContent = `Strength: ${z.supply.strength} — CVD ${z.supply.cvd >= 0 ? "+" : ""}${z.supply.cvd}`;
    $("sup-status").textContent = z.supply.status;
    $("zone-status").textContent = z.supply.status.includes("IN ZONE") || z.supply.status.includes("AT") ? z.supply.status : "AT SUPPLY";
  }
  if (z.demand) {
    $("dem-top").textContent = fmt(z.demand.top);
    $("dem-bottom").textContent = fmt(z.demand.bottom);
    $("dem-strength").textContent = `Strength: ${z.demand.strength} — CVD ${z.demand.cvd >= 0 ? "+" : ""}${z.demand.cvd}`;
    $("dem-status").textContent = z.demand.status;
  }

  /* zona multi-level S1-S3 / D1-D3 */
  const zml = snap.zones_multi;
  if (zml) {
    const pill = (z, cls) => `<span class="zone-pill ${cls}">${z.level}: ${fmt(z.bottom)}–${fmt(z.top)} (${z.dist_pts >= 0 ? "+" : ""}${fmt(z.dist_pts, 1)}p)</span>`;
    $("zone-list").innerHTML =
      zml.supply.map(z => pill(z, "sup")).join("") + zml.demand.map(z => pill(z, "dem")).join("");
  }

  /* events */
  $("events-list").innerHTML = (snap.events || []).map(ev => `
    <div class="ev-row">
      <span class="ev-tag">${ev.tag}</span>
      <span class="ev-price">${fmt(ev.price)}</span>
      <span class="ev-desc">${ev.desc}</span>
      <span class="ev-dir ${ev.dir === "UP" ? "up" : "down"}">${ev.dir === "UP" ? "▲ UP" : "▼ DOWN"}</span>
    </div>`).join("") || `<div class="dim-text">— belum ada event</div>`;

  /* heatmap */
  const hm = snap.heatmap;
  hm.tfs.slice(0, 4).forEach((tf, i) => { $(`hm-tf${i}`).textContent = tf; });
  $("hm-body").innerHTML = Object.entries(hm.rows).map(([k, vals]) => `
    <tr><td>${k.toUpperCase()}</td>${vals.slice(0, 4).map(v => `<td><span class="${heatClass(v)}">${v}</span></td>`).join("")}</tr>`).join("");
  const trendCols = hm.rows.Trend || [];
  const bias = trendCols[trendCols.length - 1];
  $("hm-bias").textContent = bias === "BEAR" ? "MONITORING - BEAR BIAS" : bias === "BULL" ? "MONITORING - BULL BIAS" : "MONITORING";

  /* setup card */
  const sc = document.querySelector(".setup-card");
  $("su-label").textContent = su && su.label ? `SETUP: ${su.label}` : (snap.setup_label ? `KANDIDAT: ${snap.setup_label}` : "");
  if (su) {
    $("setup-title").textContent = `${su.side} TRADE SETUP`;
    sc.classList.toggle("sell", su.side === "SELL");
    sc.classList.toggle("buy", su.side === "BUY");
    $("su-entry").textContent = fmt(su.entry);
    $("su-sl").textContent = fmt(su.sl);
    $("su-tp1").textContent = fmt(su.tp1);
    $("su-tp2").textContent = fmt(su.tp2);
    $("su-rr").textContent = su.rr;
  } else {
    $("setup-title").textContent = "TRADE SETUP";
    sc.classList.remove("sell", "buy");
    ["su-entry", "su-sl", "su-tp1", "su-tp2", "su-rr"].forEach(id => $(id).textContent = "--");
  }

  /* log */
  const log = snap.signals_log || [];
  $("log-list").innerHTML = log.length ? log.map(s => `
    <div class="log-row">
      <span class="dim-text">${s.time.slice(5, 16).replace("T", " ")}</span>
      <span class="${s.side === "LONG" ? "log-side long" : "log-side short"}">${s.side}</span>
      <span>${fmt(s.entry)}</span>
      <span class="dim-text">skor ${s.score}</span>
    </div>`).join("") : `<div class="dim-text">— Waiting for signals…</div>`;

  /* tombol TF yang tersedia */
  document.querySelectorAll(".tf-btn").forEach(b => {
    b.classList.toggle("disabled", !hm.tfs.map(t => t.toLowerCase()).includes(b.dataset.tf));
  });

  applyOverlays(snap, state.activeTf);
}

/* ============================== WS ============================== */
async function connectWS() {
  try {
    const cfg = await (await fetch("/api/config")).json();
    const url = `ws://${location.hostname}:${cfg.ws_port}`;
    state.ws = new WebSocket(url);
    state.ws.onopen = () => {
      state.wsBackoff = 1000;
      $("conn-status").textContent = `terhubung • ${new Date().toLocaleTimeString()}`;
    };
    state.ws.onmessage = (m) => {
      const msg = JSON.parse(m.data);
      if (msg.type === "hello" && msg.snapshot) renderSnapshot(msg.snapshot);
      if (msg.type === "snapshot") renderSnapshot(msg.data);
      if (msg.type === "candle" && msg.tf === state.activeTf) {
        const c = msg.candle;
        state.candles.push(c);
        state.series.update({ time: c[0] / 1000, open: c[1], high: c[2], low: c[3], close: c[4] });
      }
    };
    state.ws.onclose = () => {
      $("conn-status").textContent = "terputus, reconnect…";
      setTimeout(connectWS, state.wsBackoff);
      state.wsBackoff = Math.min(state.wsBackoff * 2, 30000);
    };
  } catch (e) {
    $("conn-status").textContent = "gagal konek WS: " + e.message;
    setTimeout(connectWS, 5000);
  }
}

/* ============================== EVENTS UI ============================== */
function bindUI() {
  document.querySelectorAll(".tf-btn").forEach(b => b.addEventListener("click", () => {
    if (b.classList.contains("disabled")) return;
    loadCandles(b.dataset.tf);
  }));
  document.querySelectorAll(".bars-btn").forEach(b => b.addEventListener("click", () => {
    document.querySelectorAll(".bars-btn").forEach(x => x.classList.remove("on"));
    b.classList.add("on");
    barCount = parseInt(b.dataset.bars, 10);
    fitBars();
  }));
  document.querySelectorAll(".toggle").forEach(b => b.addEventListener("click", () => {
    b.classList.toggle("on");
    const name = b.dataset.toggle;
    state.toggles[name] = b.classList.contains("on");
    b.textContent = b.textContent.replace(/(ON|OFF)$/, state.toggles[name] ? "ON" : "OFF");
    applyOverlays(state.snap, state.activeTf);
  }));
  $("btn-sync").addEventListener("click", async () => {
    const snap = await (await fetch("/api/snapshot")).json();
    renderSnapshot(snap);
    if (state.activeTf) loadCandles(state.activeTf);
  });
  $("btn-clear-log").addEventListener("click", () => {
    $("log-list").innerHTML = `<div class="dim-text">— Waiting for signals…</div>`;
  });
  $("mts-offset").addEventListener("input", () => $("mts-val").textContent = Number($("mts-offset").value || 0).toFixed(2));
  setInterval(() => {
    const now = new Date();
    $("sync-time").textContent = now.toTimeString().slice(0, 8);
  }, 1000);
}

/* ============================== BOOT ============================== */
async function boot() {
  initChart();
  bindUI();
  try {
    const snap = await (await fetch("/api/snapshot")).json();
    if (!snap.error) {
      renderSnapshot(snap);
      const tf = snap.tf_entry || "15m";
      await loadCandles(tf);
    }
  } catch (e) {
    console.warn("snapshot awal gagal:", e);
  }
  connectWS();
  /* fallback polling kalau WS mati */
  setInterval(async () => {
    if (state.ws && state.ws.readyState === 1) return;
    try { renderSnapshot(await (await fetch("/api/snapshot")).json()); } catch (e) {}
  }, 15000);
}
boot();
