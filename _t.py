import time
from binance_feed import _bybit_klines_df, LiveFeed, BYBIT_INTERVAL

# REST Bybit: 60 candle 5m PAXGUSDT
end = int(time.time() * 1000)
df = _bybit_klines_df("PAXGUSDT", "5m", end - 60 * 300000, end)
print("bybit REST rows:", len(df), "| last close:", df["close"].iloc[-1] if len(df) else None)

# parse WS: pesan bybit + binance
k_bin = LiveFeed._parse_ws({"e": "kline", "k": {"t": 1, "x": True}})
k_byb = LiveFeed._parse_ws({"topic": "kline.5.PAXGUSDT",
                            "data": [{"start": 1, "end": 2, "open": "3843", "high": "3844",
                                      "low": "3842", "close": "3843.5", "volume": "12",
                                      "confirm": True}]})
print("parse binance:", len(k_bin), "| parse bybit:", k_byb)
