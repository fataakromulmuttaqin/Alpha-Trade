/* ALPHA-TRADE dashboard client v5.0
   Chart: lightweight-charts v5 (vendored). Data: REST /api/* + WS broadcast.
   Toggle chart: OB (order blocks + zona S/D), FVG (gap 3-candle), CVD (garis
   delta volume), RR (Entry/SL/TP setup), LIMIT (level S1-S3/D1-D3),
   FORECAST (proyeksi ATR). ALARM/SND/NOTIF = alert sinyal. */
"use strict";

window.__bootErrs = [];
window.addEventListener("error", (e) => window.__bootErrs.push(String(e.message || e)));
window.addEventListener("unhandledrejection", (e) =>
  window.__bootErrs.push("promise: " + String(e.reason && e.reason.message || e.reason)));

const state = {
  chart: null, series: null, candles: [], activeTf: null, engineTf: null,
  snap: null, ws: null, wsBackoff: 1000,
  lines: { OB: [], FVG: [], LIMIT: [], RR: [], FORECAST: [], BASE: [], PDHL: [] },
  cvdSeries: null, markersApi: null,
  toggles: {},
  audioCtx: null, lastSigKey: null, lastStage: -1,
  barCount: 80, chartLimit: 20000,
};

const $ = (id) => document.getElementById(id);
const fmt = (v, d = 2) => (v === null || v === undefined || isNaN(v)) ? "--" : Number(v).toFixed(d);

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
  /* volume histogram — persis CandleChart.tsx Meridian (margin 0.82, warna candle) */
  state.volSeries = state.chart.addSeries(LightweightCharts.HistogramSeries, {
    priceScaleId: "vol", priceFormat: { type: "volume" },
    lastValueVisible: false, priceLineVisible: false,
  });
  state.chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  state.cvdSeries = state.chart.addSeries(LightweightCharts.LineSeries, {
    color: "#d9b64a", lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
    priceScaleId: "cvd",
  });
  state.chart.priceScale("cvd").applyOptions({ scaleMargins: { top: 0.93, bottom: 0 } });
  state.markersApi = LightweightCharts.createSeriesMarkers(state.series, []);
  setupCanvas();
}

async function loadCandles(tf, limitOverride) {
  const limit = limitOverride || state.chartLimit || 1500;
  const r = await fetch(`/api/candles?tf=${tf}&limit=${limit}`);
  if (!r.ok) { console.warn("candles", tf, "tidak tersedia"); return false; }
  const j = await r.json();
  state.candles = j.candles;
  state.series.setData(state.candles.map(c => ({
    time: c[0] / 1000, open: c[1], high: c[2], low: c[3], close: c[4],
  })));
  if (state.volSeries) {
    state.volSeries.setData(state.candles.map(c => ({
      time: c[0] / 1000, value: c[5],
      color: c[4] >= c[1] ? "rgba(34,197,94,0.38)" : "rgba(239,68,68,0.38)",
    })));
  }
  state.activeTf = tf;
  document.querySelectorAll(".tf-btn").forEach(b => b.classList.toggle("on", b.dataset.tf === tf));
  $("active-tf").textContent = tf.toUpperCase();
  updateCvdSeries();
  fitBars();
  drawOverlays();
  return true;
}

function updateCvdSeries() {
  if (!state.cvdSeries) return;
  let acc = 0;
  const rows = state.candles.map(c => {
    acc += Math.sign(c[4] - c[1]) * c[5];
    return { time: c[0] / 1000, value: acc };
  });
  state.cvdSeries.setData(rows);
}

function fitBars() {
  const n = state.candles.length;
  const vis = state.barCount;
  if (!vis || vis >= n) { state.chart.timeScale().fitContent(); return; }
  state.chart.timeScale().setVisibleLogicalRange({ from: n - vis, to: n + 3 });
}

/* ============================== OVERLAYS ============================== */
function clearLines(kind) {
  const list = kind ? [state.lines[kind]] : Object.values(state.lines);
  list.forEach(arr => {
    arr.forEach(l => { try { state.series.removePriceLine(l); } catch (e) {} });
    arr.length = 0;
  });
}
function line(kind, price, { color = "#7c8694", title = "", style = 2, width = 1 } = {}) {
  if (price === null || price === undefined || isNaN(price)) return;
  state.lines[kind].push(state.series.createPriceLine({
    price, color, lineWidth: width, lineStyle: style,
    axisLabelVisible: true, title,
  }));
}
const DASH = 2, DOT = 3, SOLID = 0;

function drawOverlays() {
  const snap = state.snap;
  clearLines();
  if (!snap) { state.markersApi.setMarkers([]); drawCanvas(); return; }
  const o = snap.overlays || {};

  // Order Blocks (toggle OB)
  if (state.toggles.OB) {
    (snap.order_blocks || []).forEach((b, i) => {
      const col = b.kind === "bull" ? "#f59e0b" : "#a78bfa";
      line("OB", b.top, { color: col, title: `OB${b.kind === "bull" ? "↑" : "↓"}${i + 1}`, style: DASH, width: 1 });
      line("OB", b.bottom, { color: col, title: `OB${b.kind === "bull" ? "↑" : "↓"}${i + 1}`, style: DASH, width: 1 });
    });
  }
  // Fair Value Gaps (toggle FVG)
  if (state.toggles.FVG) {
    (snap.fvg || []).forEach((g, i) => {
      const col = g.kind === "bull" ? "#2dd4bf" : "#f472b6";
      line("FVG", g.top, { color: col, title: `FVG${g.kind === "bull" ? "↑" : "↓"}${i + 1}`, style: DOT });
      line("FVG", g.bottom, { color: col, title: `FVG${g.kind === "bull" ? "↑" : "↓"}${i + 1}`, style: DOT });
    });
  }
  // level multi S1-S3 / D1-D3 (toggle LIMIT)
  if (state.toggles.LIMIT && snap.zones_multi) {
    snap.zones_multi.supply.forEach(z => {
      line("LIMIT", z.top, { color: "rgba(234,57,67,.55)", title: z.level, style: DOT });
      line("LIMIT", z.bottom, { color: "rgba(234,57,67,.55)", title: z.level, style: DOT });
    });
    snap.zones_multi.demand.forEach(z => {
      line("LIMIT", z.top, { color: "rgba(22,199,132,.55)", title: z.level, style: DOT });
      line("LIMIT", z.bottom, { color: "rgba(22,199,132,.55)", title: z.level, style: DOT });
    });
  }
  // fib konfluensi tetap (label di-axis matikan — digambar di canvas ala GODMODE)
  line("BASE", o.eq, { color: "#d9b64a", title: "", style: SOLID, width: 1 });
  line("BASE", o.gp, { color: "rgba(217,182,74,.7)", title: "", style: DOT });
  line("BASE", o.ote, { color: "rgba(217,182,74,.7)", title: "", style: DOT });

  // setup RR (toggle RR): pakai setup aktif; kalau tidak ada, level dari
  // sinyal terakhir di log — supaya toggle selalu punya efek terlihat.
  const su = snap.setup;
  const isEngineTf = !state.engineTf || state.activeTf === state.engineTf;
  const lastSig = !su ? (snap.signals_log || [])[0] : null;
  const rrSrc = (su && isEngineTf) ? su : (lastSig && isEngineTf ? lastSig : null);
  if (rrSrc && state.toggles.RR) {
    const tag = su ? "" : " (last)";
    line("RR", rrSrc.entry, { color: "#e6e9ee", title: "Entry" + tag, style: SOLID, width: 2 });
    line("RR", rrSrc.sl, { color: "#ea3943", title: "SL" + tag, style: SOLID, width: 1 });
    line("RR", rrSrc.tp1, { color: "#16c784", title: "TP 1" + tag, style: DASH });
    line("RR", rrSrc.tp2, { color: "#16c784", title: "TP 2" + tag, style: DASH });
  }
  // forecast (chip)
  if (state.toggles.FORECAST && snap.forecast && snap.forecast.direction !== "NEUTRAL") {
    const fc = snap.forecast;
    line("FORECAST", fc.sl, { color: "#ea3943", title: "FC SL", style: DOT });
    line("FORECAST", fc.tp1, { color: "#8b93a1", title: "FC TP1", style: DOT });
    line("FORECAST", fc.tp2, { color: "#8b93a1", title: "FC TP2", style: DOT });
    line("FORECAST", fc.tp3, { color: "#8b93a1", title: "FC TP3", style: DOT });
  }
  // Previous Day High / Low (toggle PDHL) — dari smc.previous_high_low
  if (state.toggles.PDHL && snap.pdh_pdl) {
    const pp = snap.pdh_pdl;
    line("PDHL", pp.pdh, { color: pp.broken_high ? "rgba(22,199,132,.45)" : "#16c784",
      title: pp.broken_high ? "PDH ✓" : "PDH", style: DASH, width: 1 });
    line("PDHL", pp.pdl, { color: pp.broken_low ? "rgba(234,57,67,.45)" : "#ea3943",
      title: pp.broken_low ? "PDL ✓" : "PDL", style: DASH, width: 1 });
  }
  /* garis CHoCH: Minor (lama) + Major (terbaru) */
  const chochEvents = (snap.events || []).filter(e => e.tag === "CHOCH");
  chochEvents.forEach((ev, i) => {
    const isMajor = i === chochEvents.length - 1;
    line("BASE", ev.price, { color: isMajor ? "rgba(234,57,67,.9)" : "rgba(234,57,67,.55)",
      title: "", style: DASH, width: isMajor ? 2 : 1 });
  });
  /* markers: sinyal (CHoCH sudah sebagai garis) + berita (maks 3, toggle NEWS) */
  const markers = [];
  if (isEngineTf) {
    (snap.signals_log || []).forEach(s => {
      const t = new Date(s.time).getTime() / 1000;
      markers.push({
        time: t, position: s.side === "LONG" ? "belowBar" : "aboveBar",
        color: "#d9b64a",
        shape: s.side === "LONG" ? "arrowUp" : "arrowDown",
        text: `${s.side === "LONG" ? "BUY" : "SELL"}`,
      });
    });
    /* marker berita: 3 terbaru, dipetakan ke candle yang memuat waktunya */
    if (state.toggles.NEWS) {
      const news = [...(snap.news && snap.news.items || [])]
        .sort((a, b) => new Date(b.time) - new Date(a.time)).slice(0, 3);
      const times = state.candles.map(c => c[0] / 1000);
      news.forEach(n => {
        const nt = new Date(n.time).getTime() / 1000;
        let ct = times.find(ct => ct >= nt);              // candle pertama >= waktu berita
        if (ct === undefined) ct = times[times.length - 1];
        if (ct === undefined) return;
        markers.push({
          time: ct, position: "aboveBar", color: "#2dd4bf", shape: "circle",
          text: `📰 ${n.verified ? "✓" : ""}${n.source}`,
        });
      });
    }
  }
  markers.sort((a, b) => a.time - b.time);
  state.markersApi.setMarkers(markers);
  drawCanvas();
}

/* ---------- canvas overlay ala GODMODE: band zona + kotak nilai + label ---------- */
function setupCanvas() {
  const wrap = $("chart-wrap"), cv = $("overlay");
  if (!wrap || !cv) return;
  const sync = () => {
    if (cv.width !== wrap.clientWidth || cv.height !== wrap.clientHeight) {
      cv.width = wrap.clientWidth; cv.height = wrap.clientHeight;
    }
    drawCanvas();
  };
  new ResizeObserver(sync).observe(wrap);
  if (state.chart) state.chart.timeScale().subscribeVisibleLogicalRangeChange(() => drawCanvas());
}

function drawCanvas() {
  const cv = $("overlay");
  const snap = state.snap;
  if (!cv || !snap) return;
  const ctx = cv.getContext("2d");
  const W = cv.width, H = cv.height;
  ctx.clearRect(0, 0, W, H);
  const s = state.series, ts = state.chart.timeScale();
  if (!s || !ts) return;
  const y = (p) => s.priceToCoordinate(p);
  const x = (tSec) => ts.timeToCoordinate(tSec);
  const timeSec = (iso) => Math.floor(new Date(String(iso).replace(" ", "T")).getTime() / 1000);

  /* band zona S/D (selalu tampil — identitas chart) */
  const zm = snap.zones_multi;
  if (zm) {
    ctx.font = "10px 'Segoe UI', sans-serif";
    const drawBand = (z, isSupply) => {
      const yT = y(z.top), yB = y(z.bottom);
      if (yT === null || yB === null) return;
      const x0Raw = x(timeSec(z.time));
      const x0 = (x0Raw === null || x0Raw < 0) ? 0 : x0Raw;   // asal di luar layar -> dari tepi kiri
      const col = isSupply ? "234,57,67" : "22,199,132";
      ctx.fillStyle = `rgba(${col},0.09)`;
      ctx.fillRect(x0, yT, W - x0, yB - yT);
      ctx.fillStyle = `rgba(${col},0.4)`;
      ctx.fillRect(x0, yT - 1, W - x0, 1);
      ctx.fillRect(x0, yB, W - x0, 1);
      ctx.fillStyle = `rgb(${col})`;
      const nama = isSupply ? "Supply" : "Demand";
      const tag = z.level.replace(/^[SD]/, "Z");
      ctx.fillText(`${nama} ${tag}  ${fmt(z.bottom)} – ${fmt(z.top)}`, x0 + 6, (yT + yB) / 2 + 3);
    };
    zm.supply.forEach(z => drawBand(z, true));
    zm.demand.forEach(z => drawBand(z, false));
  }

  /* visualisasi trade simulasi (toggle RR): band hijau ke TP2, merah ke SL,
     label "TP 2 ($x) | RR 1:y" — seperti strategy overlay GODMODE */
  if (state.toggles.RR && snap.sim && snap.sim.trades) {
    const simT = snap.sim.trades.filter(t => t.status === "OPEN" || t.exit_time).slice(0, 3);
    simT.forEach(tr => {
      const yE = y(tr.entry), yS = y(tr.sl), yT2 = y(tr.tp2);
      if (yE === null || yS === null || yT2 === null) return;
      const x0Raw = x(timeSec(tr.time));
      const x0 = (x0Raw === null || x0Raw < 0) ? 0 : x0Raw;
      const x1 = tr.status === "OPEN" ? W
        : (x(timeSec(tr.exit_time)) !== null ? x(timeSec(tr.exit_time)) : W);
      const isL = tr.side === "LONG";
      const colTP = isL ? "22,199,132" : "234,57,67";   // LONG: TP di atas (hijau)
      const colSL = isL ? "234,57,67" : "22,199,132";   // SELL: TP di bawah
      // zona profit (entry -> tp2) dan zona risiko (entry -> sl)
      const yTP1 = y(tr.tp1);
      const bandTP = isL ? [yE, yT2] : [yT2, yE];
      const bandSL = isL ? [yE, yS] : [yS, yE];
      ctx.fillStyle = `rgba(${colTP},0.13)`;
      ctx.fillRect(x0, Math.min(bandTP[0], bandTP[1]), x1 - x0, Math.abs(bandTP[1] - bandTP[0]));
      ctx.fillStyle = `rgba(${colSL},0.10)`;
      ctx.fillRect(x0, Math.min(bandSL[0], bandSL[1]), x1 - x0, Math.abs(bandSL[1] - bandSL[0]));
      // garis batas entry & tp2
      ctx.fillStyle = `rgba(${colTP},0.5)`;
      ctx.fillRect(x0, yT2 - 0.5, x1 - x0, 1);
      // label TP 2 di tepi kanan zona
      const rr = Math.abs(tr.tp2 - tr.entry) / (Math.abs(tr.entry - tr.sl) || 1e-9);
      const lbl = `${tr.status === "OPEN" ? "▶ " : ""}TP 2 ($${fmt(tr.tp2)})  |  RR 1:${rr.toFixed(1)}`;
      ctx.font = "10px 'Segoe UI', sans-serif";
      const ly = Math.min(bandTP[0], bandTP[1]) + 12;
      if (ly > 0 && ly < H) {
        ctx.fillStyle = isL ? "#16c784" : "#ea3943";
        ctx.fillText(lbl, Math.max(x0 + 6, W - 240), ly);
      }
      // label status trade di sisi kiri zona
      ctx.fillStyle = `rgba(${colTP},0.9)`;
      ctx.fillText(`${tr.side} ${tr.status}${tr.kind === "GOLDEN" ? " ★" : ""}`, x0 + 6, yE + (isL ? -4 : 12));
    });
  }

  /* label CHoCH pada garisnya (Minor / Major) */
  const choch = (snap.events || []).filter(e => e.tag === "CHOCH");
  choch.forEach((ev, i) => {
    const isMajor = i === choch.length - 1;
    const yy = y(ev.price);
    if (yy === null || yy < 0 || yy > H) return;
    const xx = x(timeSec(ev.time));
    ctx.fillStyle = isMajor ? "#ea3943" : "rgba(234,57,67,.75)";
    ctx.font = isMajor ? "bold 11px 'Segoe UI', sans-serif" : "10px 'Segoe UI', sans-serif";
    const txt = isMajor ? "Major CHoCH" : "Minor CHoCH";
    const tx = xx !== null ? xx : W * 0.5;
    ctx.fillText(txt, Math.min(tx, W - 100), yy - 5);
    if (isMajor) ctx.fillRect(Math.min(tx, W - 100), yy + 3, 78, 1);
  });
  ctx.font = "10px 'Segoe UI', sans-serif";

  /* kotak nilai kanan ala GODMODE: label di luar + kotak nilai */
  const box = (priceVal, label, bg, fg) => {
    if (priceVal === null || priceVal === undefined || isNaN(priceVal)) return;
    const yy = y(priceVal);
    if (yy === null || yy < 8 || yy > H - 8) return;
    const bw = 72, bh = 17;
    ctx.font = "10px 'Segoe UI', sans-serif";
    ctx.fillStyle = bg;
    ctx.fillRect(W - bw - 2, yy - bh / 2, bw, bh);
    ctx.fillStyle = fg;
    ctx.fillText(fmt(priceVal), W - bw + 6, yy + 3.5);
    ctx.fillStyle = bg;
    ctx.textAlign = "right";
    ctx.fillText(label, W - bw - 8, yy + 3.5);
    ctx.textAlign = "left";
  };
  box(snap.overlays?.eq, "Fib 0.5 (EQ)", "#e6e9ee", "#111");
  box(snap.overlays?.gp, "Fib 0.618 (GP)", "#d9b64a", "#111");
  box(snap.overlays?.ote, "Fib 0.786 (OTE)", "#d9b64a", "#111");
  const su = snap.setup;
  const isEngineTf2 = !state.engineTf || state.activeTf === state.engineTf;
  const lastSig2 = !su ? (snap.signals_log || [])[0] : null;
  const rrSrc2 = (su && isEngineTf2) ? su : (lastSig2 && isEngineTf2 ? lastSig2 : null);
  if (rrSrc2 && state.toggles.RR) {
    const tag2 = su ? "" : " (last)";
    box(rrSrc2.sl, "SL" + tag2, "#ea3943", "#fff");
    box(rrSrc2.entry, "Entry" + tag2, "#e6e9ee", "#111");
    box(rrSrc2.tp1, "TP 1" + tag2, "#16c784", "#fff");
    box(rrSrc2.tp2, "TP 2" + tag2, "#16c784", "#fff");
  }

  /* badge status alert di pojok kiri atas (SND/ALARM/NOTIF) */
  const badges = [];
  if (state.toggles.SND) badges.push(["🔔 SND", "#d9b64a"]);
  if (state.toggles.ALARM) badges.push(["🚨 ALARM", "#ea3943"]);
  if (state.toggles.NOTIF) badges.push(["📬 NOTIF", "#2dd4bf"]);
  if (badges.length) {
    ctx.font = "bold 10px 'Segoe UI', sans-serif";
    let bx = 8;
    badges.forEach(([txt, col]) => {
      const w = ctx.measureText(txt).width + 12;
      ctx.fillStyle = "rgba(10,12,16,0.75)";
      ctx.fillRect(bx, 8, w, 18);
      ctx.strokeStyle = col;
      ctx.lineWidth = 1;
      ctx.strokeRect(bx + 0.5, 8.5, w - 1, 17);
      ctx.fillStyle = col;
      ctx.fillText(txt, bx + 6, 21);
      bx += w + 6;
    });
  }

  /* badge MODE analisis (sensitivity + exit protocol) di kanan atas */
  const mode = snap.mode;
  if (mode && mode.sensitivity) {
    const txt = `MODE: ${mode.sensitivity.toUpperCase()} · ${(mode.exit_protocol || "").toUpperCase()}`;
    ctx.font = "bold 10px 'Segoe UI', sans-serif";
    const w = ctx.measureText(txt).width + 14;
    ctx.fillStyle = "rgba(10,12,16,0.78)";
    ctx.fillRect(W - w - 74, 8, w, 18);
    ctx.strokeStyle = "#d9b64a";
    ctx.strokeRect(W - w - 73.5, 8.5, w - 1, 17);
    ctx.fillStyle = "#d9b64a";
    ctx.fillText(txt, W - w - 66, 21);
  }
}

/* ============================== ALERT (ALARM/SND/NOTIF) ============================== */
function tone(freq, t0, dur, gainV = 0.2) {
  const ctx = state.audioCtx;
  const osc = ctx.createOscillator(), gain = ctx.createGain();
  osc.connect(gain); gain.connect(ctx.destination);
  osc.frequency.value = freq; osc.type = "sine";
  gain.gain.setValueAtTime(gainV, ctx.currentTime + t0);
  gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + t0 + dur);
  osc.start(ctx.currentTime + t0); osc.stop(ctx.currentTime + t0 + dur);
}
function beep(kind = "single") {
  try {
    state.audioCtx = state.audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    if (kind === "alarm") { tone(880, 0, .18); tone(880, .25, .18); tone(880, .5, .3); }
    else tone(880, 0, .2);
  } catch (e) {}
}
function notify(title, body) {
  try {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    new Notification(title, { body });
  } catch (e) {}
}

function detectAlerts(snap) {
  if (!snap) return;
  const log = snap.signals_log || [];
  const key = log.length ? log[0].time : null;
  const newSignal = key && key !== state.lastSigKey ? log[0] : null;
  if (state.lastSigKey !== null && newSignal) {
    const txt = `Sinyal ${newSignal.side} ${snap.symbol} ${newSignal.tf} di ${fmt(newSignal.entry)}, SL ${fmt(newSignal.sl)}, TP1 ${fmt(newSignal.tp1)}`;
    if (state.toggles.ALARM) beep("alarm");
    if (state.toggles.SND) beep("single");
    if (state.toggles.NOTIF) notify(`⚡ ${newSignal.side} ${snap.symbol} ${newSignal.tf}`, txt);
  }
  if (key) state.lastSigKey = key;

  const st2 = snap.two_stage;
  if (st2 && st2.stage !== state.lastStage) {
    if (state.lastStage !== -1) {
      if (st2.stage === 2 && state.toggles.SND) beep("single");
      if (st2.stage === 1 && state.toggles.SND) beep("single");
      if (st2.stage === 2 && state.toggles.ALARM) beep("alarm");
      if (st2.stage === 2 && state.toggles.NOTIF) notify(`⚡ ${st2.label}`, st2.detail);
    }
    state.lastStage = st2.stage;
  }
}

/* ============================== PANELS ============================== */
function heatClass(v) {
  v = String(v || "").toUpperCase();
  if (["BULL", "BUY", "CH-BULL", "DEMAND"].includes(v)) return "v-chip bull";
  if (["BEAR", "SELL", "CH-BEAR", "SUPPLY"].includes(v)) return "v-chip bear";
  if (["EXP", "RANG", "RANGING"].includes(v)) return "v-chip gold";
  return "v-chip neut";
}

function renderSnapshot(snap) {
  if (!snap) return;
  state.snap = snap;
  if (snap.engine_entry) state.engineTf = snap.engine_entry;
  const st = snap.stats;

  $("st-price").textContent = fmt(st.price);
  $("st-symbol").textContent = `${snap.symbol} Live${snap.demo ? " — DEMO" : ""}${snap.view_only ? ` (${snap.tf_entry.toUpperCase()} view)` : ""}`;
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

  /* filter sinyal (SIGNAL GOLDEN / SIGNAL HIGH RISK) — fungsional di backend */
  const flt = snap.filters || { golden: true, high_risk: false, suppressed: [], suppressed_count: 0 };
  const cg = $("chip-golden"), ch = $("chip-highrisk");
  cg.textContent = `SIGNAL GOLDEN: ${flt.golden ? "ON" : "OFF"}`;
  cg.classList.toggle("mode-gold", flt.golden);
  cg.classList.toggle("mode-dim", !flt.golden);
  ch.textContent = `SIGNAL HIGH RISK: ${flt.high_risk ? "ON" : "OFF"}`;
  ch.classList.toggle("mode-red", flt.high_risk);
  ch.classList.toggle("mode-dim", !flt.high_risk);
  /* kartu ikut filter + info sinyal yang disaring */
  if (!flt.golden) {
    $("golden-state").textContent = "OFF";
    $("golden-sub").textContent = "GOLDEN OFF — sinyal skor tinggi disaring";
  } else {
    $("golden-sub").textContent = "GET READY (GOLDEN)";
  }
  if (!flt.high_risk) {
    const n = flt.suppressed_count || 0;
    const lastSup = (flt.suppressed || [])[0];
    $("highrisk-state").textContent = "FILTERED";
    $("highrisk-sub").textContent = n
      ? `HIGH RISK OFF — ${n} disaring; terakhir ${lastSup.side} ${fmt(lastSup.entry)}`
      : "HIGH RISK OFF — sinyal skor rendah disaring";
  } else {
    $("highrisk-sub").textContent = "HIGH RISK ON — sinyal skor rendah dieksekusi";
  }

  /* confluence */
  const cf = snap.confluence;
  $("conf-level").textContent = cf.confidence >= 75 ? "HIGH" : cf.confidence >= 50 ? "MED" : "LOW";
  $("conf-val").textContent = cf.confidence;
  $("gauge-fg").style.strokeDashoffset = 214 - 214 * (cf.confidence / 100);
  $("conf-high").textContent = cf.confidence >= 75 ? "HIGH" : cf.confidence >= 50 ? "MEDIUM" : "LOW";
  $("conf-action").textContent = cf.action;
  $("conf-checks").innerHTML = (cf.checks || []).map(ch => `
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

  /* market news */
  const news = snap.news || { items: [], count: 0 };
  $("news-count").textContent = `${news.count || 0} item`;
  if (news.updated) $("news-updated").textContent = "· " + news.updated;
  const newsCard = $("news-card");
  newsCard.style.opacity = state.toggles.NEWS === false ? 0.35 : 1;
  $("news-list").innerHTML = (news.items || []).map(n => `
    <div class="news-row">
      <span class="ntime">${String(n.time).slice(5, 16).replace("T", " ")}</span>
      <span class="nsrc">${n.provider}${n.verified ? '<span class="nver">✓✓</span>' : ""}</span>
      <span class="ntitle">${n.url ? `<a href="${n.url}" target="_blank" rel="noopener">${n.title}</a>` : n.title}
        ${n.score >= 2 ? '<span class="chip chip-amber" style="padding:1px 6px;font-size:8px;margin-left:6px">IMPACT</span>' : ""}</span>
    </div>`).join("") || `<div class="dim-text" style="padding:6px 0">— belum ada berita (provider: newsdata.io → finnhub → FMP)</div>`;

  /* kartu SIMULASI TRADING (eksekusi internal otomatis) */
  const sim = snap.sim;
  if (sim && sim.stats) {
    const s = sim.stats;
    $("sim-equity").textContent = `EQ $${fmt(s.equity)} (${s.return_pct >= 0 ? "+" : ""}${s.return_pct}%)`;
    $("sim-equity").className = "chip " + (s.return_pct >= 0 ? "chip-green" : "chip-red");
    const cell = (k, v, cls = "") => `<div class="sim-stat"><div class="v ${cls}">${v}</div><div class="k">${k}</div></div>`;
    $("sim-stats").innerHTML =
      cell("TRADE", `${s.trades}<small style="color:var(--dim)">+${s.open} open</small>`) +
      cell("WINRATE", `${s.winrate}%`, s.winrate >= 50 ? "green-text" : "red-text") +
      cell("PROFIT F", s.profit_factor === null ? "--" : s.profit_factor) +
      cell("AVG R", (s.avg_r >= 0 ? "+" : "") + s.avg_r, s.avg_r >= 0 ? "green-text" : "red-text");
    const stCls = (t) => t === "WIN" ? "WIN" : t === "LOSS" ? "LOSS" : t === "OPEN" ? "OPEN" : "TIME";
    $("sim-list").innerHTML = (sim.trades || []).slice(0, 8).map(t => `
      <div class="sim-row">
        <span class="dim-text">${String(t.time).slice(5, 16).replace("T", " ")}</span>
        <span class="side ${t.side === "LONG" ? "long" : "short"}">${t.side === "LONG" ? "LONG" : "SHORT"}</span>
        <span class="dim-text">${fmt(t.entry)} → ${t.exit_price ? fmt(t.exit_price) : "…"}</span>
        <span class="r ${t.r >= 0 ? "pos" : "neg"}">${t.r >= 0 ? "+" : ""}${fmt(t.r, 2)}R</span>
        <span class="st ${stCls(t.status)}">${t.status}${t.kind === "GOLDEN" ? " ★" : ""}</span>
      </div>`).join("") || `<div class="dim-text">— belum ada trade; menunggu sinyal lolos filter</div>`;
  }

  /* heatmap (kolom dinamis sesuai jumlah TF) */
  const hm = snap.heatmap;
  const tfsShow = hm.tfs.slice(0, 6);
  $("hm-head-row").innerHTML = `<th>INDICATOR</th>` + tfsShow.map(tf => `<th>${tf}</th>`).join("");
  $("hm-body").innerHTML = Object.entries(hm.rows).map(([k, vals]) => `
    <tr><td>${k.toUpperCase()}</td>${vals.slice(0, 6).map(v => `<td><span class="${heatClass(v)}">${v}</span></td>`).join("")}</tr>`).join("");
  const trendCols = hm.rows.Trend || [];
  const bias = trendCols[trendCols.length - 1];
  $("hm-bias").textContent = bias === "BEAR" ? "MONITORING - BEAR BIAS" : bias === "BULL" ? "MONITORING - BULL BIAS" : "MONITORING";

  /* forecast chip */
  const fc = snap.forecast;
  const fcChip = $("forecast-chip");
  fcChip.classList.remove("bull", "bear");
  if (fc) {
    fcChip.textContent = `FORECAST: ${fc.direction}`;
    if (fc.direction === "BULLISH") fcChip.classList.add("bull");
    if (fc.direction === "BEARISH") fcChip.classList.add("bear");
  }

  /* setup card */
  const sc = document.querySelector(".setup-card");
  $("su-label").textContent = su && su.label ? `SETUP: ${su.label}` : (snap.setup_label ? `KANDIDAT: ${snap.setup_label}` : "");
  if (su) {
    $("setup-title").textContent = su.running ? "POSISI SIMULASI BERJALAN" : `${su.side} TRADE SETUP`;
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

  /* status koneksi setup <-> filter <-> simulasi */
  const ss = $("su-status");
  if (su && su.sim_status === "OPEN") {
    ss.textContent = `● BERJALAN DI SIMULASI ${su.sim_r !== null && su.sim_r !== undefined ? `(${su.sim_r >= 0 ? "+" : ""}${fmt(su.sim_r, 2)}R)` : ""}`;
    ss.className = "setup-status gold-text";
  } else if (su && su.sim_status && ["WIN", "LOSS", "BE+", "TIME"].includes(su.sim_status)) {
    const r = su.sim_r;
    ss.textContent = `SELESAI DI SIMULASI: ${su.sim_status} ${r !== null ? `(${r >= 0 ? "+" : ""}${fmt(r, 2)}R)` : ""}`;
    ss.className = "setup-status " + (r >= 0 ? "green-text" : "red-text");
  } else if (su && su.executable === false) {
    ss.textContent = `DISARING — tidak dieksekusi (SIGNAL ${su.kind}: OFF)`;
    ss.className = "setup-status red-text";
  } else if (su && su.fresh) {
    ss.textContent = "SIAP — entry di open candle berikutnya (simulasi + eksekusi)";
    ss.className = "setup-status gold-text";
  } else {
    ss.textContent = su ? "Menunggu candle berikutnya…" : "Belum ada sinyal di buffer";
    ss.className = "setup-status dim-text";
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

  updateRiskCalc();
  drawOverlays();
  detectAlerts(snap);
}

/* ============================== RISK CALCULATOR ============================== */
function updateRiskCalc() {
  const bal = parseFloat($("rc-balance").value) || 0;
  const risk = parseFloat($("rc-risk").value) || 0;
  const kurs = parseFloat($("rc-kurs").value) || 1;
  const cs = parseFloat($("rc-contract").value) || 1;
  const riskUSD = (bal / kurs) * (risk / 100);
  $("rc-maxloss").textContent = `Rp ${Math.round(risk * bal / 100).toLocaleString("id-ID")}`;
  const su = state.snap && state.snap.setup;
  if (su) {
    const slDist = Math.abs(su.entry - su.sl);
    const lot = slDist > 0 ? riskUSD / (slDist * cs) : 0;
    $("rc-lot").textContent = lot.toFixed(3);
  } else {
    $("rc-lot").textContent = "--";
  }
}

/* ============================== JOURNAL ============================== */
function loadJournal() {
  try { return JSON.parse(localStorage.getItem("at-journal") || '{"wins":0,"losses":0}'); }
  catch (e) { return { wins: 0, losses: 0 }; }
}
function saveJournal(j) { localStorage.setItem("at-journal", JSON.stringify(j)); renderJournal(j); }
function renderJournal(j) {
  $("jr-wins").textContent = j.wins;
  $("jr-losses").textContent = j.losses;
  const tot = j.wins + j.losses;
  $("jr-winrate").textContent = tot ? Math.round(j.wins / tot * 100) + "%" : "0%";
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
      if (msg.type === "snapshot" && !state.activeTf) renderSnapshot(msg.data);
      else if (msg.type === "snapshot" && state.snap && msg.data && msg.data.tf_entry === state.snap.tf_entry) renderSnapshot(msg.data);
      if (msg.type === "candle" && msg.tf === state.activeTf) {
        const c = msg.candle;
        state.candles.push(c);
        state.series.update({ time: c[0] / 1000, open: c[1], high: c[2], low: c[3], close: c[4] });
        if (state.volSeries) {
          state.volSeries.update({
            time: c[0] / 1000, value: c[5],
            color: c[4] >= c[1] ? "rgba(34,197,94,0.38)" : "rgba(239,68,68,0.38)",
          });
        }
        updateCvdSeries();
        drawCanvas();
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

/* ============================== TF SWITCH ============================== */
async function switchTf(tf) {
  const ok = await loadCandles(tf);
  if (!ok) return;
  try {
    const snap = await (await fetch(`/api/snapshot?tf=${tf}`)).json();
    if (!snap.error) {
      renderSnapshot(snap);          // panel + overlay khusus TF ini
      return;
    }
  } catch (e) {}
  drawOverlays();                    // fallback: overlay dari snapshot cache
}

/* ============================== EVENTS UI ============================== */
function readTogglesFromDom() {
  document.querySelectorAll(".toggle").forEach(b => {
    state.toggles[b.dataset.toggle] = b.classList.contains("on");
  });
}

function bindUI() {
  readTogglesFromDom();
  document.querySelectorAll(".tf-btn").forEach(b => b.addEventListener("click", () => {
    if (b.classList.contains("disabled")) return;
    switchTf(b.dataset.tf);
  }));
  document.querySelectorAll(".bars-btn").forEach(b => b.addEventListener("click", () => {
    document.querySelectorAll(".bars-btn").forEach(x => x.classList.remove("on"));
    b.classList.add("on");
    state.barCount = parseInt(b.dataset.bars, 10);
    // ALL: tarik seluruh history (deep history tradoor) sekali, lalu tampilkan
    if (state.barCount === 0 && (state.chartLimit || 0) < 200000) {
      state.chartLimit = 200000;
      if (state.activeTf) { loadCandles(state.activeTf); return; }
    }
    fitBars();
  }));
  document.querySelectorAll(".toggle").forEach(b => b.addEventListener("click", () => {
    b.classList.toggle("on");
    const name = b.dataset.toggle;
    state.toggles[name] = b.classList.contains("on");
    b.textContent = b.textContent.replace(/(ON|OFF)$/, state.toggles[name] ? "ON" : "OFF");
    if (name === "CVD" && state.cvdSeries) {
      state.cvdSeries.applyOptions({ visible: state.toggles.CVD });
    }
    if (name === "NEWS") {
      const nc = $("news-card");
      if (nc) nc.style.opacity = state.toggles.NEWS ? 1 : 0.35;
    }
    if (name === "SND" && state.toggles.SND) beep("single");   // konfirmasi audio aktif
    if (name === "NOTIF" && state.toggles.NOTIF && "Notification" in window
        && Notification.permission === "default") {
      Notification.requestPermission();
    }
    drawOverlays();
  }));
  $("btn-sync").addEventListener("click", async () => {
    await switchTf(state.activeTf || (state.snap && state.snap.tf_entry));
  });
  $("btn-clear-log").addEventListener("click", () => {
    $("log-list").innerHTML = `<div class="dim-text">— Waiting for signals…</div>`;
  });
  $("mts-offset").addEventListener("input", () => $("mts-val").textContent = Number($("mts-offset").value || 0).toFixed(2));
  $("forecast-chip").addEventListener("click", () => {
    state.toggles.FORECAST = !state.toggles.FORECAST;
    $("forecast-chip").classList.toggle("on", state.toggles.FORECAST);
    drawOverlays();
  });

  /* risk calculator live */
  ["rc-balance", "rc-risk", "rc-kurs", "rc-contract"].forEach(id =>
    $(id).addEventListener("input", updateRiskCalc));

  /* journal */
  renderJournal(loadJournal());
  $("jr-win").addEventListener("click", () => { const j = loadJournal(); j.wins++; saveJournal(j); });
  $("jr-loss").addEventListener("click", () => { const j = loadJournal(); j.losses++; saveJournal(j); });
  $("jr-reset").addEventListener("click", () => saveJournal({ wins: 0, losses: 0 }));

  /* selector mode -> backend */
  $("sel-sensitivity").addEventListener("change", () => postSettingsBody());
  $("sel-exit").addEventListener("change", () => postSettingsBody());

  /* chip filter sinyal — fungsional: mengubah analisis di backend */
  $("chip-golden").addEventListener("click", () => {
    const on = !$("chip-golden").textContent.includes("ON");
    postSettingsBody({ signal_golden: on });
  });
  $("chip-highrisk").addEventListener("click", () => {
    const on = $("chip-highrisk").textContent.includes("OFF");
    postSettingsBody({ signal_high_risk: on });
  });

  setInterval(() => { $("sync-time").textContent = new Date().toTimeString().slice(0, 8); }, 1000);
  loadResearch();
}

/* ---------- research report (static, dari cache/research_report.json) ---------- */
function fmtP(v) { return v == null || Number.isNaN(v) ? "--" : Number(v).toFixed(3); }
function fmtR(v) { return v == null || Number.isNaN(v) ? "--" : (v >= 0 ? "+" : "") + Number(v).toFixed(3) + "R"; }

function renderResearch(rep) {
  const sig = rep.significance || {};
  const t = sig.trend || {};
  const j = sig.joint || {};
  const sigSig = t.p_value != null && t.p_value < 0.05;
  $("rs-sig").innerHTML =
    `<div class="rs-title">SIGNIFIKANSI (trend gate)</div>` +
    `<div>Expectancy: <b>${fmtR(t.expectancy_R_real)}</b> vs baseline ${fmtR(t.baseline_mean_R)}</div>` +
    `<div>p-value: <b>${fmtP(t.p_value)}</b> ${sigSig ? "(signifikan)" : "(TIDAK signifikan)"} · trades ${t.trades_real ?? "--"}</div>` +
    `<div class="dim-text">Joint gate: trades ${j.trades_real ?? "--"}</div>`;

  const mc = (rep.montecarlo || {}).trend_gate || {};
  const boot = mc.bootstrap || {};
  const dd = mc.drawdown || {};
  $("rs-mc").innerHTML =
    `<div class="rs-title">MONTE CARLO</div>` +
    `<div>E[R] 95% CI: <b>${fmtR(boot.ci95_low_R)}</b> .. <b>${fmtR(boot.ci95_high_R)}</b></div>` +
    `<div>P(E≤0): <b>${fmtP(boot.p_expectancy_le_0)}</b></div>` +
    `<div>DD median / worst: <b>${dd["maxdd_median_%"] != null ? dd["maxdd_median_%"] + "%" : "--"}</b> / <b>${dd["maxdd_worst_%"] != null ? dd["maxdd_worst_%"] + "%" : "--"}</b></div>`;

  $("rs-risk").innerHTML =
    `<div class="rs-title">RISK GATE</div>` +
    `<div class="dim-text">Status live ada di panel eksekutor; laporan ini tidak memuat state real-time.</div>`;

  const ml = rep.ml_gate || {};
  const mlLine = ml.note
    ? `<div class="dim-text">${ml.note}</div>`
    : `<div>AUC test: <b>${fmtP(ml.auc_test)}</b> · base win: ${fmtP(ml.base_win_test)} · n test ${ml.n_test ?? "--"}</div>` +
      `<div class="dim-text">Sampel kecil, belum bisa disimpulkan edge.</div>`;
  $("rs-ml").innerHTML = `<div class="rs-title">ML GATE</div>${mlLine}`;

  $("rs-age").textContent = rep.generated_at ? rep.generated_at.slice(0, 16).replace("T", " ") : "--";
}

async function loadResearch() {
  try {
    const r = await fetch("/api/research");
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    renderResearch(await r.json());
  } catch (e) {
    $("rs-sig").innerHTML = `<div class="dim-text">Laporan belum tersedia: jalankan <code>python -m backend.research_report</code></div>`;
  }
}

async function restoreMode() {
  try {
    const h = await (await fetch("/api/health")).json();
    if (h.sensitivity) $("sel-sensitivity").value = h.sensitivity;
    if (h.exit_protocol) $("sel-exit").value = h.exit_protocol;
    setSignalChip("chip-golden", "SIGNAL GOLDEN", !!h.signal_golden);
    setSignalChip("chip-highrisk", "SIGNAL HIGH RISK", !!h.signal_high_risk);
  } catch (e) {}
}

/* ============================== LAYOUT RESIZE ============================== */
/* splitter bisa diseret: lebar rail, lebar 2 kolom bawah, tinggi baris bawah.
   Posisi tersimpan di localStorage (awali "at-") dan dipulihkan saat load. */
function initSplitters() {
  window.__splittersInit = true;
  const root = document.documentElement;
  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

  // pulihkan layout tersimpan — dengan clamp pengaman (nilai rusak diabaikan)
  const restore = (k, min, max) => {
    const v = parseFloat(localStorage.getItem("at-" + k));
    if (!isNaN(v) && v >= min && v <= max) root.style.setProperty("--" + k, v + "px");
  };
  restore("rail-w", 240, 560);
  restore("b1", 320, 760);
  restore("b2", 300, 760);
  restore("bh", 420, 1000);

  // simpan posisi sekarang ke localStorage
  const persist = () => {
    const cs = getComputedStyle(root);
    [["at-rail-w", "--rail-w"], ["at-b1", "--b1"], ["at-b2", "--b2"], ["at-bh", "--bh"]]
      .forEach(([key, cssv]) => {
        const v = parseFloat(cs.getPropertyValue(cssv));
        if (!isNaN(v)) localStorage.setItem(key, v);
      });
  };

  /* drag DELTA 1:1 — nilai awal + batas min/max diukur saat mousedown
     (sadar lebar kontainer), sehingga handle selalu menempel di kursor
     dan tidak ada kolom yang tertinggal/lebih dahulu. */
  const makeDrag = (el, measure, apply, defaults) => {
    if (!el) return;
    el.addEventListener("mousedown", (e) => {
      e.preventDefault();
      const startX = e.clientX, startY = e.clientY;
      const { val, min, max, axis } = measure();         // ukur KONDISI NYATA
      el.classList.add("drag");
      document.body.classList.add("resizing");
      const move = (ev) => {
        const delta = axis === "y" ? startY - ev.clientY : ev.clientX - startX;
        apply(clamp(val + delta, min, max));
      };
      const up = () => {
        el.classList.remove("drag");
        document.body.classList.remove("resizing");
        document.removeEventListener("mousemove", move);
        document.removeEventListener("mouseup", up);
        persist();
      };
      document.addEventListener("mousemove", move);
      document.addEventListener("mouseup", up);
    });
    if (defaults) el.addEventListener("dblclick", () => {   // dobel-klik = reset sumbu ini
      defaults.forEach(([k, v]) => {
        if (v === null) { root.style.removeProperty(k); localStorage.removeItem("at-" + k.slice(2)); }
        else root.style.setProperty(k, v + "px");
      });
      persist();
    });
  };

  // lebar rail kanan: seret KIRI untuk melebarkan (chart menyusut)
  makeDrag($("vs-main"),
    () => {
      const gw = document.querySelector(".main-grid").getBoundingClientRect().width;
      return { axis: "x", val: document.querySelector(".rail").getBoundingClientRect().width,
               min: 240, max: Math.max(280, gw - 500) };
    },
    (v) => root.style.setProperty("--rail-w", v + "px"),
    [["--rail-w", null]]);

  // lebar kolom bawah kiri: seret KANAN untuk melebarkan (kolom lain menyesuaikan)
  const gridW = () =>
    document.querySelector(".bottom-grid").getBoundingClientRect().width;
  makeDrag($("vs-b1"),
    () => {
      const gw = gridW();
      return { axis: "x", val: document.querySelector(".zone-card").getBoundingClientRect().width,
               min: 320, max: Math.max(380, gw - 640) };
    },
    (v) => root.style.setProperty("--b1", v + "px"),
    [["--b1", null]]);

  // lebar kolom bawah tengah: seret KANAN untuk melebarkan
  makeDrag($("vs-b2"),
    () => {
      const gw = gridW();
      const b1w = document.querySelector(".zone-card").getBoundingClientRect().width;
      return { axis: "x", val: document.querySelector(".mid-col").getBoundingClientRect().width,
               min: 300, max: Math.max(340, gw - b1w - 340) };
    },
    (v) => root.style.setProperty("--b2", v + "px"),
    [["--b2", null]]);

  // tinggi baris bawah: seret ATAS untuk menambah tinggi
  makeDrag($("hs-main"),
    () => ({ axis: "y", val: document.querySelector(".bottom-grid").getBoundingClientRect().height,
             min: 420, max: Math.min(1000, window.innerHeight - 180) }),
    (v) => root.style.setProperty("--bh", v + "px"),
    [["--bh", null]]);

  // tombol reset layout
  const rb = $("btn-reset-layout");
  if (rb) rb.addEventListener("click", () => {
    ["rail-w", "b1", "b2", "bh"].forEach(k => {
      localStorage.removeItem("at-" + k);
      root.style.removeProperty("--" + k);
    });
    location.reload();
  });
}

/* chip SIGNAL GOLDEN / SIGNAL HIGH RISK: toggle + kirim ke backend */
function setSignalChip(id, label, on) {
  const b = $(id);
  b.textContent = `${label}: ${on ? "ON" : "OFF"}`;
  b.classList.toggle("mode-gold", on && id === "chip-golden");
  b.classList.toggle("mode-red", on && id === "chip-highrisk");
  b.classList.toggle("mode-dim", !on);
}
function postSettingsBody(extra = {}) {
  return fetch("/api/settings", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      sensitivity: $("sel-sensitivity").value,
      exit_protocol: $("sel-exit").value,
      ...extra,
    }),
  }).then(r => r.json()).then(j => {
    if (j.signal_golden !== undefined) setSignalChip("chip-golden", "SIGNAL GOLDEN", j.signal_golden);
    if (j.signal_high_risk !== undefined) setSignalChip("chip-highrisk", "SIGNAL HIGH RISK", j.signal_high_risk);
    // snapshot diperbarui server (rebuild+broadcast); segarkan panel lokal
    return fetch("/api/snapshot").then(r => r.json()).then(s => { renderSnapshot(s); return j; });
  }).catch(() => {});
}

/* ============================== BOOT ============================== */
async function boot() {
  initChart();
  initSplitters();
  bindUI();
  restoreMode();
  try {
    const snap = await (await fetch("/api/snapshot")).json();
    if (!snap.error) {
      renderSnapshot(snap);
      const tf = snap.tf_entry || "15m";
      await switchTf(tf);
    }
  } catch (e) { console.warn("snapshot awal gagal:", e); }
  connectWS();
  setInterval(async () => {
    if (state.ws && state.ws.readyState === 1) return;
    try {
      const q = state.activeTf ? `?tf=${state.activeTf}` : "";
      renderSnapshot(await (await fetch("/api/snapshot" + q)).json());
    } catch (e) {}
  }, 15000);
}
boot();
