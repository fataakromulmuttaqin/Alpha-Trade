"""Dump kasus validasi engine Python -> JSON untuk uji kesetiaan port Rust.

Menjalankan generate_signals pada data PAXG 1h asli (dari TDC1) + HTF 4h
native, membuang input & hasilnya ke native/tests/cases_*.json.
Pemakaian: python -m backend.dump_engine_cases
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.tradoor_feed import load_market          # noqa: E402
from backend.confluence_engine import Config, generate_signals  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "native", "tests")


def to_tf(df1m, rule):
    return df1m.resample(rule).agg(
        {"open": "first", "high": "max", "low": "min",
         "close": "last", "volume": "sum"}).dropna()


def dump(name, df, htf, cfg_dict):
    cfg = Config(**cfg_dict)
    sig = generate_signals(df, cfg, htf_df=htf)
    cases = {
        "config": cfg_dict,
        "entry": {
            "time": [str(t) for t in df.index],
            "open": df["open"].tolist(), "high": df["high"].tolist(),
            "low": df["low"].tolist(), "close": df["close"].tolist(),
            "volume": df["volume"].tolist(),
        },
        "htf": {
            "time": [str(t) for t in htf.index],
            "open": htf["open"].tolist(), "high": htf["high"].tolist(),
            "low": htf["low"].tolist(), "close": htf["close"].tolist(),
            "volume": htf["volume"].tolist(),
        },
        "signals": [
            {"time": str(t), "dir": int(r["signal"]), "entry": float(r["entry"]),
             "sl": float(r["sl"]), "tp1": float(r["tp1"]), "tp2": float(r["tp2"]),
             "score": int(r["score"])}
            for t, r in sig.iterrows()
        ],
    }
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"cases_{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cases, f)
    print(f"{name}: {len(df)} bar entry, {len(htf)} bar htf, {len(sig)} sinyal -> {path}")


def main():
    df1m = load_market(os.path.join(ROOT, "tradoor-data"), "binance:PAXGUSDT", "1m", bars=10**9)
    df1h = to_tf(df1m, "60min")
    df4h = to_tf(df1m, "4h")

    # 1) EDGE-1H penuh (3 tahun) — validasi port utama
    dump("edge1h_full", df1h, df4h,
         {"htf": "4h", "min_confluence": 1, "confirm_window": 2, "cooldown": 8,
          "session_utc": [7, 21], "adx_min": 20.0,
          "sl_atr_min": 1.96, "sl_atr_max": 3.10, "tp1_rr": 2.34, "tp2_rr": 3.68,
          "rsi_pull_long": 46.0, "rsi_pull_short": 69.8, "vp_prox_atr": 0.44})

    # 2) potongan 2026 dengan flag filter desain aktif (sesi/adx/arah/fib/round)
    d26 = df1h[df1h.index >= "2026-06-01"]
    h26 = df4h[df4h.index >= "2026-06-01"]
    dump("edge1h_2026_filters", d26, h26,
         {"htf": "4h", "min_confluence": 2, "confirm_window": 3, "cooldown": 10,
          "session_utc": [7, 21], "adx_min": 15.0,
          "round_gate": True, "round_prox_atr": 0.25, "round_entry_gate": True})


if __name__ == "__main__":
    main()
