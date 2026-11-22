"""Eksperimen struktur FzX: multi-scale BOS + IDM-sweep entry (TRAIN→OOS).

Model kandidat (konfigurasi opsional di atas champion EDGE-1H):
  A) MAJOR-trend gate: swing skala besar (lookback panjang) menentukan arah;
  B) IDM-sweep: setelah BOS searah, sinyal hanya valid bila harga menyapu
     inducement (pullback) yang terbentuk pasca-BOS.

Diuji di TRAIN (2023-2025) per aset, lalu verifikasi OOS (2026) sekali.
Pemakaian: python -m backend.research_idm
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.tradoor_feed import load_market                    # noqa: E402
from backend.confluence_engine import Config, generate_signals  # noqa: E402
from backend.backtest import backtest                           # noqa: E402

COST = 0.3
TRAIN_END = "2026-01-01"
CHAMP = {
    "htf": "4h", "min_confluence": 1, "confirm_window": 2, "cooldown": 8,
    "session_utc": (7, 21), "adx_min": 20.0,
    "sl_atr_min": 1.96, "sl_atr_max": 3.10, "tp1_rr": 2.34, "tp2_rr": 3.68,
    "rsi_pull_long": 46.0, "rsi_pull_short": 69.8, "vp_prox_atr": 0.44,
}
ASSETS = {"PAXG": "binance:PAXGUSDT", "BTC": "binance:BTCUSDT"}


def to_tf(df1m, rule):
    return df1m.resample(rule).agg(
        {"open": "first", "high": "max", "low": "min",
         "close": "last", "volume": "sum"}).dropna()


def idm_sweep_mask(df, sig, bos_lookback=40, idm_pull=3):
    """True pada bar sinyal yang menyapu IDM pasca-BOS searah.

    Aproksiasi IDM: setelah BOS (close menembus ekstrem N bar), pullback
    pertama berlawanan arah selama >= idm_pull bar = level inducement;
    sinyal valid bila harga kembali menyentuh level itu (entry 'lebih dalam').
    """
    h, l, c = (df[k].to_numpy() for k in ("high", "low", "close"))
    n = len(df)
    out = np.zeros(n, bool)
    # state pasca-BOS
    bos_dir = 0        # +1 bos naik terakhir, -1 bos turun
    idm_level = np.nan
    idm_since = -1
    for i in range(bos_lookback, n):
        lo_ext = l[i - bos_lookback:i].min()
        hi_ext = h[i - bos_lookback:i].max()
        if c[i] > hi_ext and bos_dir != 1:
            bos_dir = 1
            idm_level = l[i - idm_pull:i].max()   # pullback tertinggi sebelum impuls
            idm_since = i
        elif c[i] < lo_ext and bos_dir != -1:
            bos_dir = -1
            idm_level = h[i - idm_pull:i].min()
            idm_since = i
        # sweep IDM searah
        if bos_dir == 1 and not np.isnan(idm_level):
            if l[i] <= idm_level and c[i] > idm_level:
                out[i] = True
                idm_level = np.nan          # sekali pakai
        elif bos_dir == -1 and not np.isnan(idm_level):
            if h[i] >= idm_level and c[i] < idm_level:
                out[i] = True
                idm_level = np.nan
    return pd.Series(out, index=df.index)


def mline(m):
    if not m.get("trades"):
        return "n=0"
    return (f"n={m['trades']:3d} WR={m.get('win_rate_%','-')}% PF={m.get('profit_factor','-')} "
            f"ret={m.get('return_%','-')}%")


def main():
    print("=== FzX: IDM-sweep & multi-scale (di atas champion) ===")
    data = {}
    for a, mkt in ASSETS.items():
        df1m = load_market(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "tradoor-data"), mkt, "1m", bars=10**9)
        df = to_tf(df1m, "60min")
        data[a] = (df[df.index < TRAIN_END], df[df.index >= TRAIN_END])

    for a, (tr, oo) in data.items():
        cfg = Config(**CHAMP)
        for label, part, warm in (("TRAIN", tr, False), ("OOS", None, True)):
            if warm:
                tail = tr.tail(90 * 24)
                part = pd.concat([tail, oo])
            sig = generate_signals(part, cfg)
            if label == "OOS":
                sig = sig[sig.index >= TRAIN_END]
                part_s = part[part.index >= TRAIN_END]
            else:
                part_s = part
            _, m0 = backtest(part_s, sig, cost=COST, daily_stop_r=1.0)
            # gate IDM-sweep: bar sinyal harus menyapu IDM
            idm = idm_sweep_mask(part, sig)
            sig_idm = sig[sig.index.map(lambda t: bool(idm.loc[t]))]
            _, m1 = backtest(part_s, sig_idm, cost=COST, daily_stop_r=1.0)
            print(f"  {a} {label}: base {mline(m0)}")
            print(f"  {a} {' '*len(label)}  idm {mline(m1)}")


if __name__ == "__main__":
    main()
