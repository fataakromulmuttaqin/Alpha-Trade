"""
Deteksi struktur SMC: swing, BOS (Break of Structure), CHoCH (Change of Character).
Swing hanya "diketahui" L bar setelah terbentuk (tanpa lookahead).
"""
import numpy as np
import pandas as pd


def structure_events(df, L=5):
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    n = len(df)
    bos_bull = np.zeros(n, bool); bos_bear = bos_bull.copy()
    choch_bull = bos_bull.copy(); choch_bear = bos_bull.copy()

    swing_hi = swing_lo = None      # level swing terakhir yang sudah terkonfirmasi
    trend = 0                       # +1 bullish struktur, -1 bearish struktur
    for i in range(2 * L, n):
        p = i - L                   # kandidat pivot, baru terkonfirmasi di bar i
        if h[p] == h[p - L:p + L + 1].max():
            swing_hi = h[p]
        if l[p] == l[p - L:p + L + 1].min():
            swing_lo = l[p]

        if swing_hi is not None and c[i] > swing_hi:
            if trend == -1:
                choch_bull[i] = True        # break swing high saat struktur bearish
            else:
                bos_bull[i] = True          # kelanjutan bullish
            trend = 1
            swing_hi = None                 # level sudah "dipakai"
        elif swing_lo is not None and c[i] < swing_lo:
            if trend == 1:
                choch_bear[i] = True
            else:
                bos_bear[i] = True
            trend = -1
            swing_lo = None

    idx = df.index
    return pd.DataFrame({"bos_bull": bos_bull, "bos_bear": bos_bear,
                         "choch_bull": choch_bull, "choch_bear": choch_bear}, idx)


def structure_confirms(df, signals, window=10, L=5):
    """Tambah kolom struct_ok: ada BOS/CHoCH searah dalam `window` bar sebelum sinyal."""
    ev = structure_events(df, L)
    bull = (ev["bos_bull"] | ev["choch_bull"]).rolling(window, min_periods=1).max().astype(bool)
    bear = (ev["bos_bear"] | ev["choch_bear"]).rolling(window, min_periods=1).max().astype(bool)
    out = signals.copy()
    out["struct_ok"] = np.where(out["signal"] == 1, bull.reindex(out.index),
                                bear.reindex(out.index))
    return out
