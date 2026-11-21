"""
Multi-Indicator Confluence Signal Engine
----------------------------------------
Sinyal LONG/SHORT hanya muncul jika SEMUA gate lolos:
  1. TREND      : EMA50 vs EMA200 di timeframe macro (HTF) + stack EMA 9/20/50 di TF entry
  2. MOMENTUM   : RSI (pullback-recovery / divergence) + MACD (cross / histogram)
  3. VOLUME     : posisi harga terhadap Volume Profile (POC / VAL / VAH)
  4. PRICE ACTION: engulfing / pin bar / liquidity sweep (konteks SMC)
  5. VOLATILITY : filter ATR + SL/TP dinamis berbasis ATR

Input : DataFrame index=DatetimeIndex, kolom open, high, low, close, volume
Output: DataFrame dengan kolom signal (1/-1/0), entry, sl, tp1, tp2, score, reason
"""
from dataclasses import dataclass
import numpy as np
import pandas as pd


@dataclass
class Config:
    htf: str = "4h"                 # timeframe macro untuk tren
    rsi_len: int = 14
    rsi_pull_long: float = 40       # pullback di uptrend
    rsi_pull_short: float = 60      # pullback di downtrend
    macd_fast: int = 12
    macd_slow: int = 26
    macd_sig: int = 9
    atr_len: int = 14
    sl_atr_min: float = 1.0         # jarak SL minimum (x ATR)
    sl_atr_max: float = 2.5         # jarak SL maksimum (x ATR)
    sl_buffer_atr: float = 0.5      # buffer di luar swing/wick (aman dari sweep tipis)
    tp1_rr: float = 1.5
    tp2_rr: float = 3.0
    confirm_window: int = 3         # event (cross/divergence) berlaku N bar
    vp_lookback: int = 200
    vp_bins: int = 48
    vp_value_area: float = 0.70
    vp_prox_atr: float = 0.75       # "dekat level" = dalam 0.75 ATR
    pivot_len: int = 5
    sweep_lookback: int = 20
    atr_pct_low: float = 0.20       # skip jika volatilitas terlalu rendah
    atr_pct_high: float = 0.95      # skip jika terlalu ekstrem
    min_confluence: int = 4         # dari 4 gate (MOM/VP/PA/VOL) harus lolos; 4 = AND ketat lama
    gate_trend: bool = True         # False = PING PONG (tanpa syarat tren HTF)
    allow_long: bool = True         # filter desain: izinkan sinyal long
    allow_short: bool = True        # filter desain: izinkan sinyal short
    session_utc: tuple = None       # mis. (7, 21) = hanya sinyal jam 07:00-20:59 UTC
    adx_min: float = 0.0            # filter regime: ADX >= nilai (0 = mati)
    fib_gate: bool = False          # premium/discount: LONG hanya di bawah EQ 0.5, SHORT di atas
    fib_lookback: int = 120         # rentang dealing-range utk level fib
    round_gate: bool = False        # kesadaran harga psikologis (angka bulat)
    round_prox_atr: float = 0.25    # "dekat level bulat" = dalam 0.25 ATR
    round_entry_gate: bool = True   # False = hanya sesuaikan SL/TP, entry bebas
    atr_rank_window: int = 500
    cooldown: int = 10              # jeda antar sinyal (bar)
    risk_pct: float = 1.0           # % ekuitas per trade


# ----------------------------- Indikator ------------------------------------
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def macd(close, f, s, sig):
    line = ema(close, f) - ema(close, s)
    signal = ema(line, sig)
    return line, signal, line - signal


def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def volume_profile_levels(df, cfg):
    """POC, VAL, VAH rolling; window selalu bar sebelumnya (tanpa lookahead)."""
    c, v = df["close"].to_numpy(), df["volume"].to_numpy()
    n = len(df)
    poc = np.full(n, np.nan); val = poc.copy(); vah = poc.copy()
    for i in range(cfg.vp_lookback, n):
        w_c, w_v = c[i - cfg.vp_lookback:i], v[i - cfg.vp_lookback:i]
        hist, edges = np.histogram(w_c, bins=cfg.vp_bins, weights=w_v)
        if hist.sum() == 0:
            continue
        centers = (edges[:-1] + edges[1:]) / 2
        p = int(hist.argmax())
        lo = hi = p
        acc, target = hist[p], hist.sum() * cfg.vp_value_area
        while acc < target and (lo > 0 or hi < len(hist) - 1):
            below = hist[lo - 1] if lo > 0 else -1
            above = hist[hi + 1] if hi < len(hist) - 1 else -1
            if above >= below:
                hi += 1; acc += hist[hi]
            else:
                lo -= 1; acc += hist[lo]
        poc[i], val[i], vah[i] = centers[p], centers[lo], centers[hi]
    return (pd.Series(poc, df.index), pd.Series(val, df.index),
            pd.Series(vah, df.index))


# ----------------------------- Divergence -----------------------------------
def divergence(df, rsi_s, L):
    """Regular divergence berbasis pivot. Sinyal muncul di bar konfirmasi pivot."""
    low, high, r = df["low"].to_numpy(), df["high"].to_numpy(), rsi_s.to_numpy()
    n = len(df)
    bull = np.zeros(n, bool); bear = np.zeros(n, bool)
    last_lo = last_hi = None
    for p in range(L, n - L):
        conf = p + L  # baru terkonfirmasi L bar setelah pivot
        if low[p] == low[p - L:p + L + 1].min():
            if last_lo and low[p] < low[last_lo] and r[p] > r[last_lo]:
                bull[conf] = True
            last_lo = p
        if high[p] == high[p - L:p + L + 1].max():
            if last_hi and high[p] > high[last_hi] and r[p] < r[last_hi]:
                bear[conf] = True
            last_hi = p
    return pd.Series(bull, df.index), pd.Series(bear, df.index)


# ----------------------------- Price Action ---------------------------------
def price_action(df, cfg):
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    body = (c - o).abs()
    rng = (h - l).replace(0, np.nan)
    up_w = h - np.maximum(o, c)
    lo_w = np.minimum(o, c) - l

    bull_engulf = (c > o) & (c.shift() < o.shift()) & (c >= o.shift()) & (o <= c.shift())
    bear_engulf = (c < o) & (c.shift() > o.shift()) & (c <= o.shift()) & (o >= c.shift())
    hammer = (lo_w >= 2 * body) & (up_w <= body) & (lo_w / rng > 0.55)
    shooting = (up_w >= 2 * body) & (lo_w <= body) & (up_w / rng > 0.55)

    prev_low = l.shift().rolling(cfg.sweep_lookback).min()
    prev_high = h.shift().rolling(cfg.sweep_lookback).max()
    sweep_bull = (l < prev_low) & (c > prev_low)     # sweep sell-side liquidity
    sweep_bear = (h > prev_high) & (c < prev_high)   # sweep buy-side liquidity

    return (bull_engulf | hammer | sweep_bull, bear_engulf | shooting | sweep_bear,
            sweep_bull, sweep_bear)


# ----------------------------- Engine ---------------------------------------
def adx_wilder(d, n=14):
    """ADX (Wilder) tanpa dependency eksternal — untuk filter regime trending."""
    up = d["high"].diff()
    dn = -d["low"].diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=d.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=d.index)
    tr = pd.concat([d["high"] - d["low"],
                    (d["high"] - d["close"].shift()).abs(),
                    (d["low"] - d["close"].shift()).abs()], axis=1).max(axis=1)
    atr_w = tr.ewm(alpha=1 / n, min_periods=n).mean()
    pdi = 100 * plus_dm.ewm(alpha=1 / n, min_periods=n).mean() / atr_w
    mdi = 100 * minus_dm.ewm(alpha=1 / n, min_periods=n).mean() / atr_w
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, min_periods=n).mean()


def htf_trend(df, cfg, htf_df=None):
    """+1 bullish / -1 bearish / 0 netral, dari EMA50 vs EMA200 di HTF.
    Memakai bar HTF yang SUDAH close (shift 1) agar tidak lookahead.
    `htf_df` (opsional): OHLCV HTF native dari feed (mis. candle 4h langsung
    dari Binance) — dipakai apa adanya, tanpa resample dari TF entry."""
    if htf_df is not None and len(htf_df):
        h = htf_df[["open", "high", "low", "close", "volume"]].copy()
    else:
        h = df.resample(cfg.htf).agg({"open": "first", "high": "max", "low": "min",
                                      "close": "last", "volume": "sum"}).dropna()
    e50, e200 = ema(h["close"], 50), ema(h["close"], 200)
    t = pd.Series(0, h.index)
    t[(h["close"] > e50) & (e50 > e200) & (e200.diff() > 0)] = 1
    t[(h["close"] < e50) & (e50 < e200) & (e200.diff() < 0)] = -1
    return t.shift(1).reindex(df.index, method="ffill").fillna(0)


def _recent(s, n):
    return s.rolling(n, min_periods=1).max().astype(bool)


def generate_signals(df, cfg=Config(), htf_df=None):
    d = df.copy()
    c = d["close"]

    # indikator
    d["ema9"], d["ema20"], d["ema50"], d["ema200"] = (ema(c, n) for n in (9, 20, 50, 200))
    d["rsi"] = rsi(c, cfg.rsi_len)
    d["macd"], d["macd_sig"], d["hist"] = macd(c, cfg.macd_fast, cfg.macd_slow, cfg.macd_sig)
    d["atr"] = atr(d, cfg.atr_len)
    d["poc"], d["val"], d["vah"] = volume_profile_levels(d, cfg)
    trend = htf_trend(d, cfg, htf_df)
    bull_div, bear_div = divergence(d, d["rsi"], cfg.pivot_len)
    pa_bull, pa_bear, sw_bull, sw_bear = price_action(d, cfg)

    # 1) TREND (HTF + stack EMA di TF entry)
    stack_long = (d["ema9"] > d["ema20"]) & (c > d["ema50"])
    stack_short = (d["ema9"] < d["ema20"]) & (c < d["ema50"])
    g_trend_l = (trend == 1) & stack_long
    g_trend_s = (trend == -1) & stack_short

    # 2) MOMENTUM: RSI + MACD (event berlaku confirm_window bar)
    w = cfg.confirm_window
    rsi_rec_l = _recent((d["rsi"].shift() < cfg.rsi_pull_long) & (d["rsi"] >= cfg.rsi_pull_long), w) | _recent(bull_div, w)
    rsi_rec_s = _recent((d["rsi"].shift() > cfg.rsi_pull_short) & (d["rsi"] <= cfg.rsi_pull_short), w) | _recent(bear_div, w)
    macd_l = _recent((d["macd"] > d["macd_sig"]) & (d["macd"].shift() <= d["macd_sig"].shift()), w) | ((d["hist"] > 0) & (d["hist"] > d["hist"].shift()))
    macd_s = _recent((d["macd"] < d["macd_sig"]) & (d["macd"].shift() >= d["macd_sig"].shift()), w) | ((d["hist"] < 0) & (d["hist"] < d["hist"].shift()))
    g_mom_l, g_mom_s = rsi_rec_l & macd_l, rsi_rec_s & macd_s

    # 3) VOLUME PROFILE: long dekat support (POC/VAL) & di atas VAL; short sebaliknya
    prox = cfg.vp_prox_atr * d["atr"]
    near_sup = ((c - d["poc"]).abs() <= prox) | ((c - d["val"]).abs() <= prox)
    near_res = ((c - d["poc"]).abs() <= prox) | ((c - d["vah"]).abs() <= prox)
    g_vp_l = near_sup & (c >= d["val"] - prox)
    g_vp_s = near_res & (c <= d["vah"] + prox)

    # 4) PRICE ACTION trigger (bar ini)
    g_pa_l, g_pa_s = pa_bull, pa_bear

    # 5) VOLATILITY filter (ranking ATR dalam jendela historis)
    atr_rank = d["atr"].rolling(cfg.atr_rank_window, min_periods=100).rank(pct=True)
    g_vol = (atr_rank >= cfg.atr_pct_low) & (atr_rank <= cfg.atr_pct_high)

    # gabungan: TREND wajib (kecuali gate_trend=False utk PING PONG sideways);
    # MOM/VP/PA/VOL cukup min_confluence dari 4 (default 4 = AND ketat)
    n_l = (g_mom_l.astype(int) + g_vp_l.astype(int) + g_pa_l.astype(int) + g_vol.astype(int))
    n_s = (g_mom_s.astype(int) + g_vp_s.astype(int) + g_pa_s.astype(int) + g_vol.astype(int))
    if cfg.gate_trend:
        long_ok = g_trend_l & (n_l >= cfg.min_confluence)
        short_ok = g_trend_s & (n_s >= cfg.min_confluence)
    else:
        long_ok = n_l >= cfg.min_confluence
        short_ok = n_s >= cfg.min_confluence

    # filter desain (opsional): arah, jendela sesi UTC, regime ADX
    if not cfg.allow_long:
        long_ok &= False
    if not cfg.allow_short:
        short_ok &= False
    if cfg.session_utc:
        h0, h1 = cfg.session_utc
        in_sess = (d.index.hour >= h0) & (d.index.hour < h1)
        long_ok &= in_sess
        short_ok &= in_sess
    if cfg.adx_min > 0:
        adx = adx_wilder(d, 14)
        long_ok &= adx >= cfg.adx_min
        short_ok &= adx >= cfg.adx_min
    if cfg.fib_gate:
        # premium/discount terhadap dealing range: LONG hanya di discount
        # (<= EQ 0.5), SHORT hanya di premium (>= EQ) — ICT/GODMODE style
        rng_hi = d["high"].rolling(cfg.fib_lookback).max()
        rng_lo = d["low"].rolling(cfg.fib_lookback).min()
        rng = (rng_hi - rng_lo).replace(0, np.nan)
        eq = rng_lo + 0.5 * rng
        long_ok &= c <= eq
        short_ok &= c >= eq
    if cfg.round_gate:
        # harga psikologis: level bulat besar = kolam likuiditas / magnet.
        # Step otomatis dari skala harga (emas ~3500 -> major tiap 100).
        ref = float(np.nanmedian(c))
        major = 100.0 if ref >= 500 else (10.0 if ref >= 50 else 1.0)
        rprox = cfg.round_prox_atr * d["atr"]
        if cfg.round_entry_gate:
            lvl_above = np.ceil(c / major) * major
            lvl_below = np.floor(c / major) * major
            # jangan entry LONG tepat di bawah resisten bulat / SHORT tepat
            # di atas support bulat (harga sering mantul di situ)
            long_ok &= ~((lvl_above > c) & (lvl_above - c <= rprox))
            short_ok &= ~((c > lvl_below) & (c - lvl_below <= rprox))

    # skor kualitas (bonus, bukan gate)
    score_l = (sw_bull.astype(int) + _recent(bull_div, w).astype(int)
               + (c > d["poc"]).astype(int) + (d["rsi"] > 50).astype(int))
    score_s = (sw_bear.astype(int) + _recent(bear_div, w).astype(int)
               + (c < d["poc"]).astype(int) + (d["rsi"] < 50).astype(int))

    # SL/TP dinamis ATR: stop di luar swing + buffer. Entry yang jarak stop
    # naturalnya < sl_atr_min x ATR DITOLAK (stop di dalam noise = mudah
    # disapu sweep), bukan dilebarkan paksa sampai keluar dari struktur.
    sw_low = d["low"].rolling(5).min()
    sw_high = d["high"].rolling(5).max()
    buf = cfg.sl_buffer_atr * d["atr"]
    min_dist = cfg.sl_atr_min * d["atr"]
    raw_l = c - (sw_low - buf)
    raw_s = (sw_high + buf) - c
    long_ok &= raw_l >= min_dist
    short_ok &= raw_s >= min_dist
    dist_l = raw_l.clip(min_dist, cfg.sl_atr_max * d["atr"])
    dist_s = raw_s.clip(min_dist, cfg.sl_atr_max * d["atr"])

    sig = pd.Series(0, d.index)
    sig[long_ok] = 1
    sig[short_ok & ~long_ok] = -1

    # cooldown
    last, arr = -10**9, sig.to_numpy().copy()
    for i in range(len(arr)):
        if arr[i] != 0:
            if i - last < cfg.cooldown:
                arr[i] = 0
            else:
                last = i
    d["signal"] = arr
    s = d["signal"]
    dist = np.where(s == 1, dist_l, np.where(s == -1, dist_s, np.nan))
    d["entry"] = np.where(s != 0, c, np.nan)
    d["sl"] = np.where(s == 1, c - dist, np.where(s == -1, c + dist, np.nan))
    d["tp1"] = np.where(s == 1, c + cfg.tp1_rr * dist, np.where(s == -1, c - cfg.tp1_rr * dist, np.nan))
    d["tp2"] = np.where(s == 1, c + cfg.tp2_rr * dist, np.where(s == -1, c - cfg.tp2_rr * dist, np.nan))

    if cfg.round_gate:
        # sesuaikan SL/TP terhadap level bulat besar:
        #  - SL jangan duduk sedikit DI ATAS/DI BAWAH level bulat (sweep magnet)
        #  - TP jangan diletakkan sedikit DI BALIK level bulat (harga sering
        #    mentok lalu mantul) — geser sedikit ke sisi aman.
        ref = float(np.nanmedian(c))
        major = 100.0 if ref >= 500 else (10.0 if ref >= 50 else 1.0)
        rprox = cfg.round_prox_atr * d["atr"].to_numpy()
        rbuf = cfg.sl_buffer_atr * d["atr"].to_numpy()
        sl_a, tp2_a = d["sl"].to_numpy(copy=True), d["tp2"].to_numpy(copy=True)
        is_l, is_s = (s == 1), (s == -1)

        # LONG: level bulat dalam +-rprox dari SL (di ATAU di bawahnya) ->
        # SL dipindah ke sisi aman level: L - buffer (min: tak pernah mengetatkan)
        m = is_l & ~np.isnan(sl_a)
        lvl_lo = np.floor(sl_a / major) * major
        lvl_hi = lvl_lo + major
        near_lo = (sl_a - lvl_lo) <= rprox
        near_hi = (lvl_hi - sl_a) <= rprox
        L = np.where(near_lo, lvl_lo, lvl_hi)
        unsafe = m & (near_lo | near_hi) & (L < d["entry"].to_numpy())
        sl_a[unsafe] = np.minimum(sl_a[unsafe], L[unsafe] - rbuf[unsafe])
        # TP2 long: level bulat di antara entry dan tp2, tp2 sedikit di baliknya
        m = is_l & ~np.isnan(tp2_a)
        rl = np.floor(tp2_a / major) * major
        snap = m & (rl > d["entry"].to_numpy()) & (tp2_a - rl <= rprox) & (rl - rbuf > d["entry"].to_numpy())
        tp2_a[snap] = rl[snap] - rbuf[snap]

        # SHORT: level bulat dalam +-rprox dari SL (di ATAU di atasnya) ->
        # SL dipindah ke sisi aman level: L + buffer (max: tak pernah mengetatkan)
        m = is_s & ~np.isnan(sl_a)
        lvl_hi2 = np.ceil(sl_a / major) * major
        lvl_lo2 = lvl_hi2 - major
        near_hi = (lvl_hi2 - sl_a) <= rprox
        near_lo = (sl_a - lvl_lo2) <= rprox
        L = np.where(near_hi, lvl_hi2, lvl_lo2)
        unsafe = m & (near_hi | near_lo) & (L > d["entry"].to_numpy())
        sl_a[unsafe] = np.maximum(sl_a[unsafe], L[unsafe] + rbuf[unsafe])
        # TP2 short
        m = is_s & ~np.isnan(tp2_a)
        rl = np.ceil(tp2_a / major) * major
        snap = m & (rl < d["entry"].to_numpy()) & (rl - tp2_a <= rprox) & (rl + rbuf < d["entry"].to_numpy())
        tp2_a[snap] = rl[snap] + rbuf[snap]

        d["sl"], d["tp2"] = sl_a, tp2_a

    d["score"] = np.where(s == 1, score_l, np.where(s == -1, score_s, 0))
    d["reason"] = np.where(s == 1, "TREND+MOM+VP+PA+VOL long",
                           np.where(s == -1, "TREND+MOM+VP+PA+VOL short", ""))
    return d[d["signal"] != 0][["signal", "entry", "sl", "tp1", "tp2", "score",
                                "reason", "atr"]]


def position_size(equity, entry, sl, risk_pct=1.0, contract_size=1.0):
    """Lot/unit = (equity * risk%) / (jarak SL * contract size)."""
    risk_amt = equity * risk_pct / 100
    per_unit = abs(entry - sl) * contract_size
    return risk_amt / per_unit if per_unit > 0 else 0.0


# ----------------------------- Demo -----------------------------------------
if __name__ == "__main__":
    rng = np.random.default_rng(7)
    n = 12000
    idx = pd.date_range("2025-01-01", periods=n, freq="15min")
    price = 2000 + np.cumsum(rng.normal(0.02, 1.2, n))
    o = price + rng.normal(0, 0.3, n)
    cl = price + rng.normal(0, 0.3, n)
    hi = np.maximum(o, cl) + np.abs(rng.normal(0, 0.8, n))
    lo = np.minimum(o, cl) - np.abs(rng.normal(0, 0.8, n))
    vol = rng.integers(100, 1000, n).astype(float)
    data = pd.DataFrame({"open": o, "high": hi, "low": lo, "close": cl, "volume": vol}, idx)
    out = generate_signals(data)
    print(out.tail(10).round(2))
    print("Total sinyal:", len(out))
