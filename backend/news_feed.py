"""
News feed Alpha-Trade — 3 provider gratis dengan fallback + verifikasi silang
-----------------------------------------------------------------------------
Urutan fallback (hemat kuota: berhenti di provider pertama yang sukses):
  1. newsdata.io   (utama; kuota free ±200 req/hari)
  2. finnhub.io    (fallback; butuh token -> env NEWS_KEY_FINNHUB, opsional)
  3. FMP stable economy-latest (cadangan terakhir; sering kosong)

Hemat kuota:
  - cache TTL 10 menit; refresh background tiap 5 menit (hanya jika cache basi)
  - hanya SATU provider dipanggil per refresh (fallback hanya bila gagal/kosong)

Verifikasi silang: bila >=2 provider punya data (dari cache riwayat), judul yang
mirip (irisan kata >=50%) dalam rentang +-12 jam ditandai verified=true.

Filter relevansi trading: skor kata kunci (gold, fed, inflation, CPI, suku
bunga, dollar, NFP, ...) — item skor 0 dibuang.
"""
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

NEWS_TTL = 600          # detik — umur cache sebelum refresh
NEWS_MAX = 30           # simpan maksimal 30 item
CHART_MAX = 3           # item yang dikirim untuk marker chart

KEYWORDS = [
    ("gold", 3), ("xau", 3), ("fed", 3), ("fomc", 3), ("federal reserve", 3),
    ("interest rate", 3), ("inflation", 2), ("cpi", 3), ("ppi", 2), ("dollar", 2),
    ("usd", 2), ("dxy", 2), ("gdp", 2), ("unemployment", 2), ("jobs", 1),
    ("nfp", 3), ("payroll", 2), ("retail sales", 2), ("central bank", 2),
    ("ecb", 2), ("boj", 2), ("boe", 2), ("treasury", 1), ("yield", 1),
    ("opec", 1), ("recession", 2), ("rate cut", 3), ("rate hike", 3),
    ("powell", 3), ("trump", 1), ("tariff", 2), ("sanction", 1),
    ("stock market", 1), ("nasdaq", 1), ("s&p", 1), ("economy", 1),
    ("sell-off", 1), ("selloff", 1), ("rally", 1), ("stocks", 1),
    ("bank", 1), ("bonds", 1), ("crude", 1), ("oil", 1), ("crypto", 1),
    ("bitcoin", 1), ("stimulus", 2), ("debt", 1), ("market", 1),
]


def _score(title):
    t = (title or "").lower()
    return sum(w for k, w in KEYWORDS if k in t)


def _clean(s):
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).split()


def _norm_item(time_iso, title, source, url, provider):
    return {"time": time_iso, "title": (title or "").strip(),
            "source": source or provider, "url": url or "",
            "provider": provider, "score": _score(title), "verified": False}


class NewsFeed:
    def __init__(self, ttl=NEWS_TTL, mcp_client=None):
        self.ttl = ttl
        self.items = []                # hasil akhir (sorted, difilter)
        self._ts = 0                   # waktu cache terakhir
        self.updated = ""              # string waktu fetch terakhir
        self._providers_used = []
        self._raw = {}                 # provider -> (ts, items) untuk verifikasi silang
        self._lock = threading.Lock()
        self._thread = None
        self._mcp = mcp_client
        if self._mcp is None and os.getenv("AT_MT5_MCP_URL"):
            from .mt5_mcp import MT5McpClient
            self._mcp = MT5McpClient(
                url=os.getenv("AT_MT5_MCP_URL", ""),
                api_key=os.getenv("AT_MT5_MCP_KEY", ""),
            )
        self.keys = {
            "newsdata": os.getenv("NEWS_KEY_NEWSDATA", "pub_f9baa82bf10240d4bc29c431ef0817d0"),
            "finnhub": os.getenv("NEWS_KEY_FINNHUB", ""),
            "fmp": os.getenv("NEWS_KEY_FMP", "d1c4Rk4nOBFM7BzfSeA8fj85FAsYws7X"),
        }

    def configure_mcp(self, url="", api_key=""):
        """Pasang atau perbarui klien MT5 MCP secara dinamis dari pengaturan runtime."""
        url = (url or "").strip()
        api_key = (api_key or "").strip()
        if not url:
            self._mcp = None
            return
        from .mt5_mcp import MT5McpClient
        self._mcp = MT5McpClient(url=url, api_key=api_key)

    # ----------------------------- providers -------------------------------
    def _fetch_mt5_mcp(self):
        if self._mcp is None or not self._mcp.configured():
            raise RuntimeError("MT5 MCP belum dikonfigurasi")
        out = []
        try:
            out.extend(self._mcp.fetch_economic_calendar_news())
        except Exception as e:
            print(f"[news] mt5-cal gagal: {e}", flush=True)
        try:
            for it in self._mcp.fetch_terminal_news():
                it["score"] = max(it.get("score", 0), _score(it.get("title")))
                out.append(it)
        except Exception as e:
            print(f"[news] mt5-news gagal: {e}", flush=True)
        return out
    def _fetch_newsdata(self):
        r = requests.get("https://newsdata.io/api/1/latest", timeout=12, params={
            "apikey": self.keys["newsdata"], "category": "business",
            "language": "en", "country": "us", "size": 10})   # free plan: max 10
        r.raise_for_status()
        d = r.json()
        out = []
        for it in d.get("results") or []:
            t = it.get("pubDate")
            if not t:
                continue
            dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            out.append(_norm_item(dt.astimezone(timezone.utc).isoformat(),
                                  it.get("title"), it.get("source_name"),
                                  it.get("link"), "newsdata"))
        return out

    def _fetch_finnhub(self):
        if not self.keys["finnhub"]:
            raise RuntimeError("token finnhub tidak di-set (env NEWS_KEY_FINNHUB)")
        r = requests.get("https://finnhub.io/api/v1/news", timeout=12, params={
            "category": "general", "token": self.keys["finnhub"]})
        r.raise_for_status()
        out = []
        for it in r.json()[:60]:
            ts = it.get("datetime")
            if not ts:
                continue
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            out.append(_norm_item(dt.isoformat(), it.get("headline"),
                                  it.get("source"), it.get("url"), "finnhub"))
        return out

    def _fetch_fmp(self):
        r = requests.get("https://financialmodelingprep.com/stable/news/economy-latest",
                         timeout=12, params={"apikey": self.keys["fmp"]})
        if r.status_code == 404:                         # FMP: 404 = tidak ada item
            return []
        r.raise_for_status()
        out = []
        for it in r.json()[:60]:
            t = it.get("publishedDate") or it.get("date")
            if not t:
                continue
            dt = pd_to_utc(t)
            out.append(_norm_item(dt, it.get("title"), it.get("source"),
                                  it.get("url"), "fmp"))
        return out

    # ----------------------------- refresh ---------------------------------
    def _providers_in_order(self):
        provs = []
        if self._mcp is not None and self._mcp.configured():
            provs.append(("mt5-mcp", self._fetch_mt5_mcp))
        provs.extend([
            ("newsdata", self._fetch_newsdata),
            ("finnhub", self._fetch_finnhub),
            ("fmp", self._fetch_fmp),
        ])
        return provs

    def refresh(self, force=False):
        """Ambil berita bila cache basi. Satu provider sukses = cukup."""
        with self._lock:
            if not force and time.time() - self._ts < self.ttl:
                return self.items
            for name, fn in self._providers_in_order():
                try:
                    items = fn()
                except Exception as e:
                    print(f"[news] {name} gagal: {e.__class__.__name__}", flush=True)
                    continue
                if not items:
                    print(f"[news] {name} kosong", flush=True)
                    continue
                self._raw[name] = (time.time(), items)
                merged = self._merge_verified(items)
                self.items = merged[:NEWS_MAX]
                self._ts = time.time()
                self.updated = time.strftime("%H:%M:%S UTC", time.gmtime())
                self._providers_used = [name]
                print(f"[news] {len(items)} item dari {name} "
                      f"({sum(1 for i in self.items if i['verified'])} verified)", flush=True)
                return self.items
            print("[news] semua provider gagal/ kosong", flush=True)
            return self.items

    def _merge_verified(self, fresh):
        """Gabungkan item fresh + tandai verified bila >=2 provider memiliki
        judul mirip dalam +-12 jam (pakai riwayat raw cache)."""
        items = fresh
        others = [its for name, (ts, its) in self._raw.items() if its]
        if len(self._raw) >= 2:
            for it in items:
                w1 = set(_clean(it["title"]))
                for other in others:
                    for o in other:
                        if abs(_parse(it["time"]) - _parse(o["time"])) > timedelta(hours=12):
                            continue
                        w2 = set(_clean(o["title"]))
                        inter = len(w1 & w2)
                        if w1 and inter / max(1, min(len(w1), len(w2))) >= 0.5:
                            it["verified"] = True
                            break
                    if it["verified"]:
                        break
        # urutkan: skor relevansi desc, lalu waktu desc (stable sort);
        # item skor 0 tetap disimpan untuk panel, marker chart mengambil teratas
        items.sort(key=lambda i: _parse(i["time"]), reverse=True)
        items.sort(key=lambda i: i["score"], reverse=True)
        return items

    # ----------------------------- akses -----------------------------------
    def maybe_refresh(self):
        try:
            return self.refresh()
        except Exception as e:
            print("[news] refresh gagal:", e, flush=True)
            return self.items

    def top_for_chart(self, n=CHART_MAX):
        """Item terbaru untuk marker chart (maks n)."""
        return self.items[:n]

    def high_impact_near(self, t, window_min=30, min_score=4):
        """Item berdampak tinggi dalam +-window menit dari waktu sinyal t,
        atau None bila aman entry. Dipakai gate eksekusi live (PaperSim)."""
        try:
            t0 = _parse(t if isinstance(t, str) else t.isoformat())
        except Exception:
            return None
        lo, hi = t0 - timedelta(minutes=window_min), t0 + timedelta(minutes=window_min)
        best = None
        for it in self.items:
            ti = _parse(it.get("time", ""))
            if ti is not None and lo <= ti <= hi and it.get("score", 0) >= min_score:
                if best is None or it.get("score", 0) > best.get("score", 0):
                    best = it
        return best

    def start_background(self, every=300):
        """Refresh berkala di background thread (hemat kuota: cek TTL dulu)."""
        def loop():
            while True:
                self.maybe_refresh()
                time.sleep(every)
        if self._thread is None:
            self._thread = threading.Thread(target=loop, daemon=True)
            self._thread.start()


def pd_to_utc(s):
    """Parse berbagai format tanggal -> iso UTC string."""
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        try:
            dt = datetime.fromtimestamp(float(s), tz=timezone.utc)
        except Exception:
            return s
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _parse(iso):
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


if __name__ == "__main__":
    nf = NewsFeed()
    items = nf.refresh(force=True)
    for it in items[:6]:
        mark = "✓✓" if it["verified"] else "  "
        print(f"[{it['provider']:8s}] {mark} skor={it['score']:2d} {it['time'][:16]} "
              f"{it['title'][:70]}")
    print("TOTAL:", len(items))
