"""
Eksekusi order Binance TESTNET (spot / futures) untuk sinyal confluence.
-------------------------------------------------------------------------
EXECUTION_MODE env:
  off      : tidak ada order apa pun (default — hanya sinyal)
  paper    : simulasi lokal, order dicetak & dicatat tanpa HTTP
  testnet  : kirim MARKET order ke testnet Binance

Tidak ada jalur mainnet di modul ini. API key testnet dibuat di:
  spot    : https://testnet.binance.vision   (login GitHub)
  futures : https://testnet.binancefuture.com
Env: BINANCE_TESTNET_API_KEY / BINANCE_TESTNET_API_SECRET
"""
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlencode

import requests

from .confluence_engine import position_size

BASE = {"spot": "https://testnet.binance.vision",
        "futures": "https://testnet.binancefuture.com"}
ORDER_PATH = {"spot": "/api/v3/order", "futures": "/fapi/v1/order"}
ACCOUNT_PATH = {"spot": "/api/v3/account", "futures": "/fapi/v2/account"}
TICKER_PATH = {"spot": "/api/v3/ticker/price", "futures": "/fapi/v1/ticker/price"}
EXINFO_PATH = {"spot": "/api/v3/exchangeInfo", "futures": "/fapi/v1/exchangeInfo"}

SIDE = {1: "BUY", -1: "SELL"}


class PaperOrder:
    """Catatan order simulasi (mode paper)."""

    def __init__(self):
        self.fills = []

    def record(self, d):
        self.fills.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), **d})
        print("[paper] " + json.dumps(d, default=str), flush=True)


class TestnetExecutor:
    def __init__(self, market="spot", mode=None, api_key=None, api_secret=None,
                 risk_pct=1.0):
        self.market = market if market in BASE else "spot"
        env_mode = (os.getenv("EXECUTION_MODE") or "off").lower()
        self.mode = (mode or env_mode).lower()
        if self.mode not in ("off", "paper", "testnet"):
            print(f"[exec] EXECUTION_MODE '{self.mode}' tidak dikenal -> off")
            self.mode = "off"
        self.api_key = api_key or os.getenv("BINANCE_TESTNET_API_KEY")
        self.api_secret = api_secret or os.getenv("BINANCE_TESTNET_API_SECRET")
        self.risk_pct = risk_pct
        self.paper = PaperOrder()
        self._step = None                                   # LOT_SIZE stepSize
        if self.mode == "testnet" and not (self.api_key and self.api_secret):
            print("[exec] testnet aktif tapi API key/secret kosong -> paper")
            self.mode = "paper"

    # -- infra
    def _sign(self, params):
        q = urlencode(params)
        sig = hmac.new(self.api_secret.encode(), q.encode(), hashlib.sha256).hexdigest()
        return f"{q}&signature={sig}"

    def _req(self, method, path, params=None):
        url = BASE[self.market] + path
        params = dict(params or {})
        params["timestamp"] = int(time.time() * 1000)
        params.setdefault("recvWindow", 5000)
        headers = {"X-MBX-APIKEY": self.api_key}
        if method == "GET":
            r = requests.get(url, params=self._sign(params), headers=headers, timeout=10)
        else:
            r = requests.post(url, params=self._sign(params), headers=headers, timeout=10)
        if r.status_code >= 400:
            raise RuntimeError(f"{self.market} testnet {path} {r.status_code}: {r.text[:200]}")
        return r.json()

    def _round_step(self, symbol, qty):
        """Bulatkan qty ke stepSize LOT_SIZE (cache per proses)."""
        if self._step is None:
            try:
                info = requests.get(BASE[self.market] + EXINFO_PATH[self.market],
                                    params={"symbol": symbol}, timeout=10).json()
                f = info["symbols"][0]["filters"]
                lot = next(x for x in f if x["filterType"] in ("LOT_SIZE", "MARKET_LOT_SIZE"))
                self._step = float(lot["stepSize"])
            except Exception:
                self._step = 0.001
        step = self._step or 0.001
        import math
        return math.floor(qty / step) * step

    # -- akun
    def balances(self):
        acc = self._req("GET", ACCOUNT_PATH[self.market])
        if self.market == "spot":
            return {b["asset"]: float(b["free"]) for b in acc["balances"]
                    if float(b["free"]) > 0}
        return {b["asset"]: float(b["availableBalance"]) for b in acc["assets"]
                if float(b["availableBalance"]) > 0}

    def equity(self, quote="USDT"):
        try:
            b = self.balances()
            return b.get(quote, 0.0)
        except Exception as e:
            print("[exec] gagal baca saldo:", e)
            return 0.0

    # -- order
    def market_order(self, symbol, side, quantity):
        qty = self._round_step(symbol, quantity)
        if qty <= 0:
            print("[exec] qty 0 setelah pembulatan step — order dilewati")
            return None
        if self.mode == "paper":
            rec = {"mode": "paper", "symbol": symbol, "side": side,
                   "type": "MARKET", "qty": qty}
            self.paper.record(rec)
            return rec
        return self._req("POST", ORDER_PATH[self.market],
                         {"symbol": symbol.upper(), "side": side,
                          "type": "MARKET", "quantity": qty})

    def execute_signal(self, symbol, row, entry_price=None):
        """Eksekusi sinyal engine: LONG->BUY, SHORT->SELL (futures testnet bisa
        short murni; spot testnet hanya menjual saldo yang ada)."""
        d = int(row["signal"])
        entry = float(entry_price if entry_price is not None else row["entry"])
        sl = float(row["sl"])
        eq = self.equity() or 10_000.0
        units = position_size(eq, entry, sl, risk_pct=self.risk_pct)
        if units <= 0:
            print("[exec] ukuran posisi 0 — dilewati")
            return None
        print(f"[exec] mode={self.mode} {symbol} {SIDE[d]} qty={units:.4f} "
              f"(equity {eq:.2f}, risk {self.risk_pct}%)", flush=True)
        try:
            return self.market_order(symbol, SIDE[d], units)
        except Exception as e:
            print("[exec] order gagal:", e)
            return None


if __name__ == "__main__":
    # Cek koneksi testnet + saldo (tanpa order).
    ex = TestnetExecutor(market="spot")
    print("mode:", ex.mode)
    if ex.mode == "testnet":
        print("saldo:", ex.balances())
    else:
        print("set EXECUTION_MODE=testnet + API key untuk cek saldo.")
