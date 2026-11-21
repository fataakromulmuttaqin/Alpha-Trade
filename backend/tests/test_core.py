"""Uji unit jalur kritis Alpha-Trade: engine, backtest, PaperSim, TDC1.

Jalankan dari root repo:  python -m pytest backend/tests -q
"""
import sys
import pathlib

import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.confluence_engine import Config, generate_signals  # noqa: E402
from backend.backtest import backtest, metrics                 # noqa: E402
from backend.paper_sim import PaperSim                         # noqa: E402


# ----------------------------- helpers --------------------------------------
def synth_df(n=800, seed=3, rule="15min", base=2000.0, drift=0.02):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq=rule, tz="UTC")
    p = base + np.cumsum(rng.normal(drift, 1.2, n))
    o = p + rng.normal(0, 0.3, n)
    c = p + rng.normal(0, 0.3, n)
    return pd.DataFrame({
        "open": o, "close": c,
        "high": np.maximum(o, c) + np.abs(rng.normal(0, 0.8, n)),
        "low": np.minimum(o, c) - np.abs(rng.normal(0, 0.8, n)),
        "volume": rng.integers(100, 1000, n).astype(float),
    }, index=idx)


def loose_cfg(**kw):
    """Config longgar supaya data sintetis menghasilkan sinyal."""
    return Config(htf="4h", min_confluence=1, rsi_pull_long=50,
                  rsi_pull_short=50, atr_pct_low=0.0, atr_pct_high=1.0,
                  cooldown=3, **kw)


# ----------------------------- engine ---------------------------------------
def test_engine_columns_and_determinism():
    df = synth_df()
    s1 = generate_signals(df, loose_cfg())
    s2 = generate_signals(df, loose_cfg())
    for col in ("signal", "entry", "sl", "tp1", "tp2", "score", "reason", "atr"):
        assert col in s1.columns, f"kolom {col} hilang"
    pd.testing.assert_frame_equal(s1, s2)          # deterministik
    assert (s1["signal"].isin([1, -1])).all()      # hanya baris bersinyal
    assert (s1["atr"] > 0).all()


def test_engine_session_filter():
    df = synth_df(1200)
    cfg = loose_cfg(session_utc=(12, 14))          # hanya 12:00-13:59 UTC
    s = generate_signals(df, cfg)
    hours = s.index.hour
    assert ((hours >= 12) & (hours < 14)).all(), "ada sinyal di luar sesi"


def test_engine_direction_filters():
    df = synth_df(1500, seed=11)
    sl = generate_signals(df, loose_cfg(allow_long=False))
    assert (sl["signal"] == -1).all() if len(sl) else True
    ss = generate_signals(df, loose_cfg(allow_short=False))
    assert (ss["signal"] == 1).all() if len(ss) else True


# ----------------------------- backtest -------------------------------------
def test_backtest_tp1_then_sl_at_breakeven():
    """Entry 100, SL 95 (risk 5), TP1 105 (=1R):
    bar-entry sentuh TP1 (+0.5R, stop pindah BE=100), bar berikutnya kena BE
    (sisa 0R) -> total R = +0.5."""
    idx = pd.date_range("2024-01-01", periods=4, freq="15min", tz="UTC")
    df = pd.DataFrame({"open": [100.0, 100.0, 101.0, 99.0],
                       "high": [101.0, 106.0, 101.0, 99.5],
                       "low": [99.0, 99.0, 99.0, 98.5],
                       "close": [100.5, 100.5, 99.5, 99.0],
                       "volume": [1.0, 1, 1, 1]}, index=idx)
    sig = pd.DataFrame({"signal": [1], "entry": [100.0], "sl": [95.0],
                        "tp1": [105.0], "tp2": [110.0], "atr": [5.0]},
                       index=[idx[0]])
    tr, m = backtest(df, sig, cost=0.0)
    assert m["trades"] == 1
    assert abs(tr.iloc[0]["R"] - 0.5) < 1e-9


def test_backtest_sl_first_on_same_bar():
    """Bar entry menyentuh SL dan TP di bar yang sama -> SL dihitung (konservatif)."""
    idx = pd.date_range("2024-01-01", periods=2, freq="15min", tz="UTC")
    df = pd.DataFrame({"open": [100.0, 100.0], "high": [100.5, 121.0],
                       "low": [99.5, 89.0], "close": [100.0, 95.0],
                       "volume": [1.0, 1]}, index=idx)
    sig = pd.DataFrame({"signal": [1], "entry": [100.0], "sl": [90.0],
                        "tp1": [120.0], "tp2": [140.0], "atr": [5.0]},
                       index=[idx[0]])
    tr, m = backtest(df, sig, cost=0.0)
    assert m["trades"] == 1 and tr.iloc[0]["R"] == pytest.approx(-1.0)


def test_backtest_daily_stop_blocks_entry():
    """Trade pertama hari itu loss -1R; daily_stop_r=0.5 memblok sinyal kedua."""
    idx = pd.date_range("2024-01-01", periods=10, freq="15min", tz="UTC")
    df = pd.DataFrame({"open": [100.0, 100.0, 99.0, 99.0, 99.0,
                                100.0, 100.0, 100.0, 100.0, 100.0],
                       "high": [100.5, 100.5, 99.5, 99.5, 99.5,
                                100.5, 100.5, 100.5, 100.5, 100.5],
                       "low": [99.5, 98.5, 98.5, 98.5, 98.5,
                               99.5, 99.5, 99.5, 99.5, 99.5],
                       "close": [100.0, 99.0, 99.0, 99.0, 99.0,
                                 100.0, 100.0, 100.0, 100.0, 100.0],
                       "volume": [1.0] * 10}, index=idx)
    sig = pd.DataFrame({
        "signal": [1, 1], "entry": [100.0, 100.0], "sl": [99.0, 99.0],
        "tp1": [101.0, 101.0], "tp2": [102.0, 102.0], "atr": [1.0, 1.0]},
        index=[idx[0], idx[4]])
    tr, m = backtest(df, sig, cost=0.0, daily_stop_r=0.5)
    assert m["trades"] == 1, "daily stop harus memblok trade kedua"
    tr2, m2 = backtest(df, sig, cost=0.0)
    assert m2["trades"] == 2, "tanpa daily stop keduanya entry"


def test_backtest_daily_max_trades():
    idx = pd.date_range("2024-01-01", periods=40, freq="15min", tz="UTC")
    df = synth_df(40, seed=6)
    df.index = idx
    sig = pd.DataFrame({
        "signal": [1, 1, 1], "entry": [100.0] * 3, "sl": [99.0] * 3,
        "tp1": [101.0] * 3, "tp2": [102.0] * 3, "atr": [1.0] * 3},
        index=[idx[0], idx[4], idx[8]])
    tr, m = backtest(df, sig, cost=0.0, daily_max_trades=2)
    assert m["trades"] == 2


def test_backtest_trailing_locks_profit():
    """Entry 100 (risk 10, SL 90, TP jauh): harga spike ke 115 lalu jatuh.
    Tanpa trail: time stop di 95 -> R = -0.5.
    Dengan trail (mulai 0.5R, jarak 1 ATR=5): stop terkunci di 110 -> R = +1.0."""
    idx = pd.date_range("2024-01-01", periods=5, freq="15min", tz="UTC")
    df = pd.DataFrame({
        "open": [100.0, 100.0, 111.0, 96.0, 95.0],
        "high": [101.0, 115.0, 111.0, 97.0, 96.0],
        "low": [99.0, 99.0, 109.0, 95.0, 94.0],
        "close": [100.5, 114.0, 110.0, 95.0, 95.0],
        "volume": [1.0] * 5}, index=idx)
    sig = pd.DataFrame({"signal": [1], "entry": [100.0], "sl": [90.0],
                        "tp1": [130.0], "tp2": [150.0], "atr": [5.0]},
                       index=[idx[0]])
    tr_t, m_t = backtest(df, sig, cost=0.0, trail_start_r=0.5, trail_atr=1.0)
    tr_p, m_p = backtest(df, sig, cost=0.0)
    assert m_t["trades"] == 1 and m_p["trades"] == 1
    assert tr_t.iloc[0]["R"] == pytest.approx(1.0), "exit di stop ter-trail 110"
    assert tr_p.iloc[0]["R"] == pytest.approx(-0.5), "time stop di open 95"
    assert tr_t.iloc[0]["R"] > tr_p.iloc[0]["R"], "trailing menyelamatkan trade"


# ----------------------------- PaperSim -------------------------------------
def test_papersim_daily_stop_gate():
    sim = PaperSim(path=None, daily_stop_r=0.5)
    sim.persist_enabled = False
    sim.trades = [{"time": "2026-01-02 10:00:00", "r": -1.0}]
    sim.queue_signal(pd.Timestamp("2026-01-02 12:00:00"), 1, 100, 95, 110, 120)
    assert sim.pending is None, "daily stop harus memblok sinyal"
    sim.queue_signal(pd.Timestamp("2026-01-03 12:00:00"), 1, 100, 95, 110, 120)
    assert sim.pending is not None, "hari baru = gate reset"


def test_papersim_daily_max_trades_gate():
    sim = PaperSim(path=None, daily_max_trades=1)
    sim.persist_enabled = False
    sim.trades = [{"time": "2026-01-02 10:00:00", "r": 0.5}]
    sim.queue_signal(pd.Timestamp("2026-01-02 12:00:00"), 1, 100, 95, 110, 120)
    assert sim.pending is None
