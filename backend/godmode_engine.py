"""
GODMODE-style engine layer untuk Alpha-Trade
--------------------------------------------
Adaptasi semantik algoritma GODMODE SMC (hasil reverse-engineering halaman
godmode.my.id — lihat docs/GODMODE-ALGORITHM.md) di atas confluence_engine.

Komponen:
  - market_condition()   : [TRENDING] / [SIDEWAYS]
  - trend_lamps()        : 4 lampu tren — Micro EMA9 / Mini EMA20 / Macro EMA50 / Daily
  - multi_zones()        : zona S1..S3 (supply) & D1..D3 (demand), strength dalam poin
  - structure_label()    : BULLISH/BEARISH + "HH + HL STRUCTURE"
  - events_full()        : CHOCH/HH/HL/LH/SWEEP/TC (GODMODE menampilkan HL juga)
  - confluence_godmode() : checklist HTF(20) + Zone(20-30) + Sweep(20) + 2-Candle(20)
                           + bonus konteks (struktur/volume/MTF) sampai 100
  - classify_setup()     : GOLDEN SETUP / HTF SNIPER ENTRY / SWING REVERSAL /
                           A+ SNIPER CID / STANDARD
  - two_stage()          : Stage 1 "GET READY" (harga di zona) -> Stage 2 "VALID"
                           (rejection 2-candle / CHOCH di zona)
  - session_info()       : sesi WIB Asia/London/NY/Rollover + rekomendasi
  - ai_reasoning()       : penjelasan 3 baris SITUASI / KARENA / MAKA
  - forecast()           : proyeksi arah (slope EMA20 vs EMA50) + TP1/2/3 = 1/2/3 ATR,
                           SL = 1.5 ATR (semantik FORECAST v5.0)
"""
import numpy as np
import pandas as pd

from .confluence_engine import Config, atr as atr_fn, ema, rsi
from .smc_structure import structure_events


def pdh_pdl(df_1h):
    """Previous Day High/Low dari buffer 1h (smc.previous_high_low).
    Nilai = hari UTC sebelumnya; `broken` = harga sudah menembus level hari ini.
    Tanpa lookahead: hanya baris terakhir yang dipakai."""
    if df_1h is None or len(df_1h) < 48:
        return None
    try:
        from smartmoneyconcepts import smc
    except ImportError:
        return None
    ohlc = df_1h[["open", "high", "low", "close", "volume"]]
    last = smc.previous_high_low(ohlc, time_frame="1D").iloc[-1]
    ph, pl = float(last["PreviousHigh"]), float(last["PreviousLow"])
    if np.isnan(ph) or np.isnan(pl):
        return None
    return {"pdh": round(ph, 2), "pdl": round(pl, 2),
            "broken_high": bool(last["BrokenHigh"]), "broken_low": bool(last["BrokenLow"])}

PIP = 10.0  # XAUUSD: 1 pip = 0.1 pt -> strength poin = pt; 25 pips = 2.5 pt


# ----------------------------- Preset mode (header GODMODE) -----------------
# Tiap preset adalah KONFIGURASI ANALISIS yang berbeda sungguhan:
#   golden     : Elite — semua gate, AND ketat (min_confluence 4)
#   aggressive : Momentum Pulse — cukup 1 gate lain, konfirmasi 1 bar, RSI 50/50
#   standard   : Balanced — 3 dari 4 gate, konfirmasi 2 bar
#   precision  : Confluence sniper — 4/4 gate, zona 0.5 ATR, RSI 35/65
#   pingpong   : Sideways — TANPA syarat tren HTF, main di zona (RSI 30/70)
SENSITIVITY_PRESETS = {
    "golden":    {"min_confluence": 4, "confirm_window": 3, "cooldown": 10,
                  "rsi_pull_long": 40, "rsi_pull_short": 60, "vp_prox_atr": 0.75,
                  "atr_pct_low": 0.20, "atr_pct_high": 0.95, "gate_trend": True},
    "aggressive": {"min_confluence": 1, "confirm_window": 1, "cooldown": 5,
                   "rsi_pull_long": 50, "rsi_pull_short": 50, "atr_pct_low": 0.05,
                   "gate_trend": True},
    "standard":  {"min_confluence": 3, "confirm_window": 2, "cooldown": 8,
                  "rsi_pull_long": 40, "rsi_pull_short": 60, "gate_trend": True},
    "precision": {"min_confluence": 4, "confirm_window": 4, "cooldown": 20,
                  "vp_prox_atr": 0.5, "atr_pct_low": 0.10, "atr_pct_high": 0.90,
                  "rsi_pull_long": 35, "rsi_pull_short": 65, "gate_trend": True},
    "pingpong":  {"min_confluence": 3, "confirm_window": 2, "cooldown": 6,
                  "gate_trend": False, "rsi_pull_long": 30, "rsi_pull_short": 70,
                  "vp_prox_atr": 1.0, "atr_pct_low": 0.05},
}

# Exit protocol mengubah multipliier SL/TP ala GODMODE
EXIT_PRESETS = {
    "quick": {"tp1_rr": 1.5, "tp2_rr": 3.0, "sl_atr_max": 2.5, "sl_atr_min": 1.0},
    "nitro": {"tp1_rr": 1.0, "tp2_rr": 1.5, "sl_atr_max": 2.0, "sl_atr_min": 0.8},
    "hold":  {"tp1_rr": 2.0, "tp2_rr": 4.0, "sl_atr_max": 3.0, "sl_atr_min": 1.2,
              "cooldown": 30},
}

# kunci yang dikelola preset — dikembalikan ke default sebelum tiap apply
_MANAGED_KEYS = sorted({k for v in list(SENSITIVITY_PRESETS.values()) +
                        list(EXIT_PRESETS.values()) for k in v})
_DEFAULTS = {}


def apply_mode(cfg, sensitivity=None, exit_protocol=None):
    """Terapkan preset mode ke Config (mutasi in-place).
    Kunci yang dikelola preset DI-RESET ke default dulu, sehingga pindah
    aggressive -> golden tidak meninggalkan nilai lama menempel."""
    global _DEFAULTS
    if not _DEFAULTS:
        _DEFAULTS = {k: getattr(Config(), k) for k in _MANAGED_KEYS}
    for k in _MANAGED_KEYS:                          # reset ke default
        setattr(cfg, k, _DEFAULTS[k])
    if sensitivity:
        for k, v in SENSITIVITY_PRESETS.get(sensitivity, {}).items():
            setattr(cfg, k, v)
    if exit_protocol:
        for k, v in EXIT_PRESETS.get(exit_protocol, {}).items():
            setattr(cfg, k, v)
    return cfg


# ----------------------------- Kondisi pasar --------------------------------
def adx_value(df, length=14):
    """ADX via pandas-ta-classic (opsional); None bila lib tidak terpasang."""
    try:
        import pandas_ta_classic as ta
        adx_df = ta.adx(df["high"], df["low"], df["close"], length=length)
        return float(adx_df.iloc[-1, 0])
    except Exception:
        return None


def market_condition(df, lookback=60):
    """[TRENDING] / [SIDEWAYS] — prioritas ADX (>=25 tren, <=18 sideways);
    fallback ke heuristik range-vs-ATR bila pandas-ta-classic tidak terpasang."""
    w = df.tail(lookback)
    hi, lo = float(w["high"].max()), float(w["low"].min())
    rng = hi - lo
    if rng <= 0:
        return "SIDEWAYS", hi, lo
    adx = adx_value(df)
    if adx is not None:
        return ("TRENDING" if adx >= 25 else "SIDEWAYS" if adx <= 18 else "TRENDING"), hi, lo
    c = float(df["close"].iloc[-1])
    pos = (c - lo) / rng
    a = float(atr_fn(df).iloc[-1])
    narrow = rng < 6 * a
    sideways = narrow and 0.35 <= pos <= 0.65
    return ("SIDEWAYS" if sideways else "TRENDING"), hi, lo


# ----------------------------- Mode AUTO adaptif -----------------------------
# Sensitivity dipilih otomatis mengikuti kondisi market per bar:
#   TRENDING -> standard (trend-following seimbang)
#   SIDEWAYS -> pingpong (tanpa gate tren HTF, main di zona RSI 30/70)
AUTO_REGIME_MODE = {"TRENDING": "standard", "SIDEWAYS": "pingpong"}


def _regime_series(df, lookback=60):
    """Label rezim per bar (TRENDING/SIDEWAYS) TANPA lookahead — semantik sama
    persis dengan market_condition() tapi dihitung pada jendela rolling."""
    h, l, c = df["high"], df["low"], df["close"]
    hi = h.rolling(lookback).max()
    lo = l.rolling(lookback).min()
    rng = (hi - lo)
    a = atr_fn(df)
    pos = (c - lo) / rng.replace(0, np.nan)
    heur_side = ((rng < 6 * a) & (pos >= 0.35) & (pos <= 0.65)).fillna(False)
    try:
        import pandas_ta_classic as ta
        adx = ta.adx(h, l, c, length=14)["ADX_14"].reindex(df.index)
    except Exception:
        adx = None
    if adx is None:
        return heur_side.astype(bool)
    # sama dgn market_condition: ADX >=25 trending, <=18 sideways, di antaranya trending
    side = (adx <= 18).fillna(False)
    return side.astype(bool)


def _hysteresis(side, stable_bars):
    """Label baru harus bertahan `stable_bars` bar berturut-turut sebelum rezim
    berpindah — mencegah mode bolak-balik pada transisi pendek."""
    arr = side.to_numpy(dtype=bool)
    res = np.empty(len(arr), dtype=bool)
    cur = bool(arr[0])
    cand, n = None, 0
    for i, v in enumerate(arr):
        if v == cur:
            cand, n = None, 0
        elif cand == v:
            n += 1
        else:
            cand, n = v, 1
        if n >= stable_bars:
            cur = v
            cand, n = None, 0
        res[i] = cur
    return pd.Series(res, side.index)


def generate_signals_auto(df, htf_df=None, exit_protocol=None, stable_bars=8,
                          lookback=60, warmup=260):
    """generate_signals versi ADAPTIF: tiap segmen rezim dihitung dengan preset
    sensitivity-nya masing-masing (standard utk TRENDING, pingpong utk SIDEWAYS).
    Rezim per-bar memakai hanya data masa lalu; segmen diproses dengan blok
    warmup `warmup` bar agar indikator panjang (EMA200/volume profile) matang.
    Format keluaran sama dengan generate_signals."""
    from .confluence_engine import Config, generate_signals

    if len(df) < 300:
        return generate_signals(df, Config(), htf_df=htf_df)

    lab = _hysteresis(_regime_series(df, lookback), stable_bars).to_numpy()
    change = np.flatnonzero(np.diff(lab)) + 1
    starts = np.concatenate([[0], change])
    ends = np.concatenate([change, [len(lab)]])

    parts = []
    for s, e in zip(starts, ends):
        kondisi = "SIDEWAYS" if lab[s] else "TRENDING"
        seg_cfg = apply_mode(Config(), sensitivity=AUTO_REGIME_MODE[kondisi],
                             exit_protocol=exit_protocol)
        i0 = max(0, s - warmup)
        sig = generate_signals(df.iloc[i0:e], seg_cfg, htf_df=htf_df)
        if len(sig):
            parts.append(sig.loc[df.index[s]:df.index[e - 1]])

    if not parts:
        return generate_signals(df, Config(), htf_df=htf_df).iloc[0:0]
    out = pd.concat(parts).sort_index()
    out = out[~out.index.duplicated(keep="last")]

    # cooldown global pada jarak BAR asli (bukan posisi antar sinyal)
    positions = df.index.get_indexer(out.index)
    cd, keep, last = 10, [], -10**9
    for t, p in zip(out.index, positions):
        if p - last < cd:
            continue
        last = p
        keep.append(t)
    return out.loc[keep]


# ----------------------------- Lampu tren -----------------------------------
def trend_lamps(df, df_daily=None):
    """Micro(EMA9) / Mini(EMA20) / Macro(EMA50) / Daily — UP/DOWN/FLAT."""
    c = df["close"]
    def lamp(series, price):
        if len(series) < 2:
            return "FLAT"
        slope = float(series.iloc[-1] - series.iloc[-3]) if len(series) > 3 else float(series.iloc[-1] - series.iloc[-2])
        if price > series.iloc[-1] and slope > 0:
            return "UP"
        if price < series.iloc[-1] and slope < 0:
            return "DOWN"
        return "FLAT"
    p = float(c.iloc[-1])
    e9, e20, e50 = ema(c, 9), ema(c, 20), ema(c, 50)
    daily = "FLAT"
    if df_daily is not None and len(df_daily) > 60:
        dd = df_daily["close"]
        if float(dd.iloc[-1]) > float(ema(dd, 50).iloc[-1]):
            daily = "UP"
        else:
            daily = "DOWN"
    return {"Micro (EMA 9)": lamp(e9, p), "Mini (EMA 20)": lamp(e20, p),
            "Macro (EMA 50)": lamp(e50, p), "Daily Trend": daily}


# ----------------------------- Zona multi-level -----------------------------
def _pivots(df, L=5, lookback=300):
    """Semua pivot high/low terkonfirmasi (bar p+L), terbaru terakhir."""
    w = df.tail(lookback)
    h, l = w["high"].to_numpy(), w["low"].to_numpy()
    highs, lows = [], []
    for p in range(L, len(w) - L):
        if h[p] == h[p - L:p + L + 1].max():
            highs.append((w.index[p + L], float(h[p])))
        if l[p] == l[p - L:p + L + 1].min():
            lows.append((w.index[p + L], float(l[p])))
    return highs, lows


def multi_zones(df, a, n_levels=3, L=5, lookback=300):
    """S1..S3 supply (dari atas) & D1..D3 demand (dari bawah), strength = lebar poin.
    Setiap zona membawa `time` (bar konfirmasi pivot) untuk digambar sebagai band."""
    c = float(df["close"].iloc[-1])
    highs, lows = _pivots(df, L, lookback)
    sup = [{"top": p + 0.5 * a, "bottom": p - 0.5 * a, "price": p, "time": t}
           for t, p in highs[-n_levels:]]
    dem = [{"top": p + 0.5 * a, "bottom": p - 0.5 * a, "price": p, "time": t}
           for t, p in lows[-n_levels:]]
    sup = sorted(sup, key=lambda z: z["top"], reverse=True)   # S1 terdekat dari atas
    dem = sorted(dem, key=lambda z: z["bottom"], reverse=True)  # D1 terdekat dari bawah
    out = {"supply": [], "demand": []}
    for i, z in enumerate(sup, 1):
        out["supply"].append({"level": f"S{i}", "top": round(z["top"], 2),
                              "bottom": round(z["bottom"], 2),
                              "strength_pts": round(z["top"] - z["bottom"], 2),
                              "dist_pts": round(z["bottom"] - c, 2),
                              "time": str(z["time"])})
    for i, z in enumerate(dem, 1):
        out["demand"].append({"level": f"D{i}", "top": round(z["top"], 2),
                              "bottom": round(z["bottom"], 2),
                              "strength_pts": round(z["top"] - z["bottom"], 2),
                              "dist_pts": round(c - z["top"], 2),
                              "time": str(z["time"])})
    return out


# ----------------------------- Struktur -------------------------------------
def structure_label(df, L=5):
    """BULLISH/BEARISH + 'HH + HL STRUCTURE' seperti panel GODMODE."""
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    his, los = [], []
    for p in range(L, len(df) - L):
        if h[p] == h[p - L:p + L + 1].max():
            his.append(h[p])
        if l[p] == l[p - L:p + L + 1].min():
            los.append(l[p])
    hh = len(his) >= 2 and his[-1] > his[-2]
    hl = len(los) >= 2 and los[-1] > los[-2]
    lh = len(his) >= 2 and his[-1] < his[-2]
    ll = len(los) >= 2 and los[-1] < los[-2]
    if hh and hl:
        return "BULLISH", "HH + HL STRUCTURE"
    if lh and ll:
        return "BEARISH", "LH + LL STRUCTURE"
    if hh or hl:
        return "BULLISH", "HH + HL STRUCTURE" if hh else "HL PENDING HH"
    if lh or ll:
        return "BEARISH", "LH + LL STRUCTURE" if ll else "LH PENDING LL"
    return "RANGING", "RANGE — TUNGGU BREAK"


def events_full(df, L=5, sweep_lookback=20, max_events=8):
    """Event GODMODE: CHOCH/HH/HL/LH/SWEEP/TC dengan deskripsi persis ala panel."""
    idx = df.index
    rows = []
    ev = structure_events(df, L)
    for t in idx[ev["choch_bull"]]:
        rows.append((t, float(df.loc[t, "close"]), "CHOCH",
                     "Bearish-Bullish flip — LH broken upward", "UP"))
    for t in idx[ev["choch_bear"]]:
        rows.append((t, float(df.loc[t, "close"]), "CHOCH",
                     "Bullish-Bearish flip — HL broken downward", "DOWN"))

    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    last_hi = last_lo = None
    trend = 0
    for p in range(L, len(df) - L):
        conf = idx[p + L]
        if h[p] == h[p - L:p + L + 1].max():
            if last_hi is not None:
                if h[p] > h[last_hi]:
                    rows.append((conf, float(h[p]), "HH", "Higher High — Last bullish swing high", "UP"))
                elif h[p] < h[last_hi]:
                    rows.append((conf, float(h[p]), "LH", "Lower High — Bearish resistance level", "DOWN"))
            last_hi = p
        if l[p] == l[p - L:p + L + 1].min():
            if last_lo is not None:
                if l[p] > l[last_lo]:
                    rows.append((conf, float(l[p]), "HL", "Higher Low — Bullish support level", "UP"))
                elif l[p] < l[last_lo]:
                    rows.append((conf, float(l[p]), "LL", "Lower Low — Last bearish swing low", "DOWN"))
            last_lo = p

    prev_lo = df["low"].shift().rolling(sweep_lookback).min()
    prev_hi = df["high"].shift().rolling(sweep_lookback).max()
    sw_b = (df["low"] < prev_lo) & (df["close"] > prev_lo)
    sw_s = (df["high"] > prev_hi) & (df["close"] < prev_hi)
    for t in idx[sw_b].tolist()[-1:]:
        rows.append((t, float(prev_lo.loc[t]), "SWEEP",
                     f"Liquidity grabbed below {prev_lo.loc[t]:.2f} — bullish reversal signal", "UP"))
    for t in idx[sw_s].tolist()[-1:]:
        rows.append((t, float(prev_hi.loc[t]), "SWEEP",
                     f"Liquidity grabbed above {prev_hi.loc[t]:.2f} — bearish reversal signal", "DOWN"))

    # TC (trend change) dari choch: label kedua seperti panel GODMODE
    for t in idx[ev["choch_bull"]]:
        rows.append((t, float(df.loc[t, "close"]), "TC",
                     "Bullish trend change — LL to HL structure flip", "UP"))
    for t in idx[ev["choch_bear"]]:
        rows.append((t, float(df.loc[t, "close"]), "TC",
                     "Bearish trend change — HH to LH structure flip", "DOWN"))

    rows.sort(key=lambda r: r[0])
    return [{"time": str(t), "tag": tag, "price": round(price, 2), "desc": desc, "dir": d}
            for t, price, tag, desc, d in rows[-max_events:]]


# ----------------------------- Confluence GODMODE ---------------------------
def confluence_godmode(df, df_htf, cfg, zones_multi, vol_flow, htf_label=None):
    """Skor 0-100: HTF 20 + Zone 20-30 + Sweep 20 + 2-Candle 20 + bonus konteks."""
    from .confluence_engine import htf_trend
    trend = htf_trend(df, cfg, df_htf)
    htf_dir = int(trend.iloc[-1])
    c = float(df["close"].iloc[-1])

    # zona terdekat (jarak dalam poin harga)
    zm = zones_multi
    a = float(atr_fn(df).iloc[-1])
    at_supply = any(z["bottom"] <= c <= z["top"] for z in zm["supply"])
    at_demand = any(z["bottom"] <= c <= z["top"] for z in zm["demand"])
    near_supply = any(-0.6 * a <= z["dist_pts"] <= 0.5 * a for z in zm["supply"])
    near_demand = any(-0.6 * a <= z["dist_pts"] <= 0.5 * a for z in zm["demand"])

    # zona kuat -> bobot 30 (strength > 1 ATR), else 20
    strong_zone = any(z["strength_pts"] > a for z in zm["supply"] + zm["demand"])
    zone_pts = 30 if (strong_zone and (at_supply or at_demand or near_supply or near_demand)) \
        else 20 if (at_supply or at_demand or near_supply or near_demand) else 0

    # sweep dalam lookback
    w = df.tail(cfg.sweep_lookback + 2)
    prev_lo = df["low"].shift().rolling(cfg.sweep_lookback).min().iloc[-1]
    prev_hi = df["high"].shift().rolling(cfg.sweep_lookback).max().iloc[-1]
    sweep_bull = bool((w["low"] < prev_lo).any())
    sweep_bear = bool((w["high"] > prev_hi).any())
    sweep_ok = sweep_bull or sweep_bear

    # 2-candle rejection
    o, cl = df["open"].iloc[-1], df["close"].iloc[-1]
    po, pcl = df["open"].iloc[-2], df["close"].iloc[-2]
    two_bull = bool(cl > o and pcl < po and cl >= po)
    two_bear = bool(cl < o and pcl > po and cl <= po)
    two_ok = two_bull or two_bear

    checks = [
        {"label": f"HTF {(htf_label or cfg.htf).upper()} Trend",
         "val": "BULLISH" if htf_dir == 1 else "BEARISH" if htf_dir == -1 else "RANGING",
         "ok": htf_dir != 0, "pts": 20 if htf_dir != 0 else 0},
        {"label": "Zone",
         "val": "AT SUPPLY" if (at_supply or near_supply) else "AT DEMAND" if (at_demand or near_demand) else "NEUTRAL",
         "ok": zone_pts > 0, "pts": zone_pts},
        {"label": "Sweep",
         "val": "BEARISH SWEEP" if sweep_bear else "BULLISH SWEEP" if sweep_bull else "WAITING",
         "ok": sweep_ok, "pts": 20 if sweep_ok else 0},
        {"label": "2-Candle",
         "val": "CONFIRMED" if two_ok else "WAITING",
         "ok": two_ok, "pts": 30 if two_ok else 0},
    ]
    base = sum(ch["pts"] for ch in checks)

    # bonus konteks (fakta tersembunyi di GODMODE: skor 98 saat checklist 70)
    struct, _ = structure_label(df, 5)
    bonus = 0
    if struct == "BULLISH" and htf_dir == 1:
        bonus += 10
    if struct == "BEARISH" and htf_dir == -1:
        bonus += 10
    buyers = vol_flow.get("buyers_pct", 50) if vol_flow else 50
    if (htf_dir == 1 and buyers >= 60) or (htf_dir == -1 and buyers <= 40):
        bonus += 8
    bias = heatmap_bias(df)                    # bias MTF dari TF entry sendiri
    if (htf_dir == 1 and bias == "BULL") or (htf_dir == -1 and bias == "BEAR"):
        bonus += 10
    score = min(100, base + bonus)
    action = "EXECUTE" if score >= 75 else "MONITOR" if score >= 50 else "WAIT"
    return {"score": score, "action": action, "checks": checks, "bonus": bonus,
            "htf_dir": htf_dir, "at_supply": at_supply or near_supply,
            "at_demand": at_demand or near_demand, "sweep_ok": sweep_ok,
            "two_ok": two_ok}


def heatmap_bias(df):
    """Bias single-TF: BULL/BEAR/RANG dari stack EMA."""
    c = df["close"]
    e9, e20, e50 = ema(c, 9), ema(c, 20), ema(c, 50)
    if e9.iloc[-1] > e20.iloc[-1] > e50.iloc[-1]:
        return "BULL"
    if e9.iloc[-1] < e20.iloc[-1] < e50.iloc[-1]:
        return "BEAR"
    return "RANG"


# ----------------------------- Klasifikasi setup ----------------------------
def classify_setup(cf, choch_recent, div_recent=False):
    """Label setup ala track record GODMODE."""
    aligned = cf["htf_dir"] != 0
    if aligned and cf["sweep_ok"] and cf["two_ok"] and cf["score"] >= 90 and div_recent:
        return "A+ SNIPER CID"
    if aligned and cf["sweep_ok"] and (cf["at_supply"] or cf["at_demand"]) and cf["two_ok"]:
        return "GOLDEN SETUP"
    if aligned and (cf["at_supply"] or cf["at_demand"]):
        return "HTF SNIPER ENTRY"
    if choch_recent:
        return "SWING REVERSAL"
    return "STANDARD"


def choch_recent(df, L=5, window=10):
    ev = structure_events(df, L)
    return bool((ev["choch_bull"] | ev["choch_bear"]).tail(window).any())


def rsi_divergence_recent(df, L=5, window=15):
    """Divergence RSI sederhana utk label A+ SNIPER CID."""
    r = rsi(df["close"], 14)
    l, h = df["low"].to_numpy(), df["high"].to_numpy()
    rv = r.to_numpy()
    n = len(df)
    for p in range(max(L, n - window), n - L):
        if l[p] == l[p - L:p + L + 1].min() and rv[p] > rv[p - L]:
            return True
        if h[p] == h[p - L:p + L + 1].max() and rv[p] < rv[p - L]:
            return True
    return False


# ----------------------------- 2-Stage alert --------------------------------
def two_stage(df, zones_multi):
    """Stage 1 GET READY (harga menyentuh zona) -> Stage 2 VALID (rejection di zona)."""
    c = float(df["close"].iloc[-1])
    o, cl = df["open"].iloc[-1], df["close"].iloc[-1]
    po, pcl = df["open"].iloc[-2], df["close"].iloc[-2]
    in_supply = any(z["bottom"] <= c <= z["top"] for z in zones_multi["supply"])
    in_demand = any(z["bottom"] <= c <= z["top"] for z in zones_multi["demand"])
    reject_bull = cl > o and cl > po          # candle bull menutup di atas open sebelumnya
    reject_bear = cl < o and cl < po
    if in_demand:
        return {"stage": 2, "label": "VALID SIGNAL (BUY)",
                "detail": "Rejection di Demand terkonfirmasi"} if reject_bull else \
               {"stage": 1, "label": "GET READY (BUY)",
                "detail": "Harga di Demand — tunggu candle rejection"}
    if in_supply:
        return {"stage": 2, "label": "VALID SIGNAL (SELL)",
                "detail": "Rejection di Supply terkonfirmasi"} if reject_bear else \
               {"stage": 1, "label": "GET READY (SELL)",
                "detail": "Harga di Supply — tunggu candle rejection"}
    return {"stage": 0, "label": "SCANNING", "detail": "Harga di No Trade Zone (tengah range)"}


# ----------------------------- Sesi pasar (WIB) -----------------------------
SESSIONS = [
    ("Asia (Tokyo / Sydney)", 6, 14, "Rendah", "Hindari, sinyal sedikit"),
    ("London (Eropa)", 14, 19, "Tinggi", "Waktu terbaik untuk trading"),
    ("New York (US Overlap)", 19, 3, "Sangat Tinggi", "Potensi besar, hati-hati"),
    ("Rollover (Dini Hari)", 3, 6, "Rendah", "Spread lebar / volatilitas rendah"),
]


def session_info(now_utc=None):
    """Sesi aktif dari jam UTC -> WIB (UTC+7)."""
    now = pd.Timestamp.now(tz="UTC") if now_utc is None else pd.Timestamp(now_utc)
    wib_hour = (now + pd.Timedelta(hours=7)).hour
    for name, a, b, vol, rec in SESSIONS:
        if a < b:
            inside = a <= wib_hour < b
        else:                                     # melewati tengah malam
            inside = wib_hour >= a or wib_hour < b
        if inside:
            return {"session": name, "volatility": vol, "recommendation": rec,
                    "wib_hour": wib_hour}
    return {"session": "Antar-sesi", "volatility": "Rendah",
            "recommendation": "Tunggu sesi London/NY", "wib_hour": wib_hour}


# ----------------------------- AI reasoning ---------------------------------
def ai_reasoning(df, zones_multi, two_stage_info, cf):
    """Penjelasan ala panel AI ANALYSIS: SITUASI / KARENA / MAKA."""
    cond, _, _ = market_condition(df)
    c = float(df["close"].iloc[-1])
    situasi = f"GOLDEN SMC: {'[TRENDING]' if cond == 'TRENDING' else '[SIDEWAYS]'} — harga {c:.2f}"
    if two_stage_info["stage"] == 0:
        karena = "Karena harga berada di tengah (No Trade Zone) dan belum ada pola manipulasi (Sweep) dari area Supply/Demand yang valid."
        maka = "Maka STANDBY. Biarkan harga mendatangi zona institusional terlebih dahulu."
    elif two_stage_info["stage"] == 1:
        karena = f"Karena harga sudah menyentuh zona {two_stage_info['label'].split('(')[1][:-1]} namun belum ada rejection 2-candle."
        maka = "Maka GET READY. Jangan entry dulu — tunggu candle rejection di dalam zona."
    else:
        sisi = two_stage_info["label"].split("(")[1][:-1]
        karena = f"Karena rejection terkonfirmasi di zona {sisi} dan skor confluence {cf['score']}/100."
        maka = f"Maka {cf['action']}. Eksekusi sesuai setup {two_stage_info['label']} dengan SL/TP dari engine."
    return {"situasi": situasi, "karena": karena, "maka": maka}


# ----------------------------- FVG & Order Blocks ---------------------------
def fvg_zones(df, max_age=80, max_gaps=5):
    """Fair Value Gap (3-candle gap) yang belum ter-fill penuh.
    Bull: low[i] > high[i-2] -> gap = (high[i-2], low[i]).
    Bear: high[i] < low[i-2] -> gap = (high[i], low[i-2])."""
    h, l, o, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    n = len(df)
    out = []
    for i in range(2, n):
        if l[i] > h[i - 2]:                                   # bullish gap
            top, bottom = float(l[i]), float(h[i - 2])
            kind = "bull"
        elif h[i] < l[i - 2]:                                 # bearish gap
            top, bottom = float(l[i - 2]), float(h[i])
            kind = "bear"
        else:
            continue
        # ter-fill penuh bila harga berikutnya menembus seluruh gap
        if kind == "bull" and i + 1 < n and l[i + 1:].min() <= bottom:
            continue
        if kind == "bear" and i + 1 < n and h[i + 1:].max() >= top:
            continue
        age = n - 1 - i
        if age <= max_age:
            out.append({"kind": kind, "top": round(top, 2), "bottom": round(bottom, 2),
                        "age": int(age)})
    out.sort(key=lambda g: g["age"])                          # terbaru dulu
    return out[:max_gaps]


def order_blocks(df, L=5, max_blocks=4, lookback=250):
    """Order Block = candle berlawanan terakhir sebelum impuls yang mem-break
    struktur (BOS/CHoCH). Aktif bila harga belum menembus balik melewati OB."""
    w = df.tail(lookback)
    ev = structure_events(w, L)
    o, h, l, c = (w[k].to_numpy() for k in ("open", "high", "low", "close"))
    n = len(w)
    blocks = []
    for kind, col in (("bull", "bos_bull"), ("bull", "choch_bull"),
                      ("bear", "bos_bear"), ("bear", "choch_bear")):
        bars = list(w.index[ev[col]])
        for t in bars[-2:]:                                   # break terbaru per jenis
            i = w.index.get_loc(t)
            j = i - 1
            if kind == "bull":                                # cari candle bearish terakhir
                while j >= 0 and c[j] >= o[j]:
                    j -= 1
            else:                                             # candle bullish terakhir
                while j >= 0 and c[j] <= o[j]:
                    j -= 1
            if j < 0:
                continue
            top, bottom = float(h[j]), float(l[j])
            # sudah dimitigasi penuh?
            if kind == "bull" and i + 1 < n and l[i + 1:].min() <= bottom:
                continue
            if kind == "bear" and i + 1 < n and h[i + 1:].max() >= top:
                continue
            blocks.append({"kind": kind, "top": round(top, 2), "bottom": round(bottom, 2),
                           "time": str(w.index[j])})
    # dedup by (kind, top, bottom) + ambil terbaru
    seen, uniq = set(), []
    for b in blocks:
        key = (b["kind"], b["top"], b["bottom"])
        if key not in seen:
            seen.add(key)
            uniq.append(b)
    return uniq[:max_blocks]


# ----------------------------- Forecast v5.0 --------------------------------
def forecast(df, entry_hint=None):
    """Proyeksi FORECAST: arah dari slope EMA20 + posisi vs EMA50;
    TP1/TP2/TP3 = 1x/2x/3x ATR(14), SL = 1.5x ATR."""
    c = df["close"]
    e20, e50 = ema(c, 20), ema(c, 50)
    slope = float(e20.iloc[-1] - e20.iloc[-5])
    above = float(c.iloc[-1]) > float(e50.iloc[-1])
    direction = "BULLISH" if (slope > 0 and above) else "BEARISH" if (slope < 0 and not above) else "NEUTRAL"
    a = float(atr_fn(df).iloc[-1])
    base = float(entry_hint if entry_hint is not None else c.iloc[-1])
    if direction == "BEARISH":
        levels = {"tp1": base - a, "tp2": base - 2 * a, "tp3": base - 3 * a, "sl": base + 1.5 * a}
    else:
        levels = {"tp1": base + a, "tp2": base + 2 * a, "tp3": base + 3 * a, "sl": base - 1.5 * a}
    return {"direction": direction, "slope_ema20": round(slope, 4),
            "above_ema50": above, "atr": round(a, 2), **{k: round(v, 2) for k, v in levels.items()}}
