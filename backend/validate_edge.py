"""Validasi jujur edge engine confluence pada data asli (bukan simulasi).

Memuat seluruh history 1m PAXGUSDT (proxy XAUUSD) dari arsip TDC1,
menghitung sinyal engine 5-gate pada beberapa konfigurasi, lalu backtest
dengan biaya realistis (spread+komisi 0.3 satuan harga per trade) dan
walk-forward untuk menguji konsistensi antar periode.

Pemakaian:  python -m backend.validate_edge
"""
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.tradoor_feed import load_market          # noqa: E402
from backend.confluence_engine import Config, generate_signals  # noqa: E402
from backend.backtest import backtest, walk_forward   # noqa: E402

DATA_SRC = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tradoor-data")
MARKET = "binance:PAXGUSDT"
COST = 0.3          # spread+komisi XAUUSD realistis (satuan harga, per trade)


def to_tf(df1m, rule):
    out = df1m.resample(rule).agg(
        {"open": "first", "high": "max", "low": "min",
         "close": "last", "volume": "sum"}).dropna()
    return out


def dir_breakdown(tr):
    if tr.empty:
        return ""
    parts = []
    for d, label in ((1, "LONG"), (-1, "SHORT")):
        r = tr.loc[tr["dir"] == d, "R"]
        if len(r) == 0:
            continue
        gp, gl = r[r > 0].sum(), -r[r < 0].sum()
        pf = round(gp / gl, 2) if gl > 0 else float("inf")
        parts.append(f"{label} n={len(r)} WR={(r>0).mean()*100:.0f}% PF={pf}")
    return " | ".join(parts)


def main():
    t0 = time.time()
    df1m = load_market(DATA_SRC, MARKET, "1m", bars=10**9)
    print(f"data 1m : {len(df1m):,} bar  {df1m.index[0]} .. {df1m.index[-1]}"
          f"  (load {time.time()-t0:.0f}s)")

    df5 = to_tf(df1m, "5min")
    df4h = to_tf(df1m, "4h")
    print(f"5m      : {len(df5):,} bar   4h: {len(df4h):,} bar\n")

    variants = [
        (4, True,  "GOLDEN      (4/4 gate + tren)"),
        (3, True,  "STANDARD    (3/4 gate + tren)"),
        (2, True,  "LOOSE       (2/4 gate + tren)"),
        (1, True,  "AGGRESSIVE  (1/4 gate + tren)"),
        (3, False, "PING PONG   (3/4, tanpa gate tren)"),
    ]
    results = {}
    for mc, gate, label in variants:
        t1 = time.time()
        cfg = Config(min_confluence=mc, gate_trend=gate, htf="4h")
        sig = generate_signals(df5, cfg, htf_df=df4h)
        tr, m = backtest(df5, sig, cost=COST)
        results[label] = (cfg, tr, m)
        print(f"{label}  -> trades={m.get('trades',0):3d}  "
              f"WR={m.get('win_rate_%','-')}%  PF={m.get('profit_factor','-')}  "
              f"avgR={m.get('avg_R','-')}  maxDD={m.get('max_drawdown_%','-')}%  "
              f"ret={m.get('return_%','-')}%   ({time.time()-t1:.0f}s)")
        if m.get("trades"):
            print(f"{'':28s}{dir_breakdown(tr)}")

    # sensitivitas biaya pada konfigurasi ketat (GOLDEN)
    cfg = results["GOLDEN      (4/4 gate + tren)"][0]
    sig = generate_signals(df5, cfg, htf_df=df4h)
    print("\nsensitivitas biaya (GOLDEN):")
    for c in (0.0, 0.15, 0.3, 0.5):
        _, m = backtest(df5, sig, cost=c)
        print(f"  cost={c:<5} trades={m.get('trades',0):3d}  "
              f"WR={m.get('win_rate_%','-')}%  PF={m.get('profit_factor','-')}  "
              f"ret={m.get('return_%','-')}%")

    # konsistensi antar periode (walk-forward 4 segmen) untuk GOLDEN & STANDARD
    def fmt(v, pct=False):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return "-"
        return f"{v:g}{'%' if pct else ''}"

    print("\nwalk-forward (4 segmen berurutan, cost=0.3):")
    for label in ("GOLDEN      (4/4 gate + tren)", "STANDARD    (3/4 gate + tren)"):
        cfg = results[label][0]
        wf = walk_forward(df5, cfg, n_splits=4, cost=COST)
        print(f"  {label}")
        for _, row in wf.iterrows():
            print(f"    segmen {fmt(row['segment'])}: trades={fmt(row.get('trades'))}"
                  f"  WR={fmt(row.get('win_rate_%'))}%  PF={fmt(row.get('profit_factor'))}"
                  f"  ret={fmt(row.get('return_%'))}%")


if __name__ == "__main__":
    main()
