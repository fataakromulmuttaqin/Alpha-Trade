"""
Monte Carlo atas hasil trade backtest (deret R per trade).

- shuffle : urutan trade diacak. Expectancy tetap, tapi max drawdown bergantung urutan;
            dipakai untuk distribusi terburuk drawdown.
- bootstrap: trade diambil dengan pengembalian. Dipakai untuk interval kepercayaan
            expectancy dan probabilitas expectancy <= 0.

Input: DataFrame trade dari backtest() dengan kolom "R" (dan "equity" untuk drawdown).
"""
import numpy as np


def _equity_curve_maxdd(r, risk_pct=1.0, equity0=10_000.0):
    """Max drawdown (%) dari urutan R, dengan sizing risk_pct per trade (sama dengan backtest)."""
    eq = equity0 * np.cumprod(1 + risk_pct / 100 * np.asarray(r))
    peak = np.maximum.accumulate(eq)
    return float((eq / peak - 1).min() * 100)


def shuffle_drawdown(r, n=1000, seed=7, risk_pct=1.0):
    rng = np.random.default_rng(seed)
    r = np.asarray(r, dtype=float)
    dds = np.empty(n)
    for k in range(n):
        dds[k] = _equity_curve_maxdd(rng.permutation(r), risk_pct)
    return {
        "maxdd_median_%": round(float(np.median(dds)), 2),
        "maxdd_p95_%": round(float(np.percentile(dds, 5)), 2),  # 95% kasus: DD tidak lebih buruk dari ini
        "maxdd_worst_%": round(float(dds.min()), 2),
    }


def bootstrap_expectancy(r, n=5000, seed=7, ci=0.95):
    rng = np.random.default_rng(seed)
    r = np.asarray(r, dtype=float)
    if len(r) == 0:
        return {"trades": 0}
    idx = rng.integers(0, len(r), size=(n, len(r)))
    means = r[idx].mean(axis=1)
    lo, hi = np.percentile(means, [(1 - ci) / 2 * 100, (1 + ci) / 2 * 100])
    return {
        "trades": int(len(r)),
        "expectancy_R": round(float(r.mean()), 4),
        f"ci{int(ci*100)}_low_R": round(float(lo), 4),
        f"ci{int(ci*100)}_high_R": round(float(hi), 4),
        "p_expectancy_le_0": round(float((means <= 0).mean()), 4),
    }


def run(trades, n_shuffle=1000, n_boot=5000, seed=7, risk_pct=1.0):
    r = trades["R"].to_numpy()
    if len(r) == 0:
        return {"bootstrap": {"trades": 0}, "drawdown": {}}
    return {
        "bootstrap": bootstrap_expectancy(r, n=n_boot, seed=seed),
        "drawdown": shuffle_drawdown(r, n=n_shuffle, seed=seed, risk_pct=risk_pct),
    }


if __name__ == "__main__":
    import pandas as pd
    from .backtest import backtest
    from .significance import trend_gate_signals

    data = pd.read_csv("cache/PAXGUSDT_1h.csv", index_col=0, parse_dates=True)
    tr, m = backtest(data, trend_gate_signals(data), cost=0.0)
    print(m)
    print(run(tr))
