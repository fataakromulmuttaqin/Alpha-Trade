"""
Gerbang ML di atas sinyal confluence: prediksi apakah sebuah sinyal akan kena TP1 sebelum SL.

- label : 1 jika harga menyentuh TP1 sebelum SL dalam max_hold bar, 0 jika SL duluan / habis waktu.
- fitur : score konfluensi + RSI + ATR relatif terhadap harga (hanya info yang ada saat entry).
- split : kronologis (train = awal, test = akhir). Tanpa acak, supaya tidak ada lookahead.

Model: regresi logistik. Hasil di-benchmark terhadap base rate win di test set.
"""
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler

from .confluence_engine import Config, atr, generate_signals, rsi


def build_labels(df, signals, max_hold=200):
    """Label 1/0 per sinyal: TP1 duluan (1) vs SL duluan atau habis waktu (0)."""
    idx = {t: i for i, t in enumerate(df.index)}
    hi, lo = df["high"].to_numpy(), df["low"].to_numpy()
    out = {}
    for t, s in signals.iterrows():
        i = idx.get(t)
        if i is None:
            continue
        long = int(s["signal"]) == 1
        tp1, sl = float(s["tp1"]), float(s["sl"])
        label = 0
        for j in range(i + 1, min(i + 1 + max_hold, len(df))):
            if long:
                if lo[j] <= sl:
                    break
                if hi[j] >= tp1:
                    label = 1
                    break
            else:
                if hi[j] >= sl:
                    break
                if lo[j] <= tp1:
                    label = 1
                    break
        out[t] = label
    return pd.Series(out, name="label")


def features(df, signals, cfg=Config()):
    """Fitur yang sudah diketahui saat sinyal muncul (tanpa lookahead)."""
    atr_s = atr(df, cfg.atr_len)
    rsi_s = rsi(df["close"], cfg.rsi_len)
    close = df["close"].reindex(signals.index)
    atr_rel = (atr_s.reindex(signals.index) / close).replace([np.inf, -np.inf], np.nan)
    X = pd.DataFrame({
        "score": signals["score"].astype(float),
        "rsi": rsi_s.reindex(signals.index),
        "atr_rel": atr_rel,
        "dir": signals["signal"].astype(float),
    }, index=signals.index)
    return X.dropna()


def train_eval(df, cfg=Config(), split=0.7, max_hold=200):
    sig = generate_signals(df, cfg)
    X = features(df, sig, cfg)
    y = build_labels(df, sig.loc[X.index], max_hold=max_hold).reindex(X.index).dropna()
    X = X.loc[y.index]
    if len(y) < 30:
        return {"n": len(y), "note": "sinyal terlalu sedikit untuk melatih model"}

    cut = int(len(y) * split)
    Xtr, Xte, ytr, yte = X.iloc[:cut], X.iloc[cut:], y.iloc[:cut], y.iloc[cut:]
    if ytr.nunique() < 2 or yte.nunique() < 2:
        return {"n": len(y), "note": "satu kelas saja di train/test; tidak bisa dievaluasi"}

    sc = StandardScaler().fit(Xtr)
    model = LogisticRegression(max_iter=1000).fit(sc.transform(Xtr), ytr)
    p = model.predict_proba(sc.transform(Xte))[:, 1]

    return {
        "n_train": len(ytr), "n_test": len(yte),
        "base_win_test": round(float(yte.mean()), 3),
        "auc_test": round(float(roc_auc_score(yte, p)), 3),
        "coef": dict(zip(X.columns, np.round(model.coef_[0], 3))),
    }


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "cache/PAXGUSDT_1h.csv"
    data = pd.read_csv(path, index_col=0, parse_dates=True)
    print(train_eval(data))
