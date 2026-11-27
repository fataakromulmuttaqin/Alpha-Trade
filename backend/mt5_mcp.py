"""
MetaTrader 5 Internal MCP Client & Adapter (MT5 Build 6060+)
------------------------------------------------------------
Menghubungkan Alpha-Trade ke Internal MCP Server bawaan terminal MT5
(Tools -> Options -> MCP, default http://127.0.0.1:22346/mcp) melalui
protokol MCP Streamable HTTP (JSON-RPC + Bearer token).

Dua komponen utama:
  1. MT5McpClient  : klien HTTP JSON-RPC thread-safe untuk memanggil 107 tool
                     MT5 MCP (kalender ekonomi, berita, quote, histori OHLCV,
                     info akun/posisi, serta eksekusi/modifikasi/penutupan order).
  2. MT5McpAdapter : adaptor yang meniru antarmuka library `MetaTrader5` Python
                     sehingga `MT5MultiFeed` & `MT5Executor` bisa memakai
                     transport MCP HTTP secara transparan (tanpa bergantung IPC
                     Windows Python-only).
"""
from datetime import datetime, timedelta, timezone
import json
import threading
import time
from types import SimpleNamespace

import numpy as np
import requests

from .binance_feed import INTERVAL_MS

TF_TO_MCP_PERIOD = {
    1: ("M1", 60),
    3: ("M3", 180),
    5: ("M5", 300),
    15: ("M15", 900),
    30: ("M30", 1800),
    16385: ("H1", 3600),
    16386: ("H2", 7200),
    16388: ("H4", 14400),
    16408: ("D1", 86400),
}

IMPORTANCE_SCORE = {
    "high importance": 6,
    "moderate importance": 3,
    "low importance": 1,
}


def _parse_iso_dt(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


class MT5McpClient:
    """Klien Streamable HTTP MCP untuk MetaTrader 5 Terminal MCP Server."""

    def __init__(self, url="", api_key="", timeout=12.0, session=None):
        self.url = (url or "").strip()
        self.api_key = (api_key or "").strip()
        self.timeout = float(timeout)
        self._http = session or requests.Session()
        self._lock = threading.RLock()
        self._session_id = None
        self._req_id = 0
        self._usd_events_cache = {}
        self._usd_events_ts = 0.0
        self._server_offset_sec = 0.0
        self._offset_ts = 0.0

    def configured(self):
        return bool(self.url)

    def _headers(self):
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    def _parse_response(self, resp):
        sid = resp.headers.get("Mcp-Session-Id")
        if sid:
            self._session_id = sid
        txt = (resp.text or "").strip()
        if not txt:
            return {}
        if txt.startswith("event:") or "\ndata:" in txt:
            lines = [ln[5:].strip() for ln in txt.splitlines() if ln.startswith("data:")]
            return json.loads(lines[-1]) if lines else {}
        return resp.json()

    def _post(self, payload):
        r = self._http.post(self.url, headers=self._headers(), json=payload, timeout=self.timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"MT5 MCP HTTP {r.status_code}: {(r.text or '')[:200]}")
        return self._parse_response(r)

    def initialize(self):
        """Handshake MCP: initialize -> notifications/initialized."""
        with self._lock:
            if not self.url:
                raise RuntimeError("URL MT5 MCP belum diatur")
            self._session_id = None
            self._req_id += 1
            res = self._post({
                "jsonrpc": "2.0",
                "id": self._req_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "alpha-trade", "version": "5.1"},
                },
            })
            if "error" in res:
                raise RuntimeError(f"MT5 MCP init error: {res['error']}")
            # Notification wajib pada Streamable HTTP sebelum tools/call
            self._http.post(
                self.url,
                headers=self._headers(),
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                timeout=self.timeout,
            )
            return res.get("result", {})

    def call_tool(self, name, arguments=None, _retry=True):
        """Panggil tool MCP dan kembalikan dict JSON hasil parsing content[0].text."""
        with self._lock:
            if not self._session_id:
                self.initialize()
            self._req_id += 1
            try:
                res = self._post({
                    "jsonrpc": "2.0",
                    "id": self._req_id,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments or {}},
                })
            except Exception:
                if _retry:
                    self._session_id = None
                    return self.call_tool(name, arguments=arguments, _retry=False)
                raise

            if "error" in res:
                if _retry:
                    self._session_id = None
                    return self.call_tool(name, arguments=arguments, _retry=False)
                raise RuntimeError(f"MCP error ({name}): {res['error']}")

            result = res.get("result") or {}
            content = result.get("content") or []
            raw_text = content[0].get("text", "") if content else ""
            if result.get("isError"):
                raise RuntimeError(raw_text or f"Tool '{name}' mengembalikan error")
            if not raw_text:
                return {}
            try:
                return json.loads(raw_text)
            except json.JSONDecodeError:
                return {"text": raw_text}

    # ----------------------------- Helper Domain ----------------------------
    def get_server_offset_sec(self):
        """Selisih waktu server broker terhadap UTC (detik), di-cache 10 menit."""
        if time.time() - self._offset_ts < 600 and self._offset_ts > 0:
            return self._server_offset_sec
        try:
            ti = self.call_tool("get_time_information", {})
            utc_dt = _parse_iso_dt(ti.get("utc_time"))
            srv_dt = _parse_iso_dt(ti.get("server_time") or ti.get("trade_server_time"))
            if utc_dt and srv_dt:
                self._server_offset_sec = round((srv_dt - utc_dt).total_seconds() / 900.0) * 900.0
                self._offset_ts = time.time()
        except Exception:
            pass
        return self._server_offset_sec

    def _usd_events_map(self):
        """Peta event_id -> metadata event ekonomi USD (cache 6 jam)."""
        if self._usd_events_cache and (time.time() - self._usd_events_ts) < 21600:
            return self._usd_events_cache
        d = self.call_tool("economic_calendar_list_events_by_currency", {"currency_code": "USD"})
        ev_map = {}
        for ev in d.get("events") or []:
            eid = str(ev.get("id", ""))
            if eid:
                ev_map[eid] = ev
        if ev_map:
            self._usd_events_cache = ev_map
            self._usd_events_ts = time.time()
        return ev_map

    def fetch_economic_calendar_news(self, hours_back=36, hours_ahead=72, limit=400):
        """Ambil jadwal & rilis kalender ekonomi USD dari MT5 MCP dalam format item NewsFeed.
        High importance -> score 6 (otomatis memicu gate news +-30 mnt pada XAUUSD).
        """
        ev_map = self._usd_events_map()
        if not ev_map:
            return []
        offset_sec = self.get_server_offset_sec()
        now_srv = datetime.now(timezone.utc) + timedelta(seconds=offset_sec)
        t_from = (now_srv - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%S")
        t_to = (now_srv + timedelta(hours=hours_ahead)).strftime("%Y-%m-%dT%H:%M:%S")

        res = self.call_tool("economic_calendar_list_values", {
            "datetime_from": t_from,
            "datetime_to": t_to,
            "limit": int(limit),
        })
        out = []
        for val in res.get("values") or []:
            eid = str(val.get("event_id", ""))
            meta = ev_map.get(eid)
            if not meta:
                continue
            imp_raw = str(meta.get("importance") or "").lower()
            score = IMPORTANCE_SCORE.get(imp_raw, 1)
            if score < 3:
                # Fokus pada event Moderate & High importance USD agar panel tidak penuh noise
                continue
            srv_dt = _parse_iso_dt(val.get("time"))
            if srv_dt is None:
                continue
            utc_dt = srv_dt - timedelta(seconds=offset_sec)

            parts = []
            if val.get("actual_value") is not None:
                parts.append(f"Act: {val['actual_value']}")
            if val.get("forecast_value") is not None:
                parts.append(f"Fcst: {val['forecast_value']}")
            if val.get("prev_value") is not None:
                parts.append(f"Prev: {val['prev_value']}")
            suffix = f" ({' | '.join(parts)})" if parts else ""
            imp_tag = "HIGH" if score >= 6 else "MED"
            title = f"[USD {imp_tag}] {meta.get('name', 'Economic Event')}{suffix}"
            out.append({
                "time": utc_dt.isoformat(),
                "title": title,
                "source": "MT5 Calendar",
                "url": meta.get("source_url") or "",
                "provider": "mt5-cal",
                "score": score,
                "verified": True,  # kalender ekonomi resmi MT5
            })
        return out

    def fetch_terminal_news(self, hours_back=48, limit=30):
        """Ambil berita broker dari MT5 MCP `get_news_list` dalam format NewsFeed."""
        now = datetime.now(timezone.utc)
        t_from = (now - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%S")
        t_to = (now + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
        res = self.call_tool("get_news_list", {
            "datetime_from": t_from,
            "datetime_to": t_to,
            "limit": int(limit),
        })
        out = []
        for n in res.get("news") or []:
            dt = _parse_iso_dt(n.get("time") or n.get("datetime"))
            if dt is None:
                continue
            title = (n.get("subject") or n.get("title") or "").strip()
            if not title:
                continue
            out.append({
                "time": dt.isoformat(),
                "title": title,
                "source": n.get("category") or "MT5 News",
                "url": "",
                "provider": "mt5-news",
                "score": 0,  # akan diskor ulang oleh NewsFeed
                "verified": False,
            })
        return out


class MT5McpAdapter:
    """Adaptor drop-in pengganti modul `MetaTrader5` berbasis HTTP MCP.
    Memungkinkan `MT5MultiFeed` dan `MT5Executor` berjalan melalui MT5 MCP HTTP.
    """
    TIMEFRAME_M1 = 1
    TIMEFRAME_M3 = 3
    TIMEFRAME_M5 = 5
    TIMEFRAME_M15 = 15
    TIMEFRAME_M30 = 30
    TIMEFRAME_H1 = 16385
    TIMEFRAME_H2 = 16386
    TIMEFRAME_H4 = 16388
    TIMEFRAME_D1 = 16408

    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_TRADE_MODE_CONTEST = 1
    ACCOUNT_TRADE_MODE_REAL = 2

    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 6
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009

    def __init__(self, url="", api_key="", magic=202605, client=None):
        self.client = client or MT5McpClient(url=url, api_key=api_key)
        self.magic = int(magic)
        self._last_error = (0, "OK")
        self._sym_cache = {}

    def initialize(self, path=None):
        try:
            self.client.initialize()
            self._last_error = (1, "Success (MCP)")
            return True
        except Exception as e:
            self._last_error = (-1, str(e))
            return False

    def shutdown(self):
        self.client._session_id = None

    def last_error(self):
        return self._last_error

    def terminal_info(self):
        try:
            d = self.client.call_tool("terminal_get_info", {})
            t = d.get("terminal") or {}
            conn = t.get("connection") or {}
            mcp_ok = bool(t.get("mcp_trade_allowed", False))
            exp_ok = bool(t.get("experts_trade_allowed", False))
            return SimpleNamespace(
                connected=(conn.get("state") == "synchronized"),
                trade_allowed=mcp_ok or exp_ok,
                tradeapi_disabled=not mcp_ok,
                mcp_trade_allowed=mcp_ok,
                build=int(t.get("build") or 0),
                path=t.get("terminal_path") or "",
            )
        except Exception as e:
            self._last_error = (-1, str(e))
            return None

    def account_info(self):
        try:
            d = self.client.call_tool("get_trading_account_info", {})
            a = d.get("account") or {}
            if not a:
                return None
            acc_type = str(a.get("type") or "").lower()
            tm = 0 if acc_type == "demo" else (1 if acc_type == "contest" else 2)
            return SimpleNamespace(
                login=int(a.get("login") or 0),
                server=str(a.get("server") or ""),
                currency=str(a.get("currency") or "USD"),
                balance=float(a.get("balance") or 0.0),
                equity=float(a.get("equity") or 0.0),
                profit=float(a.get("profit") or 0.0),
                margin_free=float(a.get("margin_free") or 0.0),
                leverage=int(a.get("leverage") or 0),
                trade_mode=tm,
                trade_allowed=not bool(a.get("read_only", False)),
            )
        except Exception as e:
            self._last_error = (-1, str(e))
            return None

    def _to_sym_ns(self, s):
        name = s.get("symbol") or s.get("name") or ""
        point = float(s.get("point") or 0.01)
        tick_size = float(s.get("tick_size") or point or 0.01)
        contract_size = float(s.get("contract_size") or 100.0)
        tick_val = float(s.get("tick_value") or 0.0)
        # MT5 MCP kadang mengembalikan tick_value=0 utk CFD USD -> hitung dari contract_size * tick_size
        if tick_val <= 0 and str(s.get("currency_profit") or "").upper() == "USD":
            tick_val = contract_size * tick_size
        ns = SimpleNamespace(
            name=name,
            visible=bool(s.get("selected", True)),
            point=point,
            digits=int(s.get("digits") or 2),
            trade_tick_size=tick_size,
            trade_tick_value=tick_val or 1.0,
            trade_tick_value_loss=tick_val or 1.0,
            volume_min=float(s.get("volume_min") or 0.01),
            volume_max=float(s.get("volume_max") or 100.0),
            volume_step=float(s.get("volume_step") or 0.01),
            filling_mode=int(s.get("trade_filling_mode") or 2),
            trade_stops_level=int(s.get("trade_stops_level") or 0),
            bid=float(s.get("bid") or 0.0),
            ask=float(s.get("ask") or 0.0),
            update_time=s.get("update_time") or "",
        )
        self._sym_cache[name] = ns
        return ns

    def symbol_info(self, name):
        try:
            d = self.client.call_tool("get_marketwatch_symbols", {"symbol": name, "include_hidden": True})
            syms = d.get("symbols") or []
            if not syms:
                return None
            return self._to_sym_ns(syms[0])
        except Exception:
            return None

    def symbols_get(self):
        try:
            d = self.client.call_tool("get_marketwatch_symbols", {"include_hidden": True, "limit": 1000})
            return [self._to_sym_ns(s) for s in (d.get("symbols") or [])]
        except Exception:
            return []

    def symbol_select(self, name, enable=True):
        try:
            if enable:
                self.client.call_tool("add_marketwatch_symbol", {"symbol": name, "show": True})
            return True
        except Exception:
            return False

    def symbol_info_tick(self, name):
        ns = self.symbol_info(name)
        if ns is None or (ns.bid <= 0 and ns.ask <= 0):
            return None
        dt = _parse_iso_dt(ns.update_time) or datetime.now(timezone.utc)
        return SimpleNamespace(bid=ns.bid, ask=ns.ask, time=int(dt.timestamp()))

    def copy_rates_from_pos(self, symbol, tf, start_pos, count):
        period, step_sec = TF_TO_MCP_PERIOD.get(tf, ("H1", 3600))
        need = int(count) + int(start_pos) + 4
        # Lebarkan rentang waktu (x2.5 + 3 hari) agar akhir pekan/libur tetap tercakup
        span_sec = max(need * step_sec * 2.5, 3 * 86400)
        offset_sec = self.client.get_server_offset_sec()
        now_srv = datetime.now(timezone.utc) + timedelta(seconds=offset_sec)
        t_to = (now_srv + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
        t_from = (now_srv - timedelta(seconds=span_sec)).strftime("%Y-%m-%dT%H:%M:%S")

        try:
            d = self.client.call_tool("get_chart_history", {
                "symbol": symbol,
                "period": period,
                "datetime_from": t_from,
                "datetime_to": t_to,
                "limit": min(100000, max(need * 2, 100)),
            })
        except Exception as e:
            self._last_error = (-1, str(e))
            return None

        hist = d.get("history") or []
        if not hist:
            return None

        # Deteksi apakah bar paling akhir masih berjalan (forming)
        if start_pos >= 1 and len(hist) > 1:
            last_dt = _parse_iso_dt(hist[-1].get("time"))
            if last_dt is not None:
                bar_end = last_dt + timedelta(seconds=step_sec)
                if bar_end > now_srv.replace(tzinfo=timezone.utc):
                    hist = hist[:-1]
            if start_pos > 1:
                hist = hist[:-(start_pos - 1)]
        hist = hist[-int(count):]

        dtype = np.dtype([
            ("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"),
            ("close", "f8"), ("tick_volume", "i8"), ("spread", "i4"), ("real_volume", "i8"),
        ])
        rows = []
        for r in hist:
            dt = _parse_iso_dt(r.get("time"))
            if dt is None:
                continue
            rows.append((
                int(dt.timestamp()),
                float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]),
                int(r.get("tick_volume") or r.get("volume") or 0),
                int(r.get("spread") or 0),
                int(r.get("real_volume") or 0),
            ))
        return np.array(rows, dtype=dtype) if rows else None

    def positions_get(self):
        try:
            d = self.client.call_tool("get_trading_open_positions", {"include_orders": False})
        except Exception:
            return []
        out = []
        for p in d.get("positions") or []:
            comment = str(p.get("comment") or "")
            # Order MCP tidak punya field magic -> tandai milik Alpha-Trade dari prefix komentar "AT|"
            raw_magic = p.get("magic")
            magic = int(raw_magic) if raw_magic not in (None, 0, "0") else (
                self.magic if comment.startswith("AT|") else 0
            )
            ptype = str(p.get("type") or "").lower()
            pos_type = self.POSITION_TYPE_BUY if "buy" in ptype else self.POSITION_TYPE_SELL
            out.append(SimpleNamespace(
                ticket=int(p.get("ticket") or p.get("id") or 0),
                symbol=str(p.get("symbol") or ""),
                type=pos_type,
                volume=float(p.get("volume") or 0.0),
                price_open=float(p.get("price_open") or 0.0),
                price_current=float(p.get("price_current") or p.get("price_open") or 0.0),
                sl=float(p.get("sl") or 0.0),
                tp=float(p.get("tp") or 0.0),
                profit=float(p.get("profit") or 0.0),
                magic=magic,
                comment=comment,
            ))
        return out

    def history_deals_get(self, dt_from, dt_to):
        try:
            d = self.client.call_tool("get_trading_history_positions", {
                "datetime_from": dt_from.strftime("%Y-%m-%dT%H:%M:%S"),
                "datetime_to": dt_to.strftime("%Y-%m-%dT%H:%M:%S"),
                "limit": 500,
            })
        except Exception:
            return []
        out = []
        for p in d.get("positions") or []:
            comment = str(p.get("comment") or "")
            raw_magic = p.get("magic")
            magic = int(raw_magic) if raw_magic not in (None, 0, "0") else (
                self.magic if comment.startswith("AT|") else 0
            )
            out.append(SimpleNamespace(
                magic=magic,
                profit=float(p.get("profit") or 0.0),
                swap=float(p.get("swap") or 0.0),
                commission=float(p.get("commission") or 0.0),
            ))
        return out

    def order_send(self, req):
        action = req.get("action")
        try:
            if action == self.TRADE_ACTION_DEAL and "position" in req:
                res = self.client.call_tool("trade_close_single_position", {
                    "symbol": req["symbol"],
                    "position_ticket": str(int(req["position"])),
                })
                ok = bool(res.get("ok", True)) and not res.get("error")
                return SimpleNamespace(
                    retcode=self.TRADE_RETCODE_DONE if ok else 10013,
                    order=int(req["position"]),
                    comment=str(res.get("comment") or res.get("message") or ("Done" if ok else res)),
                )
            if action == self.TRADE_ACTION_DEAL:
                args = {
                    "symbol": req["symbol"],
                    "type": "buy" if req.get("type") == self.ORDER_TYPE_BUY else "sell",
                    "volume": float(req["volume"]),
                }
                if req.get("sl"):
                    args["sl"] = float(req["sl"])
                if req.get("tp"):
                    args["tp"] = float(req["tp"])
                if req.get("comment"):
                    args["comment"] = str(req["comment"])[:31]
                res = self.client.call_tool("trade_send_market_order", args)
                tk = int(res.get("order") or res.get("position") or res.get("ticket") or res.get("deal") or 0)
                ok = bool(res.get("ok", tk > 0)) and not res.get("error")
                return SimpleNamespace(
                    retcode=self.TRADE_RETCODE_DONE if ok else 10013,
                    order=tk,
                    deal=tk,
                    comment=str(res.get("comment") or res.get("message") or ("Done" if ok else res)),
                )
            if action == self.TRADE_ACTION_SLTP:
                args = {
                    "symbol": req["symbol"],
                    "position_ticket": str(int(req["position"])),
                }
                if req.get("sl") is not None:
                    args["sl"] = float(req["sl"])
                res = self.client.call_tool("trade_modify_sl_tp", args)
                ok = bool(res.get("ok", True)) and not res.get("error")
                return SimpleNamespace(
                    retcode=self.TRADE_RETCODE_DONE if ok else 10013,
                    order=int(req["position"]),
                    comment=str(res.get("comment") or res.get("message") or ("Done" if ok else res)),
                )
        except Exception as e:
            msg = str(e)
            rc = 10027 if "not permitted" in msg.lower() else 10013
            return SimpleNamespace(retcode=rc, order=0, comment=msg)
        return SimpleNamespace(retcode=10013, order=0, comment="action tidak didukung")
