"""
Backtest sinyal confluence.
Aturan eksekusi (konservatif):
  - Entry di OPEN bar setelah sinyal.
  - Jika SL dan TP tersentuh di bar yang sama -> dianggap SL dulu.
  - 50% posisi keluar di TP1, SL sisa dipindah ke breakeven, sisa menuju TP2.
  - Biaya (spread+komisi) dalam satuan harga, dikurangkan per trade.
"""
import numpy as np
import pandas as pd
from confluence_engine import Config, generate_signals
from smc_structure import structure_confirms


def backtest(df, signals, cost=0.0, risk_pct=1.0, equity0=10_000.0,
             tp1_frac=0.5, max_hold=200):
    o, h, l = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
    pos = {t: i for i, t in enumerate(df.index)}
    equity, rows, busy_until = equity0, [], -1

    for t, s in signals.iterrows():
        i = pos[t] + 1
        if i >= len(df) or i <= busy_until:
            continue
        d = int(s["signal"])
        entry = o[i]
        sl, tp1, tp2 = s["sl"], s["tp1"], s["tp2"]
        risk = abs(s["entry"] - sl)
        if risk <= 0:
            continue
        # geser level mengikuti harga entry riil
        shift = entry - s["entry"]
        sl, tp1, tp2 = sl + shift, tp1 + shift, tp2 + shift

        stop, hit1, r_total, exit_i = sl, False, 0.0, min(i + max_hold, len(df) - 1)
        for j in range(i, min(i + max_hold, len(df))):
            hi, lo = h[j], l[j]
            stop_hit = lo <= stop if d == 1 else hi >= stop
            if stop_hit:                                   # SL didahulukan
                r_part = d * (stop - entry) / risk
                r_total += (1 - tp1_frac if hit1 else 1.0) * r_part
                exit_i = j
                break
            if not hit1 and ((hi >= tp1) if d == 1 else (lo <= tp1)):
                hit1 = True
                r_total += tp1_frac * d * (tp1 - entry) / risk
                stop = entry                               # breakeven
            if hit1 and ((hi >= tp2) if d == 1 else (lo <= tp2)):
                r_total += (1 - tp1_frac) * d * (tp2 - entry) / risk
                exit_i = j
                break
        else:                                              # time stop
            last = o[exit_i]
            r_total += (1 - tp1_frac if hit1 else 1.0) * d * (last - entry) / risk

        r_total -= cost / risk
        equity *= 1 + risk_pct / 100 * r_total
        busy_until = exit_i
        rows.append({"time": t, "dir": d, "entry": entry, "R": r_total,
                     "equity": equity, "bars": exit_i - i + 1})

    tr = pd.DataFrame(rows)
    return tr, metrics(tr, equity0)


def metrics(tr, equity0):
    if tr.empty:
        return {"trades": 0}
    r = tr["R"]
    eq = tr["equity"]
    dd = (eq / eq.cummax() - 1).min()
    gp, gl = r[r > 0].sum(), -r[r < 0].sum()
    return {
        "trades": len(tr),
        "win_rate_%": round((r > 0).mean() * 100, 1),
        "avg_R": round(r.mean(), 3),
        "expectancy_R": round(r.mean(), 3),
        "profit_factor": round(gp / gl, 2) if gl > 0 else np.inf,
        "max_drawdown_%": round(dd * 100, 2),
        "return_%": round((eq.iloc[-1] / equity0 - 1) * 100, 2),
    }


def walk_forward(df, cfg=Config(), n_splits=4, **bt_kw):
    """Bagi data jadi n_splits segmen berurutan; lihat konsistensi metrik per segmen."""
    size = len(df) // n_splits
    out = []
    for k in range(n_splits):
        seg = df.iloc[k * size:(k + 1) * size]
        sig = generate_signals(seg, cfg)
        _, m = backtest(seg, sig, **bt_kw) if len(sig) else (None, {"trades": 0})
        out.append({"segment": k + 1, **m})
    return pd.DataFrame(out)


if __name__ == "__main__":
    # Ganti dengan data XAUUSD asli: pd.read_csv(..., index_col=0, parse_dates=True)
    rng = np.random.default_rng(7)
    n = 12000
    idx = pd.date_range("2025-01-01", periods=n, freq="15min")
    p = 2000 + np.cumsum(rng.normal(0.02, 1.2, n))
    o = p + rng.normal(0, .3, n); c = p + rng.normal(0, .3, n)
    data = pd.DataFrame({"open": o, "close": c,
                         "high": np.maximum(o, c) + abs(rng.normal(0, .8, n)),
                         "low": np.minimum(o, c) - abs(rng.normal(0, .8, n)),
                         "volume": rng.integers(100, 1000, n).astype(float)}, idx)

    cfg = Config(vp_prox_atr=3, atr_pct_low=0, atr_pct_high=1, htf="1h")  # longgar utk demo
    sig = structure_confirms(data, generate_signals(data, cfg))
    print(sig[["signal", "score", "struct_ok"]].head())
    trades, m = backtest(data, sig, cost=0.3)
    print(m)
    print(walk_forward(data, cfg, cost=0.3))
