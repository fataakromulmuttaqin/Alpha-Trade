"""
Rule significance test: apakah ekspektansi sinyal lebih baik dari entry acak
dengan jarak SL/TP yang sama, pada data historis yang sama.

Metode:
  - Sinyal nyata: generate_signals() + structure_confirms() -> backtest().
  - Baseline: N kali entry acak di bar yang sama jumlahnya, arah acak 50/50,
    jarak SL/TP diambil acak dari sinyal nyata. Backtest yang sama.
  - p-value = fraksi baseline dengan expectancy_R >= nilai nyata (one-sided).

Catatan: aturan gabungan (5 gate AND) sering menghasilkan 0 sinyal; test ini
butuh sampel. Gunakan gate="trend" untuk menguji satu gate saja (sampel lebih besar).
"""
import numpy as np
import pandas as pd

from .backtest import backtest
from .confluence_engine import Config, generate_signals, htf_trend, ema
from .smc_structure import structure_confirms


def real_signals(df, cfg=Config()):
    return structure_confirms(df, generate_signals(df, cfg))


def trend_gate_signals(df, cfg=Config()):
    """Sinyal sederhana: arah HTF trend + stack EMA di TF entry, entry di bar close."""
    d = df.copy()
    c = d["close"]
    e9, e20, e50 = ema(c, 9), ema(c, 20), ema(c, 50)
    trend = htf_trend(d.assign(ema9=e9, ema20=e20, ema50=e50), cfg, None)
    long_ok = (trend == 1) & (e9 > e20) & (c > e50)
    short_ok = (trend == -1) & (e9 < e20) & (c < e50)
    sig = pd.Series(0, d.index)
    sig[long_ok] = 1
    sig[short_ok & ~long_ok] = -1
    sig = sig[sig != 0]
    atr_s = (d["high"] - d["low"]).rolling(cfg.atr_len).mean().reindex(sig.index)
    dist = cfg.sl_atr_min * atr_s
    cs = c.reindex(sig.index)
    s = sig.to_numpy()
    return pd.DataFrame({"signal": s, "entry": cs, "sl": cs - s * dist,
                         "tp1": cs + s * cfg.tp1_rr * dist,
                         "tp2": cs + s * cfg.tp2_rr * dist,
                         "score": 0, "reason": "trend-gate"}, index=sig.index).dropna()


def random_signals(df, template, rng):
    """Entry acak pada bar yang sama jumlahnya dengan template; jarak SL/TP dari template."""
    n = len(template)
    if n == 0:
        return template.copy()
    pick = rng.choice(len(df) - 2, size=n, replace=False)
    idx = df.index[np.sort(pick)]
    dirs = rng.choice([1, -1], size=n)
    dist = (template["entry"] - template["sl"]).abs().to_numpy()
    tp1d = (template["tp1"] - template["entry"]).abs().to_numpy()
    tp2d = (template["tp2"] - template["entry"]).abs().to_numpy()
    dist, tp1d, tp2d = (rng.permutation(x) for x in (dist, tp1d, tp2d))
    entry = df["close"].reindex(idx).to_numpy()
    return pd.DataFrame({"signal": dirs, "entry": entry,
                         "sl": entry - dirs * dist,
                         "tp1": entry + dirs * tp1d,
                         "tp2": entry + dirs * tp2d,
                         "score": 0, "reason": "random"}, index=idx)


def significance(df, gate="joint", cfg=Config(), n_baseline=500, seed=7, cost=0.0, **bt_kw):
    sig = real_signals(df, cfg) if gate == "joint" else trend_gate_signals(df, cfg)
    if sig.empty:
        return {"gate": gate, "trades_real": 0, "note": "tidak ada sinyal nyata"}
    _, m_real = backtest(df, sig, cost=cost, **bt_kw)
    e_real = m_real.get("expectancy_R", 0.0)

    rng = np.random.default_rng(seed)
    base = np.empty(n_baseline)
    for k in range(n_baseline):
        tr, _ = backtest(df, random_signals(df, sig, rng), cost=cost, **bt_kw)
        base[k] = tr["R"].mean() if len(tr) else 0.0

    p = float((base >= e_real).mean())
    return {
        "gate": gate,
        "trades_real": m_real["trades"],
        "expectancy_R_real": e_real,
        "baseline_mean_R": round(float(base.mean()), 4),
        "baseline_p95_R": round(float(np.percentile(base, 95)), 4),
        "p_value": p,
        "significant_5pct": p < 0.05,
        "n_baseline": n_baseline,
    }


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "cache/PAXGUSDT_1h.csv"
    data = pd.read_csv(path, index_col=0, parse_dates=True)
    for g in ("joint", "trend"):
        print(significance(data, gate=g, n_baseline=200))
