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
  activeSymbol: null, priceDigits: 2,
  snap: null, ws: null, wsBackoff: 1000,
  lines: { OB: [], FVG: [], LIMIT: [], RR: [], FORECAST: [], BASE: [], PDHL: [] },
  cvdSeries: null, markersApi: null,
  toggles: {},
  audioCtx: null, lastSigKey: null, lastStage: -1,
  barCount: 80, chartLimit: 20000,
};

const $ = (id) => document.getElementById(id);
/* base API: prioritas ?api= di URL > localStorage(at_api_base) >
   window.AT_API_BASE (config.js) > same-origin. Dipakai untuk bridge
   frontend Vercel -> backend lokal via tunnel (lihat docs/BRIDGE-VERCEL.md). */
const API = () => {
  if (window._apiCache === undefined) {
    window._apiCache = (new URLSearchParams(location.search).get("api")
      || localStorage.getItem("at_api_base") || window.AT_API_BASE || "")
      .replace(/\/$/, "");
  }
  return window._apiCache;
};
const setApiBase = (v) => {            // dipanggil dari klik status koneksi
  if (v) localStorage.setItem("at_api_base", v.replace(/\/$/, ""));
  else localStorage.removeItem("at_api_base");
  location.reload();
};
const api_ = (p) => API() + p;
const fmt = (v, d = 2) => (v === null || v === undefined || isNaN(v)) ? "--" : Number(v).toFixed(d);
const inferDigits = (px) => {
  const p = Math.abs(Number(px || 0));
  if (!p) return 2;
  if (p < 20) return 5;
  if (p < 500) return 3;
  return 2;
};
const fmtPx = (v) => fmt(v, state.priceDigits || 2);
function applyChartPrecision(d) {
  const prec = Math.max(1, Math.min(6, parseInt(d, 10) || 2));
  state.priceDigits = prec;
  if (state.series) {
    state.series.applyOptions({
      priceFormat: { type: "price", precision: prec, minMove: Number(Math.pow(10, -prec).toFixed(prec)) },
    });
  }
}

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
  const symQ = state.activeSymbol ? `&symbol=${state.activeSymbol}` : "";
  const r = await fetch(api_(`/api/candles?tf=${tf}&limit=${limit}${symQ}`));
  if (!r.ok) { console.warn("candles", tf, "tidak tersedia"); return false; }
  const j = await r.json();
  state.candles = j.candles;
  if (state.candles.length) {
    applyChartPrecision(inferDigits(state.candles[state.candles.length - 1][4]));
  }
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
      ctx.fillText(`${nama} ${tag}  ${fmtPx(z.bottom)} – ${fmtPx(z.top)}`, x0 + 6, (yT + yB) / 2 + 3);
    };
    zm.supply.forEach(z => drawBand(z, true));
    zm.demand.forEach(z => drawBand(z, false));
  }

  /* visualisasi trade simulasi (toggle RR): band hijau ke TP2, merah ke SL,
     label "TP 2 ($x) | RR 1:y" — seperti strategy overlay GODMODE */
  /* OVERLAY REVIEW: trade aktif (SL/Entry/TP1-3) + jejak history, per simbol chart */
  if (state.toggles.REVIEW !== false && snap.sim && snap.sim.trades) {
    const symNow = state.activeSymbol || snap.symbol || "";
    const all = snap.sim.trades.filter(t => (t.symbol || "") === symNow);
    const opens = all.filter(t => t.status === "OPEN");
    const closed = all.filter(t => t.status !== "OPEN").slice(0, 8);
    const trades = [...opens, ...closed];
    ctx.font = "10px 'Segoe UI', sans-serif";
    trades.forEach((tr, ti) => {
      const yE = y(tr.entry), yS = y(tr.sl), yT1 = y(tr.tp1), yT2 = y(tr.tp2);
      const yT3 = y(tr.tp3 !== undefined ? tr.tp3 : tr.tp2 + (tr.tp2 - tr.tp1));
      if ([yE, yS, yT1, yT2].some(v => v === null || isNaN(v))) return;
      const x0Raw = x(timeSec(tr.time));
      const x0 = (x0Raw === null || x0Raw < 0) ? 0 : x0Raw;
      const x1 = tr.status === "OPEN" ? W
        : (x(timeSec(tr.exit_time)) !== null ? x(timeSec(tr.exit_time)) : W);
      const isL = tr.side === "LONG";
      const win = tr.status === "WIN";
      const colTP = isL ? "22,199,132" : "234,57,67";
      const colSL = isL ? "234,57,67" : "22,199,132";
      const opacity = tr.status === "OPEN" ? 1 : 0.55; // history lebih redup

      // zona profit & risiko
      const bandTP = isL ? [yE, yT2] : [yT2, yE];
      const bandSL = isL ? [yE, yS] : [yS, yE];
      ctx.fillStyle = `rgba(${colTP},${0.13 * opacity})`;
      ctx.fillRect(x0, Math.min(bandTP[0], bandTP[1]), x1 - x0, Math.abs(bandTP[1] - bandTP[0]));
      ctx.fillStyle = `rgba(${colSL},${0.10 * opacity})`;
      ctx.fillRect(x0, Math.min(bandSL[0], bandSL[1]), x1 - x0, Math.abs(bandSL[1] - bandSL[0]));

      // garis level + label nilai di tepi kanan (label penuh hanya trade aktif)
      const isHis = tr.status !== "OPEN";
      const line = (yy, rgb, lbl, dash) => {
        if (yy === null || isNaN(yy) || yy < 0 || yy > H) return;
        ctx.strokeStyle = `rgba(${rgb},${0.55 * opacity})`;
        ctx.setLineDash(dash ? [4, 4] : []);
        ctx.beginPath(); ctx.moveTo(x0, yy); ctx.lineTo(x1, yy); ctx.stroke();
        ctx.setLineDash([]);
        if (!isHis) {
          ctx.fillStyle = `rgba(${rgb},0.9)`;
          ctx.fillText(lbl, Math.max(x0 + 4, x1 - 150), yy - 3);
        }
      };
      const tag = tr.status === "OPEN" ? "▶ " : `${tr.status} `;
      line(yS, colSL, `${tag}SL ${fmtPx(tr.sl)}`, false);
      line(yE, "217,182,74", `${tag}ENTRY ${fmtPx(tr.entry)}`, false);
      line(yT1, colTP, `TP1 ${fmtPx(tr.tp1)}`, true);
      line(yT2, colTP, `TP2 ${fmtPx(tr.tp2)}`, true);
      if (tr.status === "OPEN" && tr.tp3 !== undefined) {
        line(yT3, "124,140,160", `TP3 (proyeksi) ${fmtPx(tr.tp3)}`, true);
      }
      // badge hasil utk history
      if (tr.status !== "OPEN") {
        const plTxt = `${tr.pl_usd >= 0 ? "+" : "-"}$${fmt(Math.abs(tr.pl_usd || 0), 2)}`;
        ctx.fillStyle = tr.pl_usd >= 0 ? `rgba(22,199,132,${0.9 * opacity})`
                                       : `rgba(234,57,67,${0.9 * opacity})`;
        ctx.fillText(`${tr.side} ${tr.status} ${plTxt} (${tr.r >= 0 ? "+" : ""}${fmt(tr.r, 1)}R)`,
          x0 + 6, yE + (isL ? -6 : 14));
      }
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
    ctx.fillText(fmtPx(priceVal), W - bw + 6, yy + 3.5);
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
    const sensTxt = mode.sensitivity === "auto"
      ? `AUTO→${(mode.auto_resolved || "…").toUpperCase()}`
      : mode.sensitivity.toUpperCase();
    const txt = `MODE: ${sensTxt} · ${(mode.exit_protocol || "").toUpperCase()}`;
    ctx.font = "bold 10px 'Segoe UI', sans-serif";
    const w = ctx.measureText(txt).width + 14;
    ctx.fillStyle = "rgba(10,12,16,0.78)";
    if (W < 560) {
      // chart sempit: taruh di kiri bawah badge SND, jauh dari sumbu harga
      ctx.fillRect(8, 30, w, 18);
      ctx.strokeStyle = "#d9b64a";
      ctx.strokeRect(8.5, 30.5, w - 1, 17);
      ctx.fillStyle = "#d9b64a";
      ctx.fillText(txt, 15, 43);
    } else {
      ctx.fillRect(W - w - 74, 8, w, 18);
      ctx.strokeStyle = "#d9b64a";
      ctx.strokeRect(W - w - 73.5, 8.5, w - 1, 17);
      ctx.fillStyle = "#d9b64a";
      ctx.fillText(txt, W - w - 66, 21);
    }
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
  const engBadge = $("engine-tf-badge");
  const engBtn = $("btn-set-engine-tf");
  if (engBadge && state.engineTf) {
    engBadge.textContent = `ENGINE TF: ${state.engineTf.toUpperCase()}`;
  }
  if (engBtn && state.engineTf && state.activeTf) {
    const diff = state.activeTf !== state.engineTf;
    engBtn.style.display = diff ? "" : "none";
    if (diff) engBtn.textContent = `JADIKAN ${state.activeTf.toUpperCase()} TF SINYAL`;
  }
  const st = snap.stats;
  if (snap.price_digits || (st && st.price)) {
    applyChartPrecision(snap.price_digits || inferDigits(st.price));
  }
  if (!state.activeSymbol && snap.symbol) state.activeSymbol = snap.symbol;

  $("st-price").textContent = fmtPx(st.price);
  $("st-symbol").textContent = `${snap.symbol} Live${snap.demo ? " — DEMO" : ""}${snap.view_only ? ` (${snap.tf_entry.toUpperCase()} view)` : ""}`;
  $("st-rsi").textContent = fmt(st.rsi, 1);
  $("st-rsi-sub").textContent = st.rsi_label;
  $("st-atr").textContent = fmtPx(st.atr);
  $("st-structure").textContent = st.structure;
  $("st-structure-sub").textContent = st.structure_sub;
  $("st-ai").innerHTML = `${st.ai_score}<small>/100</small>`;
  $("st-ai-sub").textContent = st.ai_label;
  $("st-fib").textContent = st.fib;
  $("st-fib-sub").textContent = `Equilibrium: ${fmtPx(st.equilibrium)}`;

  $("chart-title").textContent = `${(snap.tf_entry || "").toUpperCase()} INSTITUTIONAL MATRIX — ${snap.symbol}`;
  $("chart-structure").textContent = st.structure;

  /* pemilih simbol (multi-pair MT5 / native) */
  const selSym = $("sel-symbol");
  if (selSym) {
    const list = snap.symbols || [snap.symbol];
    selSym.style.display = "";
    if (selSym.options.length !== list.length ||
        [...selSym.options].some((o, k) => o.value !== list[k])) {
      selSym.innerHTML = list.map(s => `<option value="${s}">${s}</option>`).join("");
    }
    const wantSym = state.activeSymbol && list.includes(state.activeSymbol) ? state.activeSymbol : snap.symbol;
    selSym.value = wantSym;
  }

  /* golden / high risk */
  $("golden-price").textContent = fmtPx(snap.golden.price);
  $("golden-state").textContent = snap.golden.state;
  $("highrisk-price").textContent = fmtPx(snap.high_risk.price);
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
    $("sup-top").textContent = fmtPx(z.supply.top);
    $("sup-bottom").textContent = fmtPx(z.supply.bottom);
    $("sup-strength").textContent = `Strength: ${z.supply.strength} — CVD ${z.supply.cvd >= 0 ? "+" : ""}${z.supply.cvd}`;
    $("sup-status").textContent = z.supply.status;
    $("zone-status").textContent = z.supply.status.includes("IN ZONE") || z.supply.status.includes("AT") ? z.supply.status : "AT SUPPLY";
  }
  if (z.demand) {
    $("dem-top").textContent = fmtPx(z.demand.top);
    $("dem-bottom").textContent = fmtPx(z.demand.bottom);
    $("dem-strength").textContent = `Strength: ${z.demand.strength} — CVD ${z.demand.cvd >= 0 ? "+" : ""}${z.demand.cvd}`;
    $("dem-status").textContent = z.demand.status;
  }

  /* zona multi-level S1-S3 / D1-D3 */
  const zml = snap.zones_multi;
  if (zml) {
    const pill = (z, cls) => `<span class="zone-pill ${cls}">${z.level}: ${fmtPx(z.bottom)}–${fmtPx(z.top)} (${z.dist_pts >= 0 ? "+" : ""}${fmtPx(z.dist_pts)})</span>`;
    $("zone-list").innerHTML =
      zml.supply.map(z => pill(z, "sup")).join("") + zml.demand.map(z => pill(z, "dem")).join("");
  }

  /* events */
  $("events-list").innerHTML = (snap.events || []).map(ev => `
    <div class="ev-row">
      <span class="ev-tag">${ev.tag}</span>
      <span class="ev-price">${fmtPx(ev.price)}</span>
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
    const cell = (k, v, cls = "", kCls = "") =>
      `<div class="sim-stat"><div class="v ${cls}">${v}</div><div class="k ${kCls}">${k}</div></div>`;
    const usd = (v) => `${v >= 0 ? "+" : "-"}$${fmt(Math.abs(v), 2)}`;
    const winUsd = Number(s.pl_win_usd ?? 0);
    const lossUsd = Number(s.pl_loss_usd ?? 0);
    const netUsd = s.net_usd !== undefined && s.net_usd !== null ? Number(s.net_usd) : (Number(s.equity) - 10000);
    const maxDd = Number(s.max_dd ?? 0);
    $("sim-stats").innerHTML =
      cell("TRADE", `${s.trades}<small style="color:var(--dim)"> +${s.open} open</small>`) +
      cell("WINRATE", `${s.winrate}%`, s.winrate >= 50 ? "green-text" : "red-text") +
      cell("PROFIT F", s.profit_factor === null ? "--" : s.profit_factor,
           (s.profit_factor !== null && Number(s.profit_factor) >= 1) ? "green-text" : "red-text") +
      cell("AVG R", (s.avg_r >= 0 ? "+" : "") + s.avg_r + "R", s.avg_r >= 0 ? "green-text" : "red-text") +
      cell("WIN", `${s.wins} · +$${fmt(Math.abs(winUsd), 2)}`, "green-text", "green-text") +
      cell("LOSS", `${s.losses} · -$${fmt(Math.abs(lossUsd), 2)}`, "red-text", "red-text") +
      cell("NET", usd(netUsd), netUsd >= 0 ? "green-text" : "red-text", netUsd >= 0 ? "green-text" : "red-text") +
      cell("MAX DD", `${fmt(maxDd, 2)}%`, maxDd < 0 ? "red-text" : "dim-text");
    // detail review: kartu penuh per trade — dirender ke #sim-detail (halaman SIM)
    renderSimDetail(sim.trades || []);
  }

/* ---------- kartu detail review per trade (halaman SIMULASI) ---------- */
function renderSimDetail(trades) {
  const box = $("sim-detail");
  if (!box) return;
  const f = state.simFilter || "ALL";
  const rows = trades.filter(t => f === "ALL"
    ? true
    : f === "OPEN" ? t.status === "OPEN"
    : f === "WIN" ? (t.status === "WIN" || t.status === "BE+" || (t.status !== "OPEN" && (t.pl_usd ?? t.r ?? 0) > 0))
    : (t.status === "LOSS" || (t.status === "TIME" && (t.pl_usd ?? t.r ?? 0) <= 0)));
  box.innerHTML = rows.map(t => {
    const isOpen = t.status === "OPEN";
    const plNum = Number(t.pl_usd ?? 0);
    const rShow = isOpen && t.floating_r !== undefined ? Number(t.floating_r) : Number(t.r ?? 0);
    const isWin = !isOpen && (t.status === "WIN" || t.status === "BE+" || plNum > 0 || rShow > 0);
    const cls = isOpen ? "open" : isWin ? "win" : "loss";
    const plPrefix = isOpen ? "FLT " : "";
    const pl = `${plPrefix}${plNum >= 0 ? "+" : "-"}$${fmt(Math.abs(plNum), 2)}`;
    const plCls = plNum >= 0 ? "green-text" : "red-text";
    const chipCls = isOpen ? "chip-amber" : isWin ? "chip-green" : "chip-red";
    const lvl = (lbl, v, cls2 = "") => `<span>${lbl} <b class="${cls2}">${fmtPx(v)}</b></span>`;
    const tp3Val = t.tp3 !== undefined ? t.tp3 : (t.tp2 !== undefined && t.tp1 !== undefined ? t.tp2 + (t.tp2 - t.tp1) : null);
    const symLbl = t.symbol || (state.snap && state.snap.symbol) || "";
    return `
    <div class="sim-block ${cls}">
      <div class="head">
        <span class="when">${String(t.time).slice(0, 16).replace("T", " ")} → ${t.exit_time ? String(t.exit_time).slice(11, 16) : "LIVE"}</span>
        <span class="sym">${symLbl} · <span class="${t.side === "LONG" ? "green-text" : "red-text"}">${t.side}</span> · ${t.kind}${t.kind === "GOLDEN" ? " ★" : ""}</span>
        <span class="chip ${chipCls}">${t.status}</span>
      </div>
      <div class="levels">
        ${lvl("ENTRY", t.entry)}${lvl("SL", t.sl, "red-text")}
        ${lvl("TP1", t.tp1, "green-text")}${lvl("TP2", t.tp2, "green-text")}
        ${lvl("TP3*", tp3Val, "dim-text")}
        ${t.hit1 ? `<span class="chip chip-green" style="padding:1px 6px;font-size:9px">TP1 ✓</span>` : ""}
      </div>
      <div class="levels">
        <span>UNIT <b>${fmt(t.units, 3)}</b></span>
        <span>RISK <b class="red-text">$${fmt(t.risk_usd, 2)}</b></span>
        <span>P/L <b class="${plCls}">${pl}</b> (<b class="${rShow >= 0 ? "green-text" : "red-text"}">${rShow >= 0 ? "+" : ""}${fmt(rShow, 2)}R</b>)</span>
        <span>BAR <b>${t.bars}</b></span>
      </div>
      <div class="why">${t.reason || (isOpen ? "Posisi berjalan — menunggu TP1 / SL" : "—")}</div>
    </div>`;
  }).join("") || `<div class="dim-text">— tidak ada trade pada filter ini</div>`;
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
    $("su-entry").textContent = fmtPx(su.entry);
    $("su-sl").textContent = fmtPx(su.sl);
    $("su-tp1").textContent = fmtPx(su.tp1);
    $("su-tp2").textContent = fmtPx(su.tp2);
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
      <span>${fmtPx(s.entry)}</span>
      <span class="dim-text">skor ${s.score}</span>
    </div>`).join("") : `<div class="dim-text">— Waiting for signals…</div>`;

  /* tombol TF yang tersedia */
  document.querySelectorAll(".tf-btn").forEach(b => {
    b.classList.toggle("disabled", !hm.tfs.map(t => t.toLowerCase()).includes(b.dataset.tf));
  });

  updateRiskCalc();
  if (snap.mt5) renderMt5Status(snap.mt5);
  drawOverlays();
  detectAlerts(snap);
}

/* ============================== METATRADER 5 ============================== */
let _mt5FormHydrated = false;
function renderMt5Status(st) {
  if (!st) return;
  const topChip = $("mt5-top-chip");
  const badge = $("mt5-badge");
  const armBtn = $("btn-mt5-arm");
  const closeAllBtn = $("btn-mt5-close-all");
  const msgEl = $("mt5-status-msg");

  if (topChip) {
    if (!st.installed) {
      topChip.textContent = "MT5: N/A";
      topChip.className = "chip chip-dim";
    } else if (st.connected && st.armed) {
      topChip.textContent = `MT5: ARMED (${st.resolved_symbol || "OK"})`;
      topChip.className = "chip chip-green";
    } else if (st.connected) {
      topChip.textContent = `MT5: STANDBY`;
      topChip.className = "chip chip-amber";
    } else {
      topChip.textContent = "MT5: OFF";
      topChip.className = "chip chip-dim";
    }
  }

  if (badge) {
    const trLbl = (st.transport || "python").toUpperCase();
    if (!st.installed) {
      badge.textContent = trLbl === "MCP" ? "MCP URL BELUM DIISI" : "PKG BELUM TERPASANG";
      badge.className = "chip chip-red";
    } else if (st.connected && st.armed) {
      badge.textContent = `ARMED · ${(st.account && st.account.trade_mode) || "MT5"} (${trLbl})`;
      badge.className = "chip chip-green";
    } else if (st.connected) {
      badge.textContent = `STANDBY · ${(st.account && st.account.trade_mode) || "MT5"} (${trLbl})`;
      badge.className = "chip chip-amber";
    } else {
      badge.textContent = "TERPUTUS";
      badge.className = "chip chip-dim";
    }
  }

  if (armBtn) {
    armBtn.textContent = st.armed ? "AUTO-TRADE: ARMED (ON)" : "AUTO-TRADE: STANDBY (OFF)";
    armBtn.className = st.armed ? "chip chip-green" : "chip mode-dim";
  }

  if ($("mt5-acc-mode")) {
    const a = st.account;
    $("mt5-acc-mode").textContent = a ? `#${a.login} (${a.trade_mode})` : "--";
    $("mt5-acc-mode").className = "v " + (a ? (a.is_demo ? "gold-text" : "red-text") : "");
    $("mt5-acc-eq").textContent = a ? `${a.currency || "$"} ${fmt(a.equity)}` : "--";
    const si = st.symbol_info;
    $("mt5-sym-now").textContent = si ? `${si.name} · ${si.spread_points}pt` : (st.resolved_symbol || "--");
    const dp = st.daily_pnl_usd || 0;
    $("mt5-daily-pnl").textContent = st.connected ? `${dp >= 0 ? "+" : "-"}$${fmt(Math.abs(dp))}` : "--";
    $("mt5-daily-pnl").className = "v " + (st.daily_stop_hit ? "red-text" : dp >= 0 ? "green-text" : "red-text");
  }

  if (msgEl && st.last_error) {
    msgEl.textContent = "❌ " + st.last_error;
    msgEl.className = "setup-status red-text";
  } else if (msgEl && st.connected && !msgEl.dataset.sticky) {
    msgEl.textContent = st.armed
      ? `● ARMED — sinyal lolos filter akan dieksekusi otomatis (split 50% TP1 + 50% TP2 + Auto-BE) di ${st.resolved_symbol}`
      : `Terhubung ke ${st.resolved_symbol} — mode STANDBY (aman, belum mengirim order).`;
    msgEl.className = "setup-status " + (st.armed ? "green-text" : "gold-text");
  }

  // Isi form sekali saat pertama muat (jangan menimpa input saat user mengetik)
  const cfg = st.settings;
  if (cfg && !_mt5FormHydrated && $("mt5-risk")) {
    _mt5FormHydrated = true;
    $("mt5-symbol").value = cfg.mt5_symbol || "";
    if ($("mt5-transport")) $("mt5-transport").value = cfg.mt5_transport || "python";
    $("mt5-path").value = cfg.mt5_terminal_path || "";
    if ($("mt5-mcp-url")) $("mt5-mcp-url").value = cfg.mt5_mcp_url || "";
    if ($("mt5-mcp-key")) {
      $("mt5-mcp-key").value = "";
      $("mt5-mcp-key").placeholder = cfg.mt5_mcp_key_set
        ? "Key tersimpan (kosongkan bila tidak diganti)"
        : "Bearer key dari Tools → Options → MCP";
    }
    $("mt5-risk").value = cfg.mt5_risk_pct ?? 1.0;
    $("mt5-max-open").value = cfg.mt5_max_open_trades ?? 1;
    $("mt5-max-spread").value = cfg.mt5_max_spread_points ?? 45;
    $("mt5-daily-stop").value = cfg.mt5_daily_stop_r ?? 2.0;
    $("mt5-be-offset").value = cfg.mt5_be_offset_points ?? 0;
    $("mt5-deviation").value = cfg.mt5_deviation ?? 25;
    $("mt5-magic").value = cfg.mt5_magic ?? 202605;
    const dBtn = $("mt5-demo-only");
    if (dBtn) {
      const on = cfg.mt5_demo_only !== false;
      dBtn.classList.toggle("on", on);
      dBtn.textContent = `DEMO ONLY: ${on ? "ON" : "OFF"}`;
    }
  }

  const posBox = $("mt5-positions");
  const posList = st.positions || [];
  if (closeAllBtn) closeAllBtn.style.display = posList.length ? "" : "none";
  if (posBox) {
    posBox.innerHTML = posList.length ? posList.map(p => `
      <div class="mt5-pos-row">
        <span><b>#${p.ticket}</b> ${p.symbol} <b class="${p.side === "BUY" ? "green-text" : "red-text"}">${p.side}</b> ${p.volume}L @ ${fmt(p.price_open)}</span>
        <span>SL ${fmt(p.sl)} · TP ${fmt(p.tp)} · <b class="${p.profit >= 0 ? "green-text" : "red-text"}">${p.profit >= 0 ? "+" : ""}$${fmt(p.profit)}</b></span>
        <button class="btn-ghost sm red-border" data-mt5-close="${p.ticket}">CLOSE</button>
      </div>`).join("") : `<div class="dim-text">— tidak ada posisi terbuka</div>`;
    posBox.querySelectorAll("[data-mt5-close]").forEach(btn => {
      btn.addEventListener("click", () => {
        const tk = parseInt(btn.dataset.mt5Close, 10);
        fetch(api_("/api/mt5/close"), {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ ticket: tk }),
        }).then(r => r.json()).then(j => {
          if ($("mt5-status-msg")) $("mt5-status-msg").textContent = (j.ok ? "✅ " : "❌ ") + (j.msg || j.error || "");
          if (j.status) renderMt5Status(j.status);
        }).catch(() => {});
      });
    });
  }

  const evBox = $("mt5-events");
  if (evBox) {
    const evs = st.events || [];
    evBox.innerHTML = evs.length ? evs.map(e => `
      <div class="mt5-ev-row">
        <span class="t">${String(e.time).slice(5, 19)}</span>
        <span class="chip chip-dim" style="padding:1px 6px;font-size:9px">${String(e.kind || "").toUpperCase()}</span>
        <span>${e.msg}</span>
      </div>`).join("") : `<div class="dim-text">— belum ada aktivitas MT5</div>`;
  }
}

function readMt5FormBody() {
  const dBtn = $("mt5-demo-only");
  const out = {
    mt5_symbol: ($("mt5-symbol") ? $("mt5-symbol").value : "").trim(),
    mt5_transport: ($("mt5-transport") ? $("mt5-transport").value : "python").trim(),
    mt5_terminal_path: ($("mt5-path") ? $("mt5-path").value : "").trim(),
    mt5_mcp_url: ($("mt5-mcp-url") ? $("mt5-mcp-url").value : "").trim(),
    mt5_risk_pct: parseFloat($("mt5-risk")?.value || "1.0"),
    mt5_max_open_trades: parseInt($("mt5-max-open")?.value || "1", 10),
    mt5_max_spread_points: parseInt($("mt5-max-spread")?.value || "45", 10),
    mt5_daily_stop_r: parseFloat($("mt5-daily-stop")?.value || "2.0"),
    mt5_be_offset_points: parseInt($("mt5-be-offset")?.value || "0", 10),
    mt5_deviation: parseInt($("mt5-deviation")?.value || "25", 10),
    mt5_magic: parseInt($("mt5-magic")?.value || "202605", 10),
    mt5_demo_only: dBtn ? dBtn.classList.contains("on") : true,
  };
  const k = ($("mt5-mcp-key") ? $("mt5-mcp-key").value : "").trim();
  if (k) out.mt5_mcp_key = k;
  return out;
}

function loadMt5Status() {
  fetch(api_("/api/mt5/status")).then(r => r.ok ? r.json() : null).then(st => {
    if (st) renderMt5Status(st);
  }).catch(() => {});
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

/* ============================== WS / POLLING ============================== */
/* WS dipakai hanya bila frontend dan backend satu origin (localhost).
   Bila frontend di-host terpisah (base API via ?api= / localStorage /
   window.AT_API_BASE, mis. bridge Vercel -> backend lokal via tunnel),
   dashboard memakai polling /api/snapshot tiap 4 detik + refresh candle
   incremental — cukup responsif karena data hanya berubah per candle close. */
async function pollSnapshot() {
  try {
    const q = (state.activeTf ? `?tf=${state.activeTf}` : "")
            + (state.activeSymbol ? `&symbol=${state.activeSymbol}` : "");
    renderSnapshot(await (await fetch(api_("/api/snapshot" + q))).json());
    // chart belum terisi (boot gagal saat server belum siap) -> coba initial-load
    if (!state.activeTf && !_bootDataLoading) loadInitialData();
    if (state.activeTf) await refreshLatestCandles();
  } catch (e) { /* jaringan gotoh — coba lagi di tick berikutnya */ }
}

/* ambil beberapa candle terakhir dan update series secara inkremental
   (pengganti pesan WS "candle" saat mode polling) */
async function refreshLatestCandles() {
  let j;
  try {
    const symQ = state.activeSymbol ? `&symbol=${state.activeSymbol}` : "";
    const r = await fetch(api_(`/api/candles?tf=${state.activeTf}&limit=5${symQ}`));
    if (!r.ok) return;
    j = await r.json();
  } catch (e) { return; }
  const rows = j.candles || [];
  let changed = false;
  for (const c of rows) {
    const last = state.candles[state.candles.length - 1];
    if (last && c[0] < last[0]) continue;              // candle lampau, abaikan
    if (last && c[0] === last[0]) state.candles[state.candles.length - 1] = c;
    else { state.candles.push(c); changed = true; }
    state.series.update({ time: c[0] / 1000, open: c[1], high: c[2], low: c[3], close: c[4] });
    if (state.volSeries) state.volSeries.update({
      time: c[0] / 1000, value: c[5],
      color: c[4] >= c[1] ? "rgba(34,197,94,0.38)" : "rgba(239,68,68,0.38)",
    });
  }
  if (rows.length) {
    updateCvdSeries();
    drawCanvas();
  }
  if (changed) fitBars();
}

function startPolling(ms) {
  if (state._pollTimer) return;
  state._pollTimer = setInterval(pollSnapshot, ms || (state.pollOnly ? 4000 : 15000));
}

function fallBackToPolling(reason) {
  if (state.pollOnly) return;
  state.pollOnly = true;
  $("conn-status").textContent = `mode polling (${reason})`;
  pollSnapshot();
  startPolling(4000);
}

async function connectWS() {
  if (API()) {
    const host = API().replace(/^https?:\/\//, "");
    state.pollOnly = true;
    $("conn-status").textContent = `mode polling • ${host}`;
    $("conn-status").title = "klik untuk mengganti URL backend";
    pollSnapshot();
    startPolling(4000);
    return;
  }
  try {
    const cfg = await (await fetch(api_("/api/config"))).json();
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    const url = `${scheme}://${location.hostname}:${cfg.ws_port}`;
    state.ws = new WebSocket(url);
    state.ws.onopen = () => {
      state.wsBackoff = 1000;
      $("conn-status").textContent = `terhubung • ${new Date().toLocaleTimeString()}`;
    };
    state.ws.onmessage = (m) => {
      const msg = JSON.parse(m.data);
      const curSym = state.activeSymbol || (state.snap && state.snap.symbol);
      const sameSym = !msg.symbol || !curSym || msg.symbol === curSym;
      if (msg.type === "hello" && msg.snapshot && !state.snap) renderSnapshot(msg.snapshot);
      if (msg.type === "snapshot" && sameSym && !state.activeTf) renderSnapshot(msg.data);
      else if (msg.type === "snapshot" && sameSym && state.snap && msg.data && msg.data.tf_entry === state.snap.tf_entry) renderSnapshot(msg.data);
      if (msg.type === "candle" && sameSym && msg.tf === state.activeTf) {
        const c = msg.candle;
        const last = state.candles[state.candles.length - 1];
        if (last && c[0] < last[0]) return;
        if (last && c[0] === last[0]) state.candles[state.candles.length - 1] = c;
        else state.candles.push(c);
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
      state.wsFails = (state.wsFails || 0) + 1;
      // host 1 port (mis. Hugging Face Spaces) tidak akan pernah buka WS —
      // setelah 2 kegagalan, beralih permanen ke polling
      if (state.wsFails >= 2) { fallBackToPolling("WS tidak tersedia"); return; }
      $("conn-status").textContent = "terputus, reconnect…";
      setTimeout(connectWS, state.wsBackoff);
      state.wsBackoff = Math.min(state.wsBackoff * 2, 30000);
    };
  } catch (e) {
    state.wsFails = (state.wsFails || 0) + 1;
    if (state.wsFails >= 2) { fallBackToPolling("WS gagal: " + e.message); return; }
    $("conn-status").textContent = "gagal konek WS: " + e.message;
    setTimeout(connectWS, 5000);
  }
}

/* ============================== TF SWITCH ============================== */
async function switchTf(tf) {
  const ok = await loadCandles(tf);
  if (!ok) return;
  try { localStorage.setItem("at-tf", tf); } catch (e) {}
  try {
    const symQ = state.activeSymbol ? `&symbol=${encodeURIComponent(state.activeSymbol)}` : "";
    const snap = await (await fetch(api_(`/api/snapshot?tf=${tf}${symQ}`))).json();
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
  // ---- navigasi halaman: sidebar (desktop) + bottom-nav (ponsel) ----
  const VIEWS = ["trading", "zona", "struktur", "news", "sim", "setting"];
  const switchView = (name) => {
    if (!VIEWS.includes(name)) name = "trading";
    document.querySelectorAll(".view").forEach(v =>
      v.classList.toggle("hidden", v.id !== "view-" + name));
    document.querySelectorAll(".side-btn").forEach(b =>
      b.classList.toggle("on", b.dataset.view === name));
    document.querySelectorAll(".bn-btn").forEach(b =>
      b.classList.toggle("on", b.dataset.view === name));
    localStorage.setItem("at-view", name);
    window.scrollTo(0, 0);
    if (name === "trading") {
      // bila data awal belum masuk (boot saat buffer server belum siap), muat
      if (!_bootDataDone) loadInitialData();
      setTimeout(() => drawCanvas(), 80); // redraw overlay pasca tampil
    }
  };
  document.querySelectorAll(".side-btn").forEach(b =>
    b.addEventListener("click", () => switchView(b.dataset.view)));
  document.querySelectorAll(".bn-btn").forEach(b =>
    b.addEventListener("click", () => switchView(b.dataset.view)));
  switchView(localStorage.getItem("at-view") || "trading");

  // ---- halaman PENGATURAN: muat isi form + aksi simpan/tes ----
  const loadSettingsPage = () => {
    fetch(api_("/api/settings")).then(r => r.json()).then(s => {
      $("set-tg-token").value = s.tg_token || "";
      $("set-tg-chat").value = s.tg_chat_id || "";
      const tgBtn = $("set-tg-on");
      tgBtn.textContent = `NOTIF: ${s.tg_on ? "ON" : "OFF"}`;
      tgBtn.classList.toggle("on", !!s.tg_on);
      $("set-mode-now").textContent = (s.sensitivity || "-").toUpperCase();
      $("set-exit-now").textContent = (s.exit_protocol || "-").toUpperCase();
    }).catch(() => {});
  };
  // muat form saat halaman setting dibuka pertama kali / tiap klik menu
  document.querySelectorAll('[data-view="setting"]').forEach(b =>
    b.addEventListener("click", () => { loadSettingsPage(); loadMt5Status(); }));
  loadSettingsPage();
  loadMt5Status();

  // ---- MetaTrader 5 UI bindings ----
  const mt5Top = $("mt5-top-chip");
  if (mt5Top) mt5Top.addEventListener("click", () => switchView("setting"));

  const mt5DemoBtn = $("mt5-demo-only");
  if (mt5DemoBtn) mt5DemoBtn.addEventListener("click", () => {
    const on = !mt5DemoBtn.classList.contains("on");
    if (!on && !confirm("Matikan pengaman DEMO ONLY? Akun REAL bisa menerima order otomatis bila AUTO-TRADE diaktifkan!")) return;
    mt5DemoBtn.classList.toggle("on", on);
    mt5DemoBtn.textContent = `DEMO ONLY: ${on ? "ON" : "OFF"}`;
  });

  const setMt5Msg = (txt, cls) => {
    const el = $("mt5-status-msg");
    if (!el) return;
    el.textContent = txt;
    el.className = "setup-status " + (cls || "dim-text");
    el.dataset.sticky = "1";
    setTimeout(() => { delete el.dataset.sticky; }, 5000);
  };

  if ($("btn-mt5-save")) $("btn-mt5-save").addEventListener("click", () => {
    fetch(api_("/api/mt5/settings"), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(readMt5FormBody()),
    }).then(r => r.json()).then(j => {
      setMt5Msg(j.ok ? "✅ Konfigurasi MT5 tersimpan" : "❌ Gagal menyimpan konfigurasi", j.ok ? "green-text" : "red-text");
      if (j.status) renderMt5Status(j.status);
    }).catch(() => setMt5Msg("❌ Jaringan gagal", "red-text"));
  });

  if ($("btn-mt5-connect")) $("btn-mt5-connect").addEventListener("click", () => {
    setMt5Msg("Menghubungkan ke terminal MT5…", "gold-text");
    fetch(api_("/api/mt5/connect"), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(readMt5FormBody()),
    }).then(r => r.json()).then(j => {
      setMt5Msg((j.ok ? "✅ " : "❌ ") + (j.msg || j.error || ""), j.ok ? "green-text" : "red-text");
      if (j.status) renderMt5Status(j.status);
    }).catch(() => setMt5Msg("❌ Gagal menghubungi backend", "red-text"));
  });

  if ($("btn-mt5-disconnect")) $("btn-mt5-disconnect").addEventListener("click", () => {
    fetch(api_("/api/mt5/disconnect"), { method: "POST" }).then(r => r.json()).then(j => {
      setMt5Msg("Koneksi MT5 diputus (STANDBY)", "dim-text");
      if (j.status) renderMt5Status(j.status);
    }).catch(() => {});
  });

  if ($("btn-mt5-arm")) $("btn-mt5-arm").addEventListener("click", () => {
    const wantArm = !$("btn-mt5-arm").textContent.includes("ARMED");
    fetch(api_("/api/mt5/arm"), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ armed: wantArm }),
    }).then(r => r.json()).then(j => {
      setMt5Msg((j.ok ? (wantArm ? "✅ " : "⏸ ") : "❌ ") + (j.msg || j.error || ""), j.ok ? (wantArm ? "green-text" : "gold-text") : "red-text");
      if (j.status) renderMt5Status(j.status);
    }).catch(() => setMt5Msg("❌ Gagal ubah status ARM", "red-text"));
  });

  if ($("btn-mt5-close-all")) $("btn-mt5-close-all").addEventListener("click", () => {
    if (!confirm("Tutup semua posisi MT5 yang dibuka oleh Alpha-Trade?")) return;
    fetch(api_("/api/mt5/close"), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ all: true }),
    }).then(r => r.json()).then(j => {
      setMt5Msg((j.ok ? "✅ " : "❌ ") + (j.msg || ""), j.ok ? "green-text" : "red-text");
      if (j.status) renderMt5Status(j.status);
    }).catch(() => {});
  });

  // filter riwayat simulasi (halaman SIMULASI)
  state.simFilter = "ALL";
  document.querySelectorAll("[data-sfilter]").forEach(b => b.addEventListener("click", () => {
    state.simFilter = b.dataset.sfilter;
    document.querySelectorAll("[data-sfilter]").forEach(x =>
      x.classList.toggle("on", x === b));
    // render ulang dari snapshot terakhir
    if (state.snap && state.snap.sim) renderSimDetail(state.snap.sim.trades || []);
  }));

  const tgToggle = $("set-tg-on");
  if (tgToggle) tgToggle.addEventListener("click", () => {
    const on = !tgToggle.classList.contains("on");
    tgToggle.classList.toggle("on", on);
    tgToggle.textContent = `NOTIF: ${on ? "ON" : "OFF"}`;
  });
  const saveSettings = () => {
    fetch(api_("/api/settings"), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tg_token: $("set-tg-token").value,
        tg_chat_id: $("set-tg-chat").value,
        tg_on: tgToggle.classList.contains("on"),
      }),
    }).then(r => r.json()).then(j => {
      $("set-status").textContent = j.tg_terpasang
        ? "✅ tersimpan — notifikasi aktif" : "tersimpan (token/chat belum lengkap)";
    }).catch(() => { $("set-status").textContent = "gagal menyimpan"; });
  };
  const saveBtn = $("btn-set-save");
  if (saveBtn) saveBtn.addEventListener("click", saveSettings);
  const testBtn = $("btn-tg-test");
  if (testBtn) testBtn.addEventListener("click", () => {
    $("set-status").textContent = "mengirim tes…";
    fetch(api_("/api/tg-test"), {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tg_token: $("set-tg-token").value,
        tg_chat_id: $("set-tg-chat").value,
      }),
    }).then(r => r.json()).then(j => {
      $("set-status").textContent = j.ok ? "✅ " + j.msg : "❌ " + (j.error || "gagal");
    }).catch(() => { $("set-status").textContent = "❌ jaringan gagal"; });
  });
  document.querySelectorAll(".tf-btn").forEach(b => b.addEventListener("click", () => {
    if (b.classList.contains("disabled")) return;
    switchTf(b.dataset.tf);
  }));
  // pindah simbol (multi-pair MT5 / native): muat ulang candles + snapshot
  async function selectSymbol(sym) {
    if (!sym) return;
    state.activeSymbol = sym;
    try { localStorage.setItem("at-symbol", sym); } catch (e) {}
    const selSym = $("sel-symbol");
    if (selSym) selSym.value = sym;
    const tf = state.activeTf || (state.snap && state.snap.tf_entry) || "5m";
    await loadCandles(tf);
    try {
      const q = `?tf=${tf}&symbol=${encodeURIComponent(sym)}`;
      const snap = await (await fetch(api_("/api/snapshot" + q))).json();
      if (!snap.error) renderSnapshot(snap);
    } catch (e) {}
    drawOverlays();
  }
  const selSymEl = $("sel-symbol");
  if (selSymEl) selSymEl.addEventListener("change", () => selectSymbol(selSymEl.value));

  // ubah TF engine sinyal & simulasi langsung dari UI (tersimpan di server)
  const setEngBtn = $("btn-set-engine-tf");
  if (setEngBtn) setEngBtn.addEventListener("click", async () => {
    if (!state.activeTf) return;
    setEngBtn.disabled = true;
    setEngBtn.textContent = "MENGHITUNG ULANG…";
    try {
      await postSettingsBody({ entry_tf: state.activeTf });
      await switchTf(state.activeTf);
    } finally {
      setEngBtn.disabled = false;
    }
  });

  // ---- Modal kelola multi-pair MT5 (+ PAIR) ----
  const pairModal = $("pair-modal");
  const QUICK_PAIRS = ["XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "XAGUSD", "US500", "USTECH100M", "US30M", "DE40"];
  const setPairMsg = (txt, cls = "dim-text") => {
    const el = $("pair-modal-msg");
    if (!el) return;
    el.textContent = txt;
    el.className = "setup-status " + cls;
  };
  async function addPairAndSwitch(sym) {
    sym = (sym || "").trim().toUpperCase();
    if (!sym) return;
    setPairMsg(`Memuat candle & replay ${sym}…`, "gold-text");
    try {
      const r = await fetch(api_("/api/symbols/add"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol: sym }),
      });
      const j = await r.json();
      if (!j.ok) { setPairMsg("❌ " + (j.msg || j.error || "gagal"), "red-text"); return; }
      setPairMsg("✅ " + (j.msg || `${j.symbol} ditambahkan`), "green-text");
      await selectSymbol(j.symbol);
      renderPairModal(j.symbols || []);
    } catch (e) { setPairMsg("❌ Gagal menambah pair", "red-text"); }
  }
  async function removePair(sym) {
    try {
      const r = await fetch(api_("/api/symbols/remove"), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol: sym }),
      });
      const j = await r.json();
      if (!j.ok) { setPairMsg("❌ " + (j.msg || j.error || "gagal"), "red-text"); return; }
      setPairMsg(j.msg || `${sym} dihapus`, "dim-text");
      const list = j.symbols || [];
      if (state.activeSymbol === sym && list.length) await selectSymbol(list[0]);
      renderPairModal(list);
    } catch (e) {}
  }
  function renderPairModal(activeList) {
    const act = activeList || (state.snap && state.snap.symbols) || [];
    const cur = state.activeSymbol || (state.snap && state.snap.symbol);
    const aBox = $("pair-active-list");
    if (aBox) {
      aBox.innerHTML = act.map(s => `
        <span class="pair-chip ${s === cur ? "on" : ""}" data-psel="${s}">
          <span>${s}</span>
          ${act.length > 1 ? `<span class="del" data-pdel="${s}" title="Hapus ${s}">×</span>` : ""}
        </span>`).join("");
      aBox.querySelectorAll("[data-psel]").forEach(el => el.addEventListener("click", (ev) => {
        if (ev.target.dataset.pdel) return;
        selectSymbol(el.dataset.psel);
        if (pairModal) pairModal.classList.add("hidden");
      }));
      aBox.querySelectorAll("[data-pdel]").forEach(el => el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        removePair(el.dataset.pdel);
      }));
    }
    const qBox = $("pair-quick-list");
    if (qBox) {
      qBox.innerHTML = QUICK_PAIRS.map(s => `
        <button type="button" class="pair-chip ${act.includes(s) ? "on" : ""}" data-padd="${s}">
          ${act.includes(s) ? "✓ " : "+ "}${s}
        </button>`).join("");
      qBox.querySelectorAll("[data-padd]").forEach(b => b.addEventListener("click", () => {
        if (act.includes(b.dataset.padd)) { selectSymbol(b.dataset.padd); if (pairModal) pairModal.classList.add("hidden"); }
        else addPairAndSwitch(b.dataset.padd);
      }));
    }
  }
  async function searchBrokerSymbols(q) {
    const box = $("pair-broker-list");
    if (!box) return;
    try {
      const d = await (await fetch(api_(`/api/mt5/symbols?q=${encodeURIComponent(q || "")}&limit=60`))).json();
      const act = new Set(d.active || []);
      box.innerHTML = (d.symbols || []).map(x => `
        <button type="button" class="pair-chip ${act.has(x.symbol) ? "on" : ""}" data-badd="${x.symbol}">
          ${act.has(x.symbol) ? "✓ " : "+ "}${x.symbol}
        </button>`).join("") || `<div class="dim-text">Tidak ditemukan di broker MT5</div>`;
      box.querySelectorAll("[data-badd]").forEach(b => b.addEventListener("click", () => addPairAndSwitch(b.dataset.badd)));
    } catch (e) { box.innerHTML = `<div class="dim-text">MT5 belum terhubung</div>`; }
  }
  if ($("btn-pair-mgr") && pairModal) {
    $("btn-pair-mgr").addEventListener("click", () => {
      pairModal.classList.remove("hidden");
      setPairMsg("");
      renderPairModal();
      searchBrokerSymbols($("pair-search")?.value || "");
    });
    $("btn-pair-close")?.addEventListener("click", () => pairModal.classList.add("hidden"));
    pairModal.addEventListener("click", (ev) => { if (ev.target === pairModal) pairModal.classList.add("hidden"); });
    let _pTimer = null;
    $("pair-search")?.addEventListener("input", (ev) => {
      clearTimeout(_pTimer);
      _pTimer = setTimeout(() => searchBrokerSymbols(ev.target.value), 250);
    });
    $("btn-pair-add-manual")?.addEventListener("click", () => addPairAndSwitch($("pair-search")?.value));
  }
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

  /* status koneksi: klik untuk set/clear URL backend bridge (tunnel) */
  $("conn-status").addEventListener("click", () => {
    const v = prompt(
      "URL backend (mis. https://xxxx.trycloudflare.com)\n"
      + "kosongkan untuk same-origin / default config.js:",
      API());
    if (v === null) return;
    setApiBase(v.trim());
  });

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
    const r = await fetch(api_("/api/research"));
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    renderResearch(await r.json());
  } catch (e) {
    $("rs-sig").innerHTML = `<div class="dim-text">Laporan belum tersedia: jalankan <code>python -m backend.research_report</code></div>`;
  }
}

async function restoreMode() {
  try {
    const h = await (await fetch(api_("/api/health"))).json();
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

  // simpan posisi sekarang ke localStorage
  const persist = () => {
    const cs = getComputedStyle(root);
    [["at-rail-w", "--rail-w"]]
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

  // tombol reset layout
  const rb = $("btn-reset-layout");
  if (rb) rb.addEventListener("click", () => {
    ["rail-w"].forEach(k => {
      localStorage.removeItem("at-" + k);
      root.style.removeProperty("--" + k);
    });
    location.reload();
  });

  // tema: glassmorphism / default (tersimpan di localStorage, default GLASS)
  const applyTheme = (t) => {
    document.documentElement.dataset.theme = t;
    const tb = $("btn-theme");
    if (!tb) return;
    tb.textContent = t === "glass" ? "THEME: GLASS" : "THEME: DEFAULT";
    tb.classList.toggle("chip-on", t === "glass");
  };
  applyTheme(localStorage.getItem("at-theme") || "glass");
  const tbBtn = $("btn-theme");
  if (tbBtn) tbBtn.addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "glass" ? "default" : "glass";
    localStorage.setItem("at-theme", next);
    applyTheme(next);
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
  return fetch(api_("/api/settings"), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      sensitivity: $("sel-sensitivity").value,
      exit_protocol: $("sel-exit").value,
      ...extra,
    }),
  }).then(r => r.json()).then(j => {
    if (j.signal_golden !== undefined) setSignalChip("chip-golden", "SIGNAL GOLDEN", j.signal_golden);
    if (j.signal_high_risk !== undefined) setSignalChip("chip-highrisk", "SIGNAL HIGH RISK", j.signal_high_risk);
    // snapshot diperbarui server (rebuild+broadcast); segarkan panel lokal sesuai simbol & TF aktif
    const params = [];
    if (state.activeTf) params.push(`tf=${encodeURIComponent(state.activeTf)}`);
    if (state.activeSymbol) params.push(`symbol=${encodeURIComponent(state.activeSymbol)}`);
    const sq = params.length ? `?${params.join("&")}` : "";
    return fetch(api_("/api/snapshot" + sq)).then(r => r.json()).then(s => { renderSnapshot(s); return j; });
  }).catch(() => {});
}

/* ============================== BOOT ============================== */
let _bootDataDone = false;
let _bootDataLoading = false;
async function loadInitialData() {
  if (_bootDataLoading) return;
  _bootDataLoading = true;
  // retry: server baru restart -> buffer belum siap (503); coba hingga siap
  for (let i = 0; i < 60; i++) {
    try {
      const savedSym = localStorage.getItem("at-symbol");
      const savedTf = localStorage.getItem("at-tf");
      const initQ = savedSym ? `?symbol=${encodeURIComponent(savedSym)}` : "";
      const snap = await (await fetch(api_("/api/snapshot" + initQ))).json();
      if (!snap.error) {
        const list = snap.symbols || [snap.symbol];
        state.activeSymbol = (savedSym && list.includes(savedSym)) ? savedSym : snap.symbol;
        renderSnapshot(snap);
        const wantTf = savedTf || snap.tf_entry || "1h";
        const ok = await switchTf(wantTf);
        if (!ok) await switchTf(snap.tf_entry || "1h");
        _bootDataDone = true;
        return;
      }
    } catch (e) {
      // simpan penyebabnya (diagnosa) — jangan diam-diam
      window.__loadErr = (e && e.message) ? e.message : String(e);
    }
    await new Promise(r => setTimeout(r, 1500));
  }
}

async function boot() {
  initChart();
  initSplitters();
  bindUI();
  restoreMode();
  await loadInitialData();
  connectWS();
  startPolling(state.pollOnly ? 4000 : 15000);
}
boot();

/* ============================== PWA ============================== */
if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost" || location.hostname === "127.0.0.1")) {
  addEventListener("load", () => {
    navigator.serviceWorker.register("./sw.js").catch(() => {});
  });
}
