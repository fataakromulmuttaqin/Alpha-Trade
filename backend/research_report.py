"""
Jalankan analisis research (significance, Monte Carlo, ML gate) dan simpan hasilnya
ke JSON. Dashboard membaca file ini lewat /api/research; tidak dihitung ulang tiap snapshot.

  python -m backend.research_report cache/PAXGUSDT_1h.csv
"""
import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timezone

import pandas as pd

from .backtest import backtest
from .confluence_engine import Config, generate_signals
from .ml_gate import train_eval
from .montecarlo import run as mc_run
from .significance import significance, trend_gate_signals

OUT = os.path.join("cache", "research_report.json")


def build(df, cfg=Config()):
    sig_joint = significance(df, gate="joint", cfg=cfg, n_baseline=200)
    sig_trend = significance(df, gate="trend", cfg=cfg, n_baseline=200)

    tr, m = backtest(df, trend_gate_signals(df), cost=0.0)
    mc = mc_run(tr) if len(tr) else {}

    ml = train_eval(df, replace(cfg, min_confluence=2))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bars": len(df),
        "significance": {"joint": sig_joint, "trend": sig_trend},
        "montecarlo": {"trend_gate": mc},
        "ml_gate": ml,
        "config": {"min_confluence": cfg.min_confluence},
    }


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "cache/PAXGUSDT_1h.csv"
    data = pd.read_csv(path, index_col=0, parse_dates=True)
    report = build(data)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, default=str)
    print("wrote", OUT)
