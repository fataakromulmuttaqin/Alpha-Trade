"""Dump indikator antara utk pembanding Rust (indikator pertama yang menyimpang)."""
import json, os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.tradoor_feed import load_market
from backend.confluence_engine import (Config, generate_signals, rsi as rsi_fn, macd as macd_fn,
    atr as atr_fn, volume_profile_levels, adx_wilder, htf_trend)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def to_tf(df1m, rule):
    return df1m.resample(rule).agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"}).dropna()

df1m = load_market(os.path.join(ROOT, "tradoor-data"), "binance:PAXGUSDT", "1m", bars=10**9)
df1h = to_tf(df1m, "60min"); df4h = to_tf(df1m, "4h")
d26 = df1h[df1h.index >= "2026-06-01"]; h26 = df4h[df4h.index >= "2026-06-01"]
cfg = Config(htf="4h", min_confluence=2, confirm_window=3, cooldown=10,
             session_utc=(7,21), adx_min=15.0,
             round_gate=True, round_prox_atr=0.25, round_entry_gate=True)

c = d26["close"]
rsi_s = rsi_fn(c, cfg.rsi_len)
line, sig_, hist = macd_fn(c, cfg.macd_fast, cfg.macd_slow, cfg.macd_sig)
atr_s = atr_fn(d26, cfg.atr_len)
poc, val, vah = volume_profile_levels(d26, cfg)
rank = atr_s.rolling(cfg.atr_rank_window, min_periods=100).rank(pct=True)
adx = adx_wilder(d26, 14)
trend = htf_trend(d26, cfg, h26)

out = {
  "times": [str(t) for t in d26.index],
  "rsi": [None if np.isnan(x) else x for x in rsi_s],
  "macd_line": [None if np.isnan(x) else x for x in line],
  "macd_sig": [None if np.isnan(x) else x for x in sig_],
  "hist": [None if np.isnan(x) else x for x in hist],
  "atr": [None if np.isnan(x) else x for x in atr_s],
  "poc": [None if np.isnan(x) else x for x in poc],
  "val": [None if np.isnan(x) else x for x in val],
  "vah": [None if np.isnan(x) else x for x in vah],
  "rank": [None if (isinstance(x,float) and np.isnan(x)) else x for x in rank],
  "adx": [None if np.isnan(x) else x for x in adx],
  "trend": [int(x) for x in trend],
}
os.makedirs("native/tests", exist_ok=True)
with open("native/tests/intermediates.json", "w") as f: json.dump(out, f)
print("dump:", len(d26), "bar")

# ---- kandidat gate (long_ok/short_ok) sebelum cooldown
import json as _json
g_sig = generate_signals(d26, cfg, htf_df=h26)
# replikasi kandidat: jalankan bagian gate — termudah lewat monkey: gunakan
# langkah dari engine: hitung manual dengan helper yang sama
from backend.confluence_engine import (ema as _ema, atr as _atr, rsi as _rsi,
    macd as _macd, volume_profile_levels as _vp, divergence as _div,
    price_action as _pa, rolling_rank_pct if False else None)  # noqa
