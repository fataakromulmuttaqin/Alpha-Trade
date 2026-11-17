import io
import numpy as np, pandas as pd
from backend.tradoor_feed import write_tdc1, read_tdc1
rng = np.random.default_rng(5)
n = 5
idx = pd.date_range("2025-10-01", periods=n, freq="1min")
p = 3850 + np.cumsum(rng.normal(0, 0.5, n))
o = p + rng.normal(0, 0.1, n); c = p + rng.normal(0, 0.1, n)
df = pd.DataFrame({"open": o, "close": c,
                   "high": np.maximum(o, c) + abs(rng.normal(0, 0.3, n)),
                   "low": np.minimum(o, c) - abs(rng.normal(0, 0.3, n)),
                   "volume": rng.integers(1, 5000, n).astype(float)}, index=idx)
blob = write_tdc1(df, 1)
iv, back = read_tdc1(io.BytesIO(blob))
print("df:"); print(df.round(2))
print("back:"); print(back.round(2))
