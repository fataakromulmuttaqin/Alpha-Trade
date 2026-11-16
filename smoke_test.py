"""Smoke test backend: analytics.build_snapshot dengan data sintetis."""
import numpy as np
import pandas as pd

from analytics import build_snapshot, events_list, heatmap_row, zones, volume_flow
from confluence_engine import Config, generate_signals

rng = np.random.default_rng(7)
n = 1200
idx = pd.date_range("2025-11-01", periods=n, freq="5min")
price = 3850 + np.cumsum(rng.normal(0.03, 1.4, n))
o = price + rng.normal(0, 0.3, n)
cl = price + rng.normal(0, 0.3, n)
hi = np.maximum(o, cl) + np.abs(rng.normal(0, 0.9, n))
lo = np.minimum(o, cl) - np.abs(rng.normal(0, 0.9, n))
vol = rng.integers(100, 1000, n).astype(float)
df = pd.DataFrame({"open": o, "high": hi, "low": lo, "close": cl, "volume": vol}, idx)

hdf = df.resample("4h").agg({"open": "first", "high": "max", "low": "min",
                             "close": "last", "volume": "sum"}).dropna()

print("== volume_flow:", volume_flow(df))
print("== zones:", zones(df, 2.0))
print("== events (3 terakhir):", events_list(df)[-3:])
print("== heatmap_row:", heatmap_row(df))

sig = generate_signals(df, Config(vp_prox_atr=3, atr_pct_low=0, atr_pct_high=1), htf_df=hdf)
print("== sinyal (longgar):", len(sig))
snap = build_snapshot({"5m": df, "4h": hdf}, Config(), [], "XAUUSD")
assert snap, "snapshot None"
print("== stats:", snap["stats"])
print("== confluence:", {k: snap["confluence"][k] for k in ("confidence", "action")})
print("== golden:", snap["golden"])
print("== setup:", snap["setup"])
print("== heatmap:", snap["heatmap"])
print("SMOKE OK")

# --- GODMODE layer ---
print("== market_condition:", __import__("godmode_engine").market_condition(df))
print("== trend_lamps:", __import__("godmode_engine").trend_lamps(df, hdf))
print("== zones_multi:", __import__("godmode_engine").multi_zones(df, 2.0))
print("== two_stage:", snap["two_stage"])
print("== session:", snap["session"])
print("== setup_label:", snap["setup_label"])
print("== ai_reasoning:", snap["ai_reasoning"])
print("== forecast:", snap["forecast"])
print("== confluence checks:", [(c["label"], c["val"], c["pts"]) for c in snap["confluence"]["checks"]], "+bonus", snap["confluence"]["bonus"])
print("== events GODMODE:", [e["tag"] for e in snap["events"]])
print("GODMODE LAYER OK")
