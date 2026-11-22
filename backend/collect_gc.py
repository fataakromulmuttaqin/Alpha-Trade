"""Kolektor data emas asli COMEX (GC=F) 15m via yfinance.

Menumpuk candle 15m emas asli ke cache/gcf_15m.csv (dedup by timestamp).
Jalankan berkala (mis. per jam via Task Scheduler) — dalam 2-3 bulan cukup
untuk memvalidasi profil EDGE-1H di emas ASLI, bukan proxy PAXG.

Pemakaian: python -m backend.collect_gc
"""
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "cache", "gcf_15m.csv")


def main():
    try:
        import yfinance as yf
    except ImportError:
        sys.exit("pip install yfinance")
    df = yf.download("GC=F", period="60d", interval="15m", progress=False,
                     auto_adjust=False)
    if df is None or df.empty:
        sys.exit("yfinance kosong")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    df.index.name = "time"
    df = df.dropna()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    if os.path.exists(OUT):
        old = pd.read_csv(OUT, index_col="time", parse_dates=True)
        merged = pd.concat([old, df])
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    else:
        merged = df
    merged.to_csv(OUT)
    print(f"GC=F 15m: {len(merged):,} bar tersimpan ({merged.index[0]} .. {merged.index[-1]})")


if __name__ == "__main__":
    main()
