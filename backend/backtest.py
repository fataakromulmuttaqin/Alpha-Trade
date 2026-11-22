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
from .confluence_engine import Config, generate_signals
from .smc_structure import structure_confirms


def backtest(df, signals, cost=0.0, risk_pct=1.0, equity0=10_000.0,
             tp1_frac=0.5, max_hold=200,
             daily_stop_r=0.0, daily_max_trades=0,
             trail_start_r=0.0, trail_atr=0.0, risk_scale=None):
    """daily_stop_r      : berhenti entry sisa hari itu setelah rugi kumulatif
                           mencapai -X R (R diakui saat trade close, dihitung
                           pada hari ENTRY-nya). 0 = mati.
    daily_max_trades    : batas jumlah entry per hari kalender. 0 = mati.
    trail_start_r/trail_atr : setelah profit mencapai trail_start_r x risk,
                           stop di-trail pada jarak trail_atr x ATR dari harga
                           terbaik. Butuh kolom 'atr' di signals. 0 = mati."""
    o, h, l = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
    pos = {t: i for i, t in enumerate(df.index)}
    equity, rows, busy_until = equity0, [], -1
    day_r, day_count, cur_day = 0.0, 0, None
    has_atr = "atr" in signals.columns

    for t, s in signals.iterrows():
        day = t.date()
        if day != cur_day:
            cur_day, day_r, day_count = day, 0.0, 0
        if daily_stop_r > 0 and day_r <= -daily_stop_r:
            continue                                        # daily stop: no entry
        if daily_max_trades > 0 and day_count >= daily_max_trades:
            continue
        i = pos[t] + 1
        if i >= len(df) or i <= busy_until:
            continue
        d = int(s["signal"])
        entry = o[i]
        sl, tp1, tp2 = s["sl"], s["tp1"], s["tp2"]
        risk = abs(s["entry"] - sl)
        if risk <= 0:
            continue
        # vol-targeted sizing: skala risiko per trade (opsional)
        eff_risk = risk_pct
        if risk_scale is not None:
            try:
                eff_risk = risk_pct * float(risk_scale.loc[t])
            except (KeyError, ValueError, TypeError):
                eff_risk = risk_pct
        # geser level mengikuti harga entry riil
        shift = entry - s["entry"]
        sl, tp1, tp2 = sl + shift, tp1 + shift, tp2 + shift
        atr = float(s["atr"]) if has_atr else 0.0
        trail_on = trail_atr > 0 and trail_start_r > 0 and atr > 0
        best = entry

        stop, hit1, r_total, exit_i = sl, False, 0.0, min(i + max_hold, len(df) - 1)
        for j in range(i, min(i + max_hold, len(df))):
            hi, lo = h[j], l[j]
            # 1) SL dengan stop iterasi sebelumnya (SL didahulukan)
            if (lo <= stop) if d == 1 else (hi >= stop):
                r_part = d * (stop - entry) / risk
                r_total += (1 - tp1_frac if hit1 else 1.0) * r_part
                exit_i = j
                break
            # 2) update trailing: stop hanya bergerak searah keuntungan
            if trail_on:
                if d == 1:
                    if hi > best:
                        best = hi
                    if best - entry >= trail_start_r * risk:
                        stop = max(stop, best - trail_atr * atr)
                        if lo <= stop:                      # stop baru tersentuh di bar yang sama
                            r_total += (1 - tp1_frac if hit1 else 1.0) * d * (stop - entry) / risk
                            exit_i = j
                            break
                else:
                    if lo < best or best == entry:
                        best = lo
                    if entry - best >= trail_start_r * risk:
                        stop = min(stop, best + trail_atr * atr)
                        if hi >= stop:
                            r_total += (1 - tp1_frac if hit1 else 1.0) * d * (stop - entry) / risk
                            exit_i = j
                            break
            # 3) TP1 lalu TP2
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
        equity *= 1 + eff_risk / 100 * r_total
        day_r += r_total
        day_count += 1
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
