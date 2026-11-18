"""Tuning edge engine + validasi out-of-sample yang jujur.

Protokol anti-overfit:
  - TRAIN : 2023-01 .. 2025-12  -> optuna memilih parameter di sini saja
  - OOS   : 2026-01 .. sekarang -> disentuh SEKALI di akhir, untuk laporan

Per timeframe (5m/15m/1h): optuna memaksimalkan expectancy MINIMUM antar fold
walk-forward pada TRAIN (fold minim trade dianggap 0). Parameter terbaik lalu
dievaluasi di OOS dibanding default tanpa tuning.

Pemakaian:
  python -m backend.tune_edge --tf 15m --trials 50
  python -m backend.tune_edge --tf all            # 5m/15m/1h berurutan
  python -m backend.tune_edge --report            # rangkum hasil di cache/
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.tradoor_feed import load_market            # noqa: E402
from backend.confluence_engine import Config, generate_signals  # noqa: E402
from backend.backtest import backtest                   # noqa: E402
from backend.tune import tune, SESSIONS                 # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_SRC = os.path.join(ROOT, "tradoor-data")
MARKET = "binance:PAXGUSDT"
COST = 0.3
TRAIN_END = "2026-01-01"          # OOS mulai di sini
WARMUP_DAYS = 90                  # ekor train dipakai warmup indikator OOS
CACHE = os.path.join(ROOT, "cache")


def to_tf(df1m, rule):
    return df1m.resample(rule).agg(
        {"open": "first", "high": "max", "low": "min",
         "close": "last", "volume": "sum"}).dropna()


def oos_eval(df_tf, best_kw, oos_start):
    """Sinyal di (ekor train + OOS) agar indikator hangat, lalu trade OOS saja."""
    tail = df_tf[df_tf.index < oos_start].tail(WARMUP_DAYS * 24 * (1440 // _tf_minutes(df_tf)))
    part = pd.concat([tail, df_tf[df_tf.index >= oos_start]])
    sig = generate_signals(part, Config(**best_kw))
    if sig.empty:
        return {"trades": 0}
    sig = sig[sig.index >= oos_start]
    _, m = backtest(part[part.index >= oos_start], sig, cost=COST)
    return m


def _tf_minutes(df_tf):
    delta = df_tf.index[1] - df_tf.index[0]
    return int(delta.total_seconds() // 60)


def run_tf(df1m, tf_rule, trials):
    df = to_tf(df1m, tf_rule)
    train = df[df.index < TRAIN_END]
    oos_start = pd.Timestamp(TRAIN_END, tz=df.index.tz)
    print(f"\n=== {tf_rule} | train {train.index[0].date()}..{train.index[-1].date()}"
          f" ({len(train):,} bar) | OOS dari {oos_start.date()} ===", flush=True)

    t0 = time.time()
    study = tune(train, base_cfg=Config(htf="4h"), n_trials=trials, n_splits=4, cost=COST)
    best = dict(study.best_params)
    direction, session = best.pop("direction"), best.pop("session")
    best["allow_long"] = direction in ("both", "long")
    best["allow_short"] = direction in ("both", "short")
    best["session_utc"] = SESSIONS[session]
    best["adx_min"] = float(best.pop("adx_min"))
    print(f"tuning {trials} trial selesai {time.time()-t0:.0f}s | "
          f"min-exp train = {study.best_value:.3f}", flush=True)

    oos_best = oos_eval(df, best, oos_start)
    oos_base = oos_eval(df, {}, oos_start)     # default tanpa tuning = baseline

    res = {"tf": tf_rule, "train_min_expectancy": round(study.best_value, 4),
           "best_params": best, "oos_tuned": oos_best, "oos_default": oos_base}
    print(f"  OOS TUNED  : {json.dumps(oos_best)}", flush=True)
    print(f"  OOS DEFAULT: {json.dumps(oos_base)}", flush=True)
    return res


def report():
    if not os.path.isdir(CACHE):
        return print("cache/ kosong")
    rows = []
    for fn in sorted(os.listdir(CACHE)):
        if fn.startswith("edge_tune_") and fn.endswith(".json"):
            with open(os.path.join(CACHE, fn), encoding="utf-8") as f:
                r = json.load(f)
            b, d = r["oos_tuned"], r["oos_default"]
            rows.append((r["tf"], r["train_min_expectancy"], b, d))
    print(f"{'TF':>5} | {'train minExp':>12} | {'OOS tuned':>38} | {'OOS default':>38}")
    for tf, te, b, d in rows:
        def cell(m):
            if not m.get("trades"):
                return "tidak ada trade"
            return (f"n={m['trades']} WR={m.get('win_rate_%')}% "
                    f"PF={m.get('profit_factor')} ret={m.get('return_%')}%")
        print(f"{tf:>5} | {te:>12} | {cell(b):>38} | {cell(d):>38}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="all", help="5m | 15m | 1h | all")
    ap.add_argument("--trials", type=int, default=50)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    if args.report:
        return report()

    os.makedirs(CACHE, exist_ok=True)
    t0 = time.time()
    df1m = load_market(DATA_SRC, MARKET, "1m", bars=10**9)
    print(f"data 1m: {len(df1m):,} bar ({df1m.index[0].date()}..{df1m.index[-1].date()}) "
          f"{time.time()-t0:.0f}s", flush=True)

    plan = {"5m": ("5min", 35), "15m": ("15min", 50), "1h": ("60min", 60)}
    if args.tf != "all":
        rule, tr = plan[args.tf]
        plan = {args.tf: (rule, args.trials)}

    for tf_name, (rule, trials) in plan.items():
        res = run_tf(df1m, rule, trials)
        with open(os.path.join(CACHE, f"edge_tune_{tf_name}.json"), "w",
                  encoding="utf-8") as f:
            json.dump(res, f, indent=1, default=str)
    report()


if __name__ == "__main__":
    main()
