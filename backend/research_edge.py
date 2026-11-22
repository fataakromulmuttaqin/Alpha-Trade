"""Riset peningkatan edge — semua diuji dengan protokol yang sama:
pilih di TRAIN (2023-2025), verifikasi SEKALI di OOS (2026), dua aset.

Menguji: portofolio PAXG+BTC, vol-targeted sizing, varian exit,
analisis rezim, ML meta-filter (purged CV).
Pemakaian: python -m backend.research_edge
"""
import json
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.tradoor_feed import load_market                    # noqa: E402
from backend.confluence_engine import (Config, generate_signals,  # noqa: E402
    adx_wilder, volume_profile_levels, rsi as rsi_fn, atr as atr_fn,
    ema as ema_fn)
from backend.backtest import backtest                           # noqa: E402

COST = 0.3
TRAIN_END = "2026-01-01"
CHAMP = {  # EDGE-1H champion (ronde 2 + adx20)
    "htf": "4h", "min_confluence": 1, "confirm_window": 2, "cooldown": 8,
    "session_utc": (7, 21), "adx_min": 20.0,
    "sl_atr_min": 1.96, "sl_atr_max": 3.10, "tp1_rr": 2.34, "tp2_rr": 3.68,
    "rsi_pull_long": 46.0, "rsi_pull_short": 69.8, "vp_prox_atr": 0.44,
}

ASSETS = {"PAXG": "binance:PAXGUSDT", "BTC": "binance:BTCUSDT"}


def to_tf(df1m, rule):
    return df1m.resample(rule).agg(
        {"open": "first", "high": "max", "low": "min",
         "close": "last", "volume": "sum"}).dropna()


def oos_slice(train, oos):
    """Sinyal dihitung dgn ekor train 90 hari sbg warmup; trade OOS saja."""
    return train, oos


def sig_with_warmup(df, cfg, oos_start, warmup_bars=90 * 24):
    tail = df[df.index < oos_start].tail(warmup_bars)
    part = pd.concat([tail, df[df.index >= oos_start]])
    sig = generate_signals(part, cfg)
    return sig[sig.index >= oos_start], part[part.index >= oos_start]


def mline(m):
    if not m.get("trades"):
        return "n=0"
    return (f"n={m['trades']:3d} WR={m.get('win_rate_%','-')}% PF={m.get('profit_factor','-')} "
            f"ret={m.get('return_%','-')}% dd={m.get('max_drawdown_%','-')}%")


def load_all():
    data = {}
    for a, mkt in ASSETS.items():
        df1m = load_market(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "tradoor-data"), mkt, "1m", bars=10**9)
        df = to_tf(df1m, "60min")
        data[a] = (df[df.index < TRAIN_END], df[df.index >= TRAIN_END])
    return data


# ----------------------------- 1) portofolio --------------------------------
def portfolio(data):
    print("\n=== 1) PORTOFOLIO PAXG + BTC (masing2 akun 10k, risiko 1%) ===")
    curves, eq_final = {}, {}
    for a, (tr, oo) in data.items():
        cfg = Config(**CHAMP)
        # OOS dengan warmup (protokol tune_edge)
        tail = tr.tail(90 * 24)
        part = pd.concat([tail, oo])
        sig = generate_signals(part, cfg)
        sig = sig[sig.index >= TRAIN_END]
        sub = part[part.index >= TRAIN_END]
        trd, m = backtest(sub, sig, cost=COST, daily_stop_r=1.0)
        curves[a] = trd
        eq_final[a] = m
        print(f"  {a:5s} OOS: {mline(m)}")
    # gabung: dua akun independen — timeline union, ffill, lalu dijumlah
    eq_p = curves["PAXG"][["time", "equity"]].set_index("time")["equity"]
    eq_b = curves["BTC"][["time", "equity"]].set_index("time")["equity"]
    union = eq_p.index.union(eq_b.index).sort_values()
    tot = (eq_p.reindex(union).ffill().fillna(10_000)
           + eq_b.reindex(union).ffill().fillna(10_000))
    dd = ((tot / tot.cummax()) - 1).min() * 100
    ret = (tot.iloc[-1] / 20_000 - 1) * 100
    n = sum(m["trades"] for m in eq_final.values())
    wins = sum((t["R"] > 0).sum() for t in curves.values())
    wr = wins / max(n, 1) * 100
    gp = sum(t["R"][t["R"] > 0].sum() for t in curves.values())
    gl = -sum(t["R"][t["R"] < 0].sum() for t in curves.values())
    pf = gp / gl if gl > 0 else float("inf")
    print(f"  GABUNGAN OOS: n={n} WR={wr:.1f}% PF={pf:.2f} "
          f"ret={ret:.1f}% (vs 20k) maxDD={dd:.1f}%")


# ----------------------------- 2) vol sizing --------------------------------
def vol_sizing(data):
    print("\n=== 2) VOL-TARGETED SIZING (skala risiko ~1/ATR%) ===")
    for a, (tr, oo) in data.items():
        cfg = Config(**CHAMP)
        tail = tr.tail(90 * 24)
        part = pd.concat([tail, oo])
        sig = generate_signals(part, cfg)
        sig_oos = sig[sig.index >= TRAIN_END]
        sub = part[part.index >= TRAIN_END]
        atrp = (sig_oos["atr"] / sig_oos["entry"]) * 100.0
        med = float(atrp.median())
        scale = (med / atrp).clip(0.5, 2.0)
        _, m0 = backtest(sub, sig_oos, cost=COST, daily_stop_r=1.0)
        _, m1 = backtest(sub, sig_oos, cost=COST, daily_stop_r=1.0, risk_scale=scale)
        print(f"  {a}: baseline ret={m0.get('return_%','-')}% dd={m0.get('max_drawdown_%','-')}%"
              f"  |  vol-targeted ret={m1.get('return_%','-')}% dd={m1.get('max_drawdown_%','-')}%")


# ----------------------------- 3) exit variants ------------------------------
def exit_variants(data):
    print("\n=== 3) STRUKTUR EXIT (pilih di TRAIN, verifikasi OOS) ===")
    variants = [
        ("base 2.34/3.68 f50", {"tp1_rr": 2.34, "tp2_rr": 3.68}, 0.5),
        ("1.5/4.5 f30", {"tp1_rr": 1.5, "tp2_rr": 4.5}, 0.3),
        ("2.0/4.5 f40", {"tp1_rr": 2.0, "tp2_rr": 4.5}, 0.4),
        ("3.0/5.0 f50", {"tp1_rr": 3.0, "tp2_rr": 5.0}, 0.5),
    ]
    scores = []
    tr_cache = {}
    for a, (tr, oo) in data.items():
        for name, mod, frac in variants:
            cfg = Config(**{**CHAMP, **mod})
            sig = generate_signals(tr, cfg)
            _, m = backtest(tr, sig, cost=COST, tp1_frac=frac, daily_stop_r=1.0)
            tr_cache.setdefault(name, []).append((a, m))
    for name, mod, frac in variants:
        pfs = [m.get("profit_factor", 0) for _, m in tr_cache[name]]
        rets = [m.get("return_%", 0) for _, m in tr_cache[name]]
        avg = float(np.mean([p if p != float("inf") else 5 for p in pfs]))
        scores.append((avg, name, mod, frac))
        detail = " | ".join(f"{a}: PF={m.get('profit_factor','-')} ret={m.get('return_%','-')}%"
                            for a, m in tr_cache[name])
        print(f"  {name:20s} avgPF={avg:.2f}  {detail}")
    best = max(scores)
    print(f"  -> TERPILIH (TRAIN): {best[1]}")
    for a, (tr, oo) in data.items():
        cfg = Config(**{**CHAMP, **best[2]})
        tail = tr.tail(90 * 24)
        part = pd.concat([tail, oo])
        sig = generate_signals(part, cfg)
        sig = sig[sig.index >= TRAIN_END]
        sub = part[part.index >= TRAIN_END]
        _, m = backtest(sub, sig, cost=COST, tp1_frac=best[3], daily_stop_r=1.0)
        print(f"  OOS {a}: {mline(m)}")


# ----------------------------- 4) rezim -------------------------------------
def regime(data):
    print("\n=== 4) REZIM: kapan edge hidup/mati (TRAIN & OOS) ===")
    for a, (tr, oo) in data.items():
        cfg = Config(**CHAMP)
        for label, part, warm in (("TRAIN", tr, False), ("OOS", None, True)):
            if warm:
                tail = tr.tail(90 * 24)
                part = pd.concat([tail, oo])
                sig_all = generate_signals(part, cfg)
                sig_all = sig_all[sig_all.index >= TRAIN_END]
                part = part[part.index >= TRAIN_END]
            else:
                sig_all = generate_signals(part, cfg)
            if sig_all.empty:
                continue
            trd, _ = backtest(part, sig_all, cost=COST, daily_stop_r=1.0)
            if trd.empty:
                continue
            adx = adx_wilder(part, 14)
            adx_s = pd.Series(adx, index=part.index)
            trd = trd.copy()
            trd["adx"] = adx_s.reindex(trd["time"]).to_numpy()
            buckets = [(0, 20, "ADX<20"), (20, 30, "ADX20-30"), (30, 999, "ADX>30")]
            line = []
            for lo, hi, nm in buckets:
                sel = trd[(trd["adx"] >= lo) & (trd["adx"] < hi)]
                if len(sel):
                    gp = sel["R"][sel["R"] > 0].sum()
                    gl = -sel["R"][sel["R"] < 0].sum()
                    pf = gp / gl if gl > 0 else float("inf")
                    line.append(f"{nm}: n={len(sel)} PF={pf:.2f}")
            print(f"  {a} {label}: " + " | ".join(line))


# ----------------------------- 5) ML meta filter ----------------------------
def build_features(df, sig, cfg):
    feats = []
    adx = adx_wilder(df, 14)
    adx_s = pd.Series(adx, index=df.index)
    rank = atr_fn(df, cfg.atr_len).rolling(500, min_periods=100).rank(pct=True)
    poc, val, vah = volume_profile_levels(df, cfg)
    poc_s = pd.Series(poc, index=df.index)
    r_s = rsi_fn(df["close"], 14)
    ht = htf_trend_series(df, cfg)
    for t, row in sig.iterrows():
        i = df.index.get_loc(t)
        d_poc = abs(row["entry"] - poc_s.iloc[i]) / max(row["atr"], 1e-9)
        tv = int(ht.iloc[i])
        feats.append({
            "rsi": float(r_s.iloc[i]),
            "adx": float(adx_s.iloc[i]),
            "atr_rank": 0.5 if np.isnan(rank.iloc[i]) else float(rank.iloc[i]),
            "hour": t.hour, "dow": t.dayofweek,
            "dist_poc_atr": d_poc,
            "trend": tv,
            "dir": int(row["signal"]),
            "atr_pct": float(row["atr"]) / row["entry"] * 100,
            "score": float(row["score"]),
        })
    return pd.DataFrame(feats, index=sig.index)


_HTFCACHE = {}
def htf_trend_series(df, cfg):
    key = id(df)
    if key not in _HTFCACHE:
        from backend.confluence_engine import htf_trend as ht
        _HTFCACHE[key] = ht(df, cfg, None)
    return _HTFCACHE[key]


def ml_meta(data):
    print("\n=== 5) ML META-FILTER (purged CV di TRAIN; OOS disentuh sekali) ===")
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import make_pipeline
    from sklearn.metrics import roc_auc_score
    EMBARGO = 50
    Xs, ys, meta = [], [], []
    for a, (tr, oo) in data.items():
        cfg = Config(**CHAMP)
        sig = generate_signals(tr, cfg)
        if sig.empty:
            continue
        trd, _ = backtest(tr, sig, cost=COST, daily_stop_r=1.0)
        F = build_features(tr, sig, cfg)
        lab = trd.set_index("time")["R"] > 0
        y = lab.reindex(sig.index).fillna(False).astype(int).to_numpy()
        # purge: buang sinyal dalam EMBARGO bar terakhir tiap ~500 bar fold-gap
        Xs.append((a, F, y, sig.index))
        ys.append((a, trd))
    # gabung dataset train
    F_all = pd.concat([f for _, f, _, _ in Xs])
    y_all = np.concatenate([y for _, _, y, _ in Xs])
    times_all = np.concatenate([idx.to_numpy() for _, _, _, idx in Xs])
    order = np.argsort(times_all)
    F_all, y_all = F_all.iloc[order].reset_index(drop=True), y_all[order]
    t_all = pd.Series(times_all[order])
    feats = list(F_all.columns)
    X = F_all[feats].fillna(0).to_numpy()
    # purged K-fold berurutan dgn embargo
    from sklearn.model_selection import TimeSeriesSplit
    aucs = []
    tscv = TimeSeriesSplit(n_splits=5, gap=EMBARGO)
    for tr_i, te_i in tscv.split(X):
        if len(np.unique(y_all[tr_i])) < 2:
            continue
        model = make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=1000))
        model.fit(X[tr_i], y_all[tr_i])
        p = model.predict_proba(X[te_i])[:, 1]
        if len(np.unique(y_all[te_i])) >= 2:
            aucs.append(roc_auc_score(y_all[te_i], p))
    print(f"  purged CV AUC per fold: {[round(a, 3) for a in aucs]} "
          f"(mean {np.mean(aucs):.3f})" if aucs else "  CV tidak cukup data")
    # latih penuh TRAIN -> filter OOS
    model = make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=1000))
    model.fit(X, y_all)
    for a, (tr, oo) in data.items():
        cfg = Config(**CHAMP)
        tail = tr.tail(90 * 24)
        part = pd.concat([tail, oo])
        sig = generate_signals(part, cfg)
        sig = sig[sig.index >= TRAIN_END]
        sub = part[part.index >= TRAIN_END]
        F_oos = build_features(sub, sig, cfg)
        F_oos = F_oos[feats].fillna(0)
        p = model.predict_proba(F_oos.to_numpy())[:, 1]
        _, m0 = backtest(sub, sig, cost=COST, daily_stop_r=1.0)
        for thr in (0.45, 0.5):
            keep = p >= thr
            sig_f = sig[keep] if len(keep) == len(sig) else sig
            _, m1 = backtest(sub, sig_f, cost=COST, daily_stop_r=1.0)
            print(f"  OOS {a} thr={thr}: n={m1.get('trades',0)} (dari {m0.get('trades',0)}) "
                  f"PF={m1.get('profit_factor','-')} vs base {m0.get('profit_factor','-')} "
                  f"ret={m1.get('return_%','-')} vs {m0.get('return_%','-')}%")


def main():
    data = load_all()
    portfolio(data)
    vol_sizing(data)
    exit_variants(data)
    regime(data)
    ml_meta(data)


if __name__ == "__main__":
    main()
