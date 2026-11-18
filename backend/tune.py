"""
Tuning parameter Config dengan Optuna, objektif = walk-forward.

Objektif: MIN expectancy_R di antara segmen walk-forward (bukan rata-rata),
supaya parameter yang menang harus konsisten di semua segmen, bukan satu split beruntung.
Fold dengan trade < min_fold_trades dihitung expectancy 0 — mencegah "menang"
lewat segmen dengan 3 trade keberuntungan.

Catatan overfit: hasil tuning di data yang sama tidak membuktikan edge. Validasi pakai
data di luar sampel tuning (mis. tahun berikutnya) sebelum dipercaya.
"""
import numpy as np
import optuna
import pandas as pd

from .backtest import walk_forward
from .confluence_engine import Config

SPACE = {
    "sl_atr_min": ("float", 1.0, 2.0),
    "sl_atr_max": ("float", 1.5, 3.5),
    "tp1_rr": ("float", 1.0, 2.5),
    "tp2_rr": ("float", 2.0, 5.0),
    "rsi_pull_long": ("float", 30, 50),
    "rsi_pull_short": ("float", 50, 70),
    "confirm_window": ("int", 2, 8),
    "vp_prox_atr": ("float", 0.4, 1.2),
    "cooldown": ("int", 3, 30),
    "min_confluence": ("int", 1, 4),
    # filter desain (kategorikal) — dipilih optuna, bukan diasumsikan
    "direction": ("cat", ("both", "long", "short")),
    "session": ("cat", ("all", "ldn_ny", "ny", "ldn")),
    "adx_min": ("cat", (0, 15, 20, 25)),
}

SESSIONS = {"all": None, "ldn_ny": (7, 21), "ny": (12, 21), "ldn": (7, 12)}


def _make_cfg(trial, base):
    kw = {}
    for name, spec in SPACE.items():
        kind = spec[0]
        if kind == "float":
            kw[name] = trial.suggest_float(name, spec[1], spec[2])
        elif kind == "int":
            kw[name] = trial.suggest_int(name, spec[1], spec[2])
        else:  # cat
            kw[name] = trial.suggest_categorical(name, spec[1])
    direction, session, adx = kw.pop("direction"), kw.pop("session"), kw.pop("adx_min")
    kw["allow_long"] = direction in ("both", "long")
    kw["allow_short"] = direction in ("both", "short")
    kw["session_utc"] = SESSIONS[session]
    kw["adx_min"] = float(adx)
    return Config(**{**base.__dict__, **kw})


def objective_factory(df, base_cfg, n_splits=4, cost=0.0, min_fold_trades=10):
    def objective(trial):
        cfg = _make_cfg(trial, base_cfg)
        wf = walk_forward(df, cfg, n_splits=n_splits, cost=cost)
        if "expectancy_R" in wf.columns:
            exp = wf["expectancy_R"].fillna(0.0).to_numpy()
            ntr = wf["trades"].fillna(0).to_numpy()
            exp = np.where(ntr < min_fold_trades, 0.0, exp)
        else:
            exp = np.zeros(len(wf))
        trial.set_user_attr("segments", wf.to_dict("records"))
        return float(np.min(exp))
    return objective


def tune(df, base_cfg=Config(), n_trials=30, n_splits=4, cost=0.0, seed=7):
    study = optuna.create_study(direction="maximize",
                                sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(objective_factory(df, base_cfg, n_splits, cost), n_trials=n_trials,
                   show_progress_bar=False)
    return study


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "cache/PAXGUSDT_1h.csv"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    data = pd.read_csv(path, index_col=0, parse_dates=True)
    s = tune(data, n_trials=n)
    print("best min-segment expectancy:", round(s.best_value, 4))
    print("best params:", s.best_params)
