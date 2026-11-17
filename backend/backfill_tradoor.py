"""
Backfill history Binance (arsip resmi data.binance.vision) -> partisi TDC1
yang sama dengan format data tradoor, sehingga bisa dibaca oleh
backend.tradoor_feed.load_market (dan oleh tradoor itu sendiri di Linux).

Contoh:
  python -m backend.backfill_tradoor --symbol PAXGUSDT --from 2023-01
  python -m backend.backfill_tradoor --symbol BTCUSDT --from 2021-01 --market binance-perp:BTCUSDT

Catatan format CSV arsip (spot monthly/daily klines):
  kolom: open_time, open, high, low, close, volume, close_time, quote,
         trades, taker_buy_base, taker_buy_quote, ignore (tanpa header)
  sejak 2025 file memakai MIKRO detik -> dinormalisasi ke milidetik.
"""
import argparse
import gzip
import io
import json
import os
import time
import zipfile
from datetime import datetime, timezone

import pandas as pd
import requests

ARCHIVE = "https://data.binance.vision/data/spot/monthly/klines/{sym}/1m/{sym}-1m-{ym}.zip"
ARCHIVE_DAILY = "https://data.binance.vision/data/spot/daily/klines/{sym}/1m/{sym}-1m-{ymd}.zip"


def _months(from_ym, to_ym):
    y, m = int(from_ym[:4]), int(from_ym[5:7])
    ey, em = int(to_ym[:4]), int(to_ym[5:7])
    while (y, m) <= (ey, em):
        yield f"{y:04d}-{m:02d}"
        m += 1
        if m > 12:
            y, m = y + 1, 1


def _fetch_df(url, retries=2):
    for attempt in range(retries + 1):
        r = requests.get(url, timeout=60)
        if r.status_code == 404:
            return None
        if r.status_code in (429, 418):
            time.sleep(int(r.headers.get("Retry-After", 5)))
            continue
        r.raise_for_status()
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        name = zf.namelist()[0]
        df = pd.read_csv(zf.open(name), header=None, usecols=range(11),
                         names=["open_time", "open", "high", "low", "close", "volume",
                                "close_time", "quote", "trades", "taker_base",
                                "taker_quote"])
        # arsip 2025+ memakai mikrodetik; normalisasi ke milidetik
        unit = "us" if int(df["open_time"].iloc[0]) > 10**14 else "ms"
        df["open_time"] = pd.to_datetime(df["open_time"], unit=unit, utc=True)
        df["close_time"] = pd.to_datetime(df["close_time"], unit=unit, utc=True)
        df = df.set_index("open_time").sort_index()
        df["volume"] = df["volume"].astype(float)
        return df[["open", "high", "low", "close", "volume"]]
    return None


def backfill(symbol, market, from_ym, out_dir):
    months = list(_months(from_ym, datetime.now(timezone.utc).strftime("%Y-%m")))
    mkt_dir = market.replace(":", "_")
    base = os.path.join(out_dir, mkt_dir, "1m")
    os.makedirs(base, exist_ok=True)
    bases = []
    total = 0
    for ym in months:
        path = os.path.join(base, f"{ym}.tdc.gz")
        df = None
        if ym == datetime.now(timezone.utc).strftime("%Y-%m"):
            # bulan berjalan: rakit dari file harian
            from datetime import timedelta
            today = datetime.now(timezone.utc).date()
            frames = []
            d = today.replace(day=1)
            while d <= today:
                u = ARCHIVE_DAILY.format(sym=symbol.upper(),
                                         ymd=d.strftime("%Y-%m-%d"))
                part = _fetch_df(u)
                if part is not None:
                    frames.append(part)
                d += timedelta(days=1)
            if frames:
                df = pd.concat(frames)
        else:
            df = _fetch_df(ARCHIVE.format(sym=symbol.upper(), ym=ym))
        if df is None or df.empty:
            print(f"  - {ym}: kosong/404, dilewati", flush=True)
            continue
        # tulis partisi TDC1 (kolom volume, flags=0)
        from .tradoor_feed import write_tdc1
        blob = write_tdc1(df, 1)
        with open(path, "wb") as f:
            f.write(blob)
        bases.append({"period": ym, "rows": len(df)})
        total += len(df)
        print(f"  + {ym}: {len(df)} bar ({len(blob)/1024:.0f} KB)", flush=True)
        time.sleep(0.3)
    manifest = {"market": market, "updatedAt": int(time.time() * 1000),
                "bases": {"1m": bases}}
    with open(os.path.join(out_dir, mkt_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    print(f"SELESAI: {total} bar 1m untuk {market} -> {out_dir}", flush=True)
    return total


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="PAXGUSDT")
    ap.add_argument("--market", default="binance:PAXGUSDT",
                    help="penamaan market tradoor (venue:SYMBOL)")
    ap.add_argument("--from", dest="from_ym", default="2023-01")
    ap.add_argument("--to", dest="to_ym",
                    default=datetime.now(timezone.utc).strftime("%Y-%m"))
    ap.add_argument("--out", default=os.getenv("AT_TRADOOR_DATA", "tradoor-data/data"))
    a = ap.parse_args()
    backfill(a.symbol, a.market, a.from_ym, a.out)
