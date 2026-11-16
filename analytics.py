"""
Analytics dashboard Alpha-Trade (GODMODE-style panels)
------------------------------------------------------
Semua panel dihitung dari OHLCV yang sudah close (tanpa lookahead):
  - header stats   : price, RSI, ATR, structure state, AI score, fibonacci
  - volume flow    : buy/sell vol, net CVD, buyers %, absorption
  - zones          : supply & demand dari swing terkonfirmasi + CVD strength
  - events         : CHoCH / HH / LL / SWEEP / TC dengan deskripsi
  - heatmap        : Trend / Zone / Momentum / CVD Flow / Breakout per timeframe
  - confluence     : checklist + confidence untuk panel AI CONFLUENCE
  - setup          : entry / SL / TP1 / TP2 / RR dari sinyal confluence_engine
"""
import numpy as np
import pandas as pd

from confluence_engine import Config, atr as atr_fn, ema, generate_signals, rsi
from smc_structure import structure_events


# ----------------------------- Volume flow ----------------------------------
def cvd(df):
    """Cumulative Volume Delta: volume bull dikurangi volume bear."""
    delta = np.sign(df["close"] - df["open"]) * df["volume"]
    return delta.cumsum()


def volume_flow(df, n=20):
    d = df.tail(n)
    delta = np.sign(d["close"] - d["open"]) * d["volume"]
    buy = float(delta[delta > 0].sum())
    sell = float(-delta[delta < 0].sum())
    tot = buy + sell
    net = buy - sell
    bp = round(buy / tot * 100) if tot else 50
    # absorption: sisi yang lebih kecil "ditelan" (harga tetap bertahan)
    small = "SELL" if sell < buy else "BUY"
    absorption = f"ABSORPTION ({small})" if tot and (min(buy, sell) / max(buy, sell) > 0.4) else "FLOW NORMAL"
    return {"buy": round(buy), "sell": round(sell), "net_cvd": round(net),
            "buyers_pct": bp, "sellers_pct": 100 - bp, "absorption": absorption}


# ----------------------------- Zona S/D -------------------------------------
def _last_swing(df, kind="low", L=5, lookback=200):
    """Swing terakhir yang sudah terkonfirmasi (pivot L bar, tanpa lookahead)."""
    w = df.tail(lookback)
    arr = (w["low"] if kind == "low" else w["high"]).to_numpy()
    for p in range(len(arr) - L - 1, L - 1, -1):
        if kind == "low" and arr[p] == arr[p - L:p + L + 1].min():
            i = w.index[p]
            row = df.loc[i]
            return {"idx": i, "price": float(arr[p]),
                    "top": float(max(row["open"], row["close"])),
                    "bottom": float(row["low"])}
        if kind == "high" and arr[p] == arr[p - L:p + L + 1].max():
            i = w.index[p]
            row = df.loc[i]
            return {"idx": i, "price": float(arr[p]),
                    "top": float(row["high"]),
                    "bottom": float(min(row["open"], row["close"]))}
    return None


def zones(df, a, L=5, lookback=200):
    """Zona supply & demand dari swing terakhir + kekuatan CVD saat formasi."""
    c = df["close"].iloc[-1]
    out = {}
    for kind, name in (("high", "supply"), ("low", "demand")):
        z = _last_swing(df, kind, L, lookback)
        if not z:
            continue
        p = df.index.get_loc(z["idx"])
        lo = max(0, p - L)
        hi = min(len(df), p + L + 1)
        strength = int(df["high"].iloc[lo:hi].eq(df["high"].iloc[lo:hi].max()).sum()) if kind == "high" \
            else int(df["low"].iloc[lo:hi].eq(df["low"].iloc[lo:hi].min()).sum())
        cvd_at = float(cvd(df.iloc[lo:hi]).iloc[-1] - cvd(df.iloc[max(0, lo - 1):lo]).iloc[-1]) if lo else 0.0
        if name == "supply":
            z["bottom"] = max(z["bottom"], z["top"] - 1.2 * a)   # lebar zona maks 1.2 ATR
            inside = z["bottom"] <= c <= z["top"]
            near = 0 <= (c - z["bottom"]) <= 0.5 * a
            status = "PRICE IN ZONE" if inside else ("AT SUPPLY" if near else "ENTRY SELL")
        else:
            z["top"] = min(z["top"], z["bottom"] + 1.2 * a)
            inside = z["bottom"] <= c <= z["top"]
            near = 0 <= (z["top"] - c) <= 0.5 * a
            status = "PRICE IN ZONE" if inside else ("AT DEMAND" if near else "ENTRY BUY")
        out[name] = {"top": round(z["top"], 2), "bottom": round(z["bottom"], 2),
                     "strength": f"{strength} candles",
                     "cvd": int(round(cvd_at)), "status": status}
    return out


# ----------------------------- Events struktur ------------------------------
def events_list(df, L=5, sweep_lookback=20, max_events=8):
    """Gabungan CHoCH/BOS (smc_structure), HH/LL (pivot), dan liquidity sweep."""
    ev = structure_events(df, L)
    idx = df.index
    rows = []
    trend_dir = {1: "UP", -1: "DOWN"}
    choch_rows = []
    for t in idx[ev["choch_bull"]]:
        choch_rows.append((t, float(df.loc[t, "close"]), "CHoCH",
                           "Bearish→Bullish flip — LH broken upward", "UP"))
    for t in idx[ev["choch_bear"]]:
        choch_rows.append((t, float(df.loc[t, "close"]), "CHoCH",
                           "Bullish→Bearish flip — HL broken downward", "DOWN"))
    rows.extend(choch_rows[-4:])

    # swing HH / LL + TC (flip struktur besar)
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    last_hi = last_lo = None
    swings = []
    for p in range(L, len(df) - L):
        if h[p] == h[p - L:p + L + 1].max():
            if last_hi is not None and h[p] > h[last_hi]:
                swings.append((idx[p + L], float(h[p]), "HH",
                               "Higher High — Last bullish swing high", "UP"))
            last_hi = p
        if l[p] == l[p - L:p + L + 1].min():
            if last_lo is not None and l[p] < l[last_lo]:
                swings.append((idx[p + L], float(l[p]), "LL",
                               "Lower Low — Last bearish swing low", "DOWN"))
            last_lo = p
    rows.extend(swings[-2:])

    # liquidity sweep
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

    rows.sort(key=lambda r: r[0])
    return [{"time": str(t), "tag": tag, "price": round(price, 2),
             "desc": desc, "dir": d} for t, price, tag, desc, d in rows[-max_events:]]


def structure_state(df, L=5):
    """EXPANDING / CONTRACTING dari rentang swing terakhir."""
    h, l = df["high"].to_numpy(), df["low"].to_numpy()
    his, los = [], []
    for p in range(L, len(df) - L):
        if h[p] == h[p - L:p + L + 1].max():
            his.append(h[p])
        if l[p] == l[p - L:p + L + 1].min():
            los.append(l[p])
    if len(his) >= 2 and len(los) >= 2:
        r_now = his[-1] - los[-1]
        r_prev = his[-2] - los[-2]
        state = "EXPANDING" if r_now > r_prev else "CONTRACTING"
        sub = "HH + LL — VOLATILE" if state == "EXPANDING" else "RANGE MENGECEL — QUIET"
        return state, sub, float(his[-1]), float(los[-1])
    return "WARMING UP", "DATA KURANG", None, None


# ----------------------------- Fibonacci ------------------------------------
def fib_state(df, lookback=120):
    """Premium/discount dari rentang terakhir; EQ = 0.5, GP = 0.618, OTE = 0.786."""
    w = df.tail(lookback)
    hi, lo = float(w["high"].max()), float(w["low"].min())
    rng = hi - lo
    if rng <= 0:
        return {}
    c = float(df["close"].iloc[-1])
    pos = (c - lo) / rng
    return {"high": hi, "low": lo, "eq": lo + 0.5 * rng, "gp": hi - 0.618 * rng,
            "ote": hi - 0.786 * rng,
            "state": "DISCOUNT" if pos < 0.5 else "PREMIUM",
            "equilibrium": round(lo + 0.5 * rng, 2)}


# ----------------------------- Heatmap MTF ----------------------------------
def heatmap_row(df):
    c = df["close"]
    e9, e20, e50 = ema(c, 9), ema(c, 20), ema(c, 50)
    r = rsi(c, 14)
    cv_s = cvd(df)
    ev = structure_events(df, 5)

    stack_bull = bool(e9.iloc[-1] > e20.iloc[-1] > e50.iloc[-1])
    stack_bear = bool(e9.iloc[-1] < e20.iloc[-1] < e50.iloc[-1])
    state, _, shi, slo = structure_state(df, 5)
    trend = "BULL" if stack_bull else "BEAR" if stack_bear else ("EXP" if state == "EXPANDING" else "RANG")

    z = zones(df, float(atr_fn(df).iloc[-1]))
    zone = "SUPPLY" if "supply" in z and z["supply"]["status"] in ("PRICE IN ZONE", "AT SUPPLY") \
        else "DEMAND" if "demand" in z and z["demand"]["status"] in ("PRICE IN ZONE", "AT DEMAND") else "NEUT"

    mom = "BULL" if r.iloc[-1] > 55 else "BEAR" if r.iloc[-1] < 45 else "NEUT"

    slope = float(cv_s.iloc[-1] - cv_s.iloc[-10]) if len(cv_s) > 10 else 0.0
    flow = "BUY" if slope > 0 else "SELL" if slope < 0 else "MIXED"

    bull_bo = bool((ev["bos_bull"] | ev["choch_bull"]).tail(10).any())
    bear_bo = bool((ev["bos_bear"] | ev["choch_bear"]).tail(10).any())
    brk = "CH-BULL" if bull_bo else "CH-BEAR" if bear_bo else "WAIT"

    return {"Trend": trend, "Zone": zone, "Momentum": mom,
            "CVD Flow": flow, "Breakout": brk}


# ----------------------------- Confluence AI --------------------------------
def confluence(df, df_htf, cfg):
    """Checklist ala panel AI CONFLUENCE: tiap cek bernilai 25."""
    from confluence_engine import htf_trend
    trend = htf_trend(df, cfg, df_htf)
    htf_dir = int(trend.iloc[-1])

    a = float(atr_fn(df).iloc[-1])
    z = zones(df, a)
    c = float(df["close"].iloc[-1])
    at_supply = "supply" in z and (z["supply"]["status"] in ("PRICE IN ZONE", "AT SUPPLY"))
    at_demand = "demand" in z and (z["demand"]["status"] in ("PRICE IN ZONE", "AT DEMAND"))

    w = df.tail(cfg.sweep_lookback + 2)
    prev_lo = df["low"].shift().rolling(cfg.sweep_lookback).min().iloc[-1]
    prev_hi = df["high"].shift().rolling(cfg.sweep_lookback).max().iloc[-1]
    sweep_bull = bool((w["low"] < prev_lo).any())
    sweep_bear = bool((w["high"] > prev_hi).any())

    o, cl = df["open"].iloc[-1], df["close"].iloc[-1]
    po, pcl = df["open"].iloc[-2], df["close"].iloc[-2]
    two_bull = bool(cl > o and pcl < po and cl >= po)
    two_bear = bool(cl < o and pcl > po and cl <= po)

    r = rsi(df["close"], cfg.rsi_len).iloc[-1]
    checks = [
        {"label": f"HTF TREND ({cfg.htf.upper()})",
         "val": "BULLISH" if htf_dir == 1 else "BEARISH" if htf_dir == -1 else "RANGING",
         "ok": htf_dir != 0, "pts": 25 if htf_dir != 0 else 0},
        {"label": "ZONE", "val": "AT SUPPLY" if at_supply else "AT DEMAND" if at_demand else "NEUTRAL",
         "ok": at_supply or at_demand, "pts": 25 if (at_supply or at_demand) else 0},
        {"label": "SWEEP", "val": "BEARISH SWEEP" if sweep_bear else "BULLISH SWEEP" if sweep_bull else "WAIT",
         "ok": sweep_bull or sweep_bear, "pts": 25 if (sweep_bull or sweep_bear) else 0},
        {"label": "2-CANDLE", "val": "CONFIRMED" if (two_bull or two_bear) else "WAIT",
         "ok": two_bull or two_bear, "pts": 25 if (two_bull or two_bear) else 0},
    ]
    score = sum(ch["pts"] for ch in checks)
    if (htf_dir == 1 and at_demand and r > 50) or (htf_dir == -1 and at_supply and r < 50):
        score = min(100, score + 20)                     # bonus searah penuh
    action = "EXECUTE" if score >= 75 else "MONITOR" if score >= 50 else "WAIT"
    return {"confidence": score, "action": action, "checks": checks}


# ----------------------------- Snapshot -------------------------------------
def header_stats(df, df_htf, cfg):
    c = df["close"]
    r = float(rsi(c, cfg.rsi_len).iloc[-1])
    a_series = atr_fn(df, cfg.atr_len)
    a = float(a_series.iloc[-1])
    state, sub, _, _ = structure_state(df, 5)
    f = fib_state(df)
    # AI score pakai confluence checklist
    conf = confluence(df, df_htf, cfg)
    return {"price": round(float(c.iloc[-1]), 2), "rsi": round(r, 1),
            "rsi_label": "Neutral" if 45 <= r <= 55 else ("Bullish" if r > 55 else "Bearish"),
            "atr": round(a, 2),
            "atr_label": "Volatilitas TF entry",
            "structure": state, "structure_sub": sub,
            "ai_score": conf["confidence"], "ai_label": "HIGH" if conf["confidence"] >= 75 else ("MED" if conf["confidence"] >= 50 else "LOW"),
            "fib": f.get("state", "-"), "equilibrium": f.get("equilibrium")}


def build_snapshot(buffers, cfg=Config(), signals_log=None, symbol="XAUUSD"):
    """Snapshot penuh dashboard. buffers = {tf: DataFrame} (semua bar close)."""
    tfs = sorted(buffers, key=lambda t: {"1m": 1, "5m": 5, "15m": 15, "30m": 30,
                                         "1h": 60, "4h": 240, "1d": 1440}.get(t, 0))
    tf_entry = tfs[0] if tfs else None
    tf_htf = tfs[-1] if len(tfs) > 1 else None
    df = buffers.get(tf_entry)
    df_htf = buffers.get(tf_htf)
    if df is None or len(df) < 260:
        return None

    a = float(atr_fn(df, cfg.atr_len).iloc[-1])
    f = fib_state(df)
    z = zones(df, a)
    sig = generate_signals(df, cfg, htf_df=df_htf)

    setup = None
    if len(sig):
        row = sig.iloc[-1]
        d = int(row["signal"])
        rr = (abs(row["tp2"] - row["entry"]) / abs(row["entry"] - row["sl"]))
        setup = {"side": "SELL" if d == -1 else "BUY", "time": str(sig.index[-1]),
                 "entry": round(float(row["entry"]), 2), "sl": round(float(row["sl"]), 2),
                 "tp1": round(float(row["tp1"]), 2), "tp2": round(float(row["tp2"]), 2),
                 "rr": f"1:{rr:.0f}", "score": int(row["score"]),
                 "reason": str(row["reason"]),
                 "fresh": sig.index[-1] == df.index[-1]}

    golden = {"price": round(float(df["close"].iloc[-1]), 2),
              "state": "EXECUTE (GOLDEN)" if setup and setup["fresh"] and setup["score"] >= 3
              else ("STANDBY" if not (setup and setup["fresh"]) else "EXECUTE"),
              "sub": "GET READY (GOLDEN)"}
    high_risk = {"price": round(float(df["close"].iloc[-1]), 2),
                 "state": "EXECUTE (HIGH RISK)" if setup and setup["fresh"] and setup["score"] < 3 else "WAIT",
                 "sub": "HIGH RISK " + ("ON" if setup and setup["fresh"] and setup["score"] < 3 else "OFF")}

    return {
        "symbol": symbol, "ts": pd.Timestamp.now(tz="UTC").isoformat(),
        "tf_entry": tf_entry, "tf_htf": tf_htf,
        "stats": header_stats(df, df_htf, cfg),
        "volume_flow": volume_flow(df, 20),
        "zones": z,
        "events": events_list(df, 5, cfg.sweep_lookback),
        "heatmap": {"tfs": [t.upper() for t in tfs],
                    "rows": {k: [heatmap_row(buffers[t])[k] for t in tfs]
                             for k in ("Trend", "Zone", "Momentum", "CVD Flow", "Breakout")}},
        "setup": setup, "golden": golden, "high_risk": high_risk,
        "confluence": confluence(df, df_htf, cfg),
        "overlays": {"eq": f.get("eq"), "gp": f.get("gp"), "ote": f.get("ote"),
                     "range_high": f.get("high"), "range_low": f.get("low"),
                     "supply": [z["supply"]["top"], z["supply"]["bottom"]] if "supply" in z else None,
                     "demand": [z["demand"]["top"], z["demand"]["bottom"]] if "demand" in z else None},
        "signals_log": signals_log or [],
    }
