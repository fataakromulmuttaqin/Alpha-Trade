"""
Tradoor data bridge — baca candle dari data tradoor (TDC1) untuk Alpha-Trade.
-----------------------------------------------------------------------------
Tradoor (https://github.com/tradoor-terminal/tradoor) menyimpan history per
market sebagai partisi gzip TDC1 (spesifikasi: docs/format.md di repo tradoor):

  data/{venue}/{SYMBOL}/manifest.json
  data/{venue}/{SYMBOL}/{1m|1h|1d}/{YYYY|YYYY-MM|YYYY-MM-DD}.tdc.gz

Format partisi (little-endian, setelah gzip):
  magic "TDC1" | u8 interval | u8 price_scale | u8 volume_scale |
  u8 quote_scale | u8 flags (1 quote, 2 taker, 4 trades) | u32 count |
  i64 first_open | uvarint steps x(count-1) |
  ivarint open x count (delta dgn close[i-1]) |
  ivarint high x count (delta dari max(open,close)) |
  ivarint low x count (delta dari min(open,close)) |
  ivarint close x count (delta dari open) |
  uvarint volume x count | [uvarint quote | uvarint taker | uvarint trades]

uvarint = LEB128; ivarint = LEB128 zigzag. Harga integer x 10^-scale.
Sumber data bisa direktori lokal (self-host) atau URL server tradoor
(server menyajikan /data/... sebagai file statis publik).
"""
import asyncio
import gzip
import io
import json
import math
import os
from urllib.request import urlopen

import pandas as pd

INTERVAL_MIN = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}
FLAGS_QUOTE, FLAGS_TAKER, FLAGS_TRADES = 1, 2, 4
COLS = ["open", "high", "low", "close", "volume", "close_time"]


# ----------------------------- primitives -----------------------------------
def _read_uvarint(buf, pos):
    result, shift = 0, 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def _read_ivarint(buf, pos):
    u, pos = _read_uvarint(buf, pos)
    # zigzag decode
    return (u >> 1) ^ -(u & 1), pos


def _read_column_u(buf, pos, count):
    out = []
    for _ in range(count):
        v, pos = _read_uvarint(buf, pos)
        out.append(v)
    return out, pos


def _read_column_i(buf, pos, count):
    out = []
    for _ in range(count):
        v, pos = _read_ivarint(buf, pos)
        out.append(v)
    return out, pos


# ----------------------------- partisi ---------------------------------------
def read_tdc1(path_or_bytes):
    """Baca satu partisi TDC1 (path file .tdc.gz, bytes, atau BytesIO ter-gzip).
    Return (interval_menit, DataFrame[open, high, low, close, volume, ...])."""
    if isinstance(path_or_bytes, (bytes, bytearray)):
        raw = gzip.decompress(bytes(path_or_bytes))
    elif hasattr(path_or_bytes, "read"):                 # file object / BytesIO
        raw = gzip.decompress(path_or_bytes.read())
    else:
        with open(path_or_bytes, "rb") as f:
            raw = gzip.decompress(f.read())
    if raw[:4] != b"TDC1":
        raise ValueError("bukan partisi TDC1")
    interval = raw[4]
    price_scale = 10 ** raw[5]
    vol_scale = 10 ** raw[6]
    flags = raw[8]
    count = int.from_bytes(raw[9:13], "little")
    first_open = int.from_bytes(raw[13:21], "little", signed=True)
    pos = 21

    steps, pos = _read_column_u(raw, pos, max(0, count - 1))
    opens_rel, pos = _read_column_i(raw, pos, count)
    highs, pos = _read_column_i(raw, pos, count)
    lows, pos = _read_column_i(raw, pos, count)
    closes, pos = _read_column_i(raw, pos, count)
    volumes, pos = _read_column_u(raw, pos, count)
    quote = taker = trades = None
    if flags & FLAGS_QUOTE:
        quote, pos = _read_column_u(raw, pos, count)
    if flags & FLAGS_TAKER:
        taker, pos = _read_column_u(raw, pos, count)
    if flags & FLAGS_TRADES:
        trades, pos = _read_column_u(raw, pos, count)

    # susun waktu: open[i] = open[i-1] + step[i-1]
    times = [first_open]
    for s in steps:
        times.append(times[-1] + s)
    # susun harga dari delta kolom
    o_prev = c_prev = None
    o_list, h_list, l_list, c_list = [], [], [], []
    for i in range(count):
        o = opens_rel[i] if o_prev is None else c_prev + opens_rel[i]
        c = o + closes[i]
        h = max(o, c) + highs[i]
        low = min(o, c) - lows[i]
        o_list.append(o)
        h_list.append(h)
        l_list.append(low)
        c_list.append(c)
        o_prev, c_prev = o, c
    out = pd.DataFrame({
        "open": [v / price_scale for v in o_list],
        "high": [v / price_scale for v in h_list],
        "low": [v / price_scale for v in l_list],
        "close": [v / price_scale for v in c_list],
        "volume": [v / vol_scale for v in volumes],
    }, index=pd.DatetimeIndex(pd.to_datetime(times, unit="ms", utc=True), name="time"))
    if quote is not None:
        out["quote"] = [v / vol_scale for v in quote]
    if taker is not None:
        out["taker_buy"] = [v / vol_scale for v in taker]
    if trades is not None:
        out["trades"] = trades
    out["close_time"] = (out.index.astype("int64") // 10**6) + interval * 60_000 - 1
    return interval, out


def write_tdc1(df, interval_min, price_scale=100, vol_scale=100, extra=False):
    """Encoder TDC1 minimal — dipakai untuk uji round-trip reader
    (dan untuk menulis data sendiri bila perlu). df = kolom standar.
    price_scale/vol_scale = pengali desimal (100 -> byte eksponen 2)."""
    buf = bytearray()
    buf += b"TDC1"
    buf.append(interval_min)
    buf.append(int(round(math.log10(price_scale))))
    buf.append(int(round(math.log10(vol_scale))))
    buf.append(0)                                    # quote/taker/trades opsional
    buf.append(0)
    buf += len(df).to_bytes(4, "little")
    first = int(df.index[0].timestamp() * 1000)
    buf += first.to_bytes(8, "little", signed=True)
    times = [int(t.timestamp() * 1000) for t in df.index]
    for a, b in zip(times, times[1:]):               # steps
        _write_uvarint(buf, b - a)
    # kolom COLUMNAR: semua open, lalu high, low, close, volume
    os_ = [int(round(v * price_scale)) for v in df["open"]]
    cs_ = [int(round(v * price_scale)) for v in df["close"]]
    hs_ = [int(round(v * price_scale)) for v in df["high"]]
    ls_ = [int(round(v * price_scale)) for v in df["low"]]
    vs_ = [int(round(v * vol_scale)) for v in df["volume"]]
    for i, o in enumerate(os_):                      # open[i] - close[i-1]
        _write_ivarint(buf, o if i == 0 else o - cs_[i - 1])
    for i, h in enumerate(hs_):                      # high - max(open, close)
        _write_ivarint(buf, h - max(os_[i], cs_[i]))
    for i, low in enumerate(ls_):                    # low - min(open, close)
        _write_ivarint(buf, min(os_[i], cs_[i]) - low)
    for i, c in enumerate(cs_):                      # close - open
        _write_ivarint(buf, c - os_[i])
    for v in vs_:                                    # volume
        _write_uvarint(buf, v)
    return gzip.compress(bytes(buf))


def _write_uvarint(buf, v):
    while True:
        b = v & 0x7F
        v >>= 7
        buf.append(b | (0x80 if v else 0))
        if not v:
            return


def _write_ivarint(buf, v):
    _write_uvarint(buf, (v << 1) ^ (v >> 63) if v < 0 else (v << 1))


# ----------------------------- market loader ---------------------------------
def _fetch_bytes(src):
    """src = path lokal atau URL http(s)."""
    if isinstance(src, bytes):
        return src
    if src.startswith("http://") or src.startswith("https://"):
        with urlopen(src, timeout=15) as r:
            return r.read()
    with open(src, "rb") as f:
        return f.read()


def load_market(source, market, interval="15m", bars=2000):
    """Muat candle satu market dari data tradoor.
    source  : direktori data tradoor (lokal) ATAU base URL server tradoor
              (yang menyajikan /data/...), mis. 'http://localhost:8080/data'
    market  : 'binance-perp:BTCUSDT'
    Tradoor hanya menyimpan 1m/1h/1d — TF lain di-resample dari 1m/1h.
    Return DataFrame kolom standar Alpha-Trade."""
    base = source.rstrip("/")
    is_http = base.startswith("http://") or base.startswith("https://")
    if base.endswith("/data") or "/data/" in base:
        data_base = base
    else:
        data_base = base + "/data"
    # ':' valid di Linux (layout tradoor); di Windows disk lokal diganti '_'
    mkt_dir = market if is_http or os.name != "nt" else market.replace(":", "_")
    manifest = json.loads(_fetch_bytes(f"{data_base}/{mkt_dir}/manifest.json").decode())
    bases = manifest.get("bases", {})
    src_iv = interval if bases.get(interval) else None
    if src_iv is None:                                   # TF turunan -> dari 1m/1h
        for cand in ("1m", "1h", "1d"):
            if bases.get(cand):
                src_iv = cand
                break
        if src_iv is None:
            return pd.DataFrame(columns=COLS)
    parts = sorted(bases.get(src_iv) or [], key=lambda p: str(p.get("period")))
    frames = []
    for p in parts:
        url = f"{data_base}/{mkt_dir}/{src_iv}/{p['period']}.tdc.gz"
        try:
            _, df = read_tdc1(_fetch_bytes(url))
        except (FileNotFoundError, OSError):
            continue                                    # partisi hilang -> lewati
        frames.append(df)
        if sum(len(f) for f in frames) >= bars * max(1, INTERVAL_MIN[interval] // INTERVAL_MIN.get(src_iv, 1)):
            break
    if not frames:
        return pd.DataFrame(columns=COLS)
    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    if src_iv != interval:                              # resample ke TF minta
        agg = {"open": "first", "high": "max", "low": "min", "close": "last",
               "volume": "sum"}
        extra = [c for c in ("quote", "taker_buy", "trades") if c in df.columns]
        for c in extra:
            agg[c] = "sum"
        df = df.resample(f"{INTERVAL_MIN[interval]}min").agg(agg).dropna(subset=["close"])
        df["close_time"] = (df.index.astype("int64") // 10**6) + INTERVAL_MIN[interval] * 60_000 - 1
    return df.tail(bars)[[c for c in COLS if c in df.columns] +
                         [c for c in df.columns if c not in COLS]]


# ----------------------------- feed untuk dashboard ---------------------------
class TradoorFeed:
    """Provider data dari tradoor self-host (file lokal atau HTTP).
    Interface mirip LiveFeed: seed() lalu run(state) polling candle baru."""

    def __init__(self, source, market, interval="15m", buffer_bars=1500,
                 on_candle=None, poll_sec=30):
        self.source = source
        self.market = market
        self.interval = interval
        self.buffer_bars = buffer_bars
        self.on_candle = on_candle
        self.poll_sec = poll_sec
        self.buf = pd.DataFrame()

    def seed(self):
        self.buf = load_market(self.source, self.market, self.interval, self.buffer_bars)
        print(f"[tradoor] seed {len(self.buf)} candle {self.market} {self.interval}",
              flush=True)
        return self.buf

    async def run(self, state, emit=True):
        self._push(state)
        while True:
            await asyncio.sleep(self.poll_sec)
            try:
                df = await asyncio.get_running_loop().run_in_executor(
                    None, self._load_latest)
                self.buf = df
                self._push(state)
            except Exception as e:
                print(f"[tradoor] poll gagal ({self.interval}):", e, flush=True)

    def _load_latest(self):
        return load_market(self.source, self.market, self.interval, self.buffer_bars)

    def _push(self, state):
        if self.on_candle and len(self.buf):
            self.on_candle(self.interval, self.buf)


if __name__ == "__main__":
    # uji round-trip: tulis partisi sintetis -> baca -> bandingkan
    import numpy as np
    rng = np.random.default_rng(5)
    n = 500
    idx = pd.date_range("2025-10-01", periods=n, freq="1min", tz="UTC")
    p = 3850 + np.cumsum(rng.normal(0, 0.5, n))
    o = p + rng.normal(0, 0.1, n)
    c = p + rng.normal(0, 0.1, n)
    df = pd.DataFrame({
        "open": o, "close": c,
        "high": np.maximum(o, c) + abs(rng.normal(0, 0.3, n)),
        "low": np.minimum(o, c) - abs(rng.normal(0, 0.3, n)),
        "volume": rng.integers(1, 5000, n).astype(float),
    }, index=idx)
    blob = write_tdc1(df, 1)
    iv, back = read_tdc1(io.BytesIO(blob))
    same_o = np.allclose(back["open"], df["open"], atol=0.01)
    same_c = np.allclose(back["close"], df["close"], atol=0.01)
    same_h = np.allclose(back["high"], df["high"], atol=0.01)
    same_l = np.allclose(back["low"], df["low"], atol=0.01)
    same_v = np.allclose(back["volume"], df["volume"], atol=0.01)
    same_t = (back.index == df.index).all()
    print(f"round-trip: o={same_o} h={same_h} l={same_l} c={same_c} v={same_v} t={same_t}")
    print("UKURAN:", len(blob), "bytes gzip utk", n, "bar")
    assert same_o and same_c and same_h and same_l and same_v and same_t
    print("TRADOOR READER OK")
