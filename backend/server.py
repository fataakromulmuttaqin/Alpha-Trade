"""
Alpha-Trade dashboard server (pola sama dengan Meridian server.js)
------------------------------------------------------------------
- HTTP  : static file dari frontend/ + REST API  (port default 8788)
- WS    : broadcast snapshot & candle update (port default 8789)

REST:
  GET /api/health    -> status server
  GET /api/snapshot  -> state penuh dashboard (JSON)
  GET /api/candles?tf=15m&limit=500 -> candle [t,o,h,l,c,v]
  GET /api/signals   -> log sinyal

WS  (ws://host:PORT/):
  connect -> {"type":"hello", ...}
  tiap update -> {"type":"snapshot", data:...} | {"type":"candle", tf, candle}
"""
import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import websockets

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(PROJECT_ROOT, "frontend")
MIME = {".html": "text/html", ".js": "application/javascript",
        ".css": "text/css", ".png": "image/png", ".svg": "image/svg+xml",
        ".ico": "image/x-icon", ".json": "application/json"}


class DashboardServer:
    """state: objek dengan .snapshot() -> dict|None, .candles(tf, limit),
    .signals(), .health() — dipasok run.py."""

    def __init__(self, state, http_port=8788, ws_port=8789):
        self.state = state
        self.http_port = http_port
        self.ws_port = ws_port
        self.clients = set()
        self._http = None
        self._ws_server = None
        self._loop = None

    # ----------------------------- HTTP ------------------------------------
    def _handler(self):
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):                      # senyapkan log akses
                pass

            def _json(self, obj, code=200):
                body = json.dumps(obj, default=str).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def do_OPTIONS(self):
                """Preflight CORS utk bridge frontend terpisah (Vercel -> tunnel)."""
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Access-Control-Max-Age", "86400")
                self.end_headers()

            def do_GET(self):
                u = urlparse(self.path)
                if u.path == "/api/health":
                    return self._json(srv.state.health())
                if u.path == "/api/config":
                    return self._json({"http_port": srv.http_port,
                                       "ws_port": srv.ws_port})
                if u.path == "/api/snapshot":
                    q = parse_qs(u.query)
                    sym = (q.get("symbol") or [None])[0]
                    snap = srv.state.snapshot(view_tf=(q.get("tf") or [None])[0], symbol=sym)
                    if snap is None:
                        return self._json({"error": "buffer belum siap"}, 503)
                    return self._json(snap)
                if u.path == "/api/candles":
                    q = parse_qs(u.query)
                    tf = (q.get("tf") or [""])[0]
                    sym = (q.get("symbol") or [None])[0]
                    limit = int((q.get("limit") or ["1500"])[0])
                    rows = srv.state.candles(tf, limit, symbol=sym)
                    if rows is None:
                        return self._json({"error": f"tf '{tf}' tidak tersedia"}, 404)
                    return self._json({"tf": tf, "symbol": sym or srv.state.symbol, "candles": rows})
                if u.path == "/api/signals":
                    q = parse_qs(u.query)
                    sym = (q.get("symbol") or [None])[0]
                    return self._json(srv.state.signals(symbol=sym))
                if u.path == "/api/sim":
                    q = parse_qs(u.query)
                    sym = (q.get("symbol") or [None])[0]
                    return self._json(srv.state.sim_data(symbol=sym))
                if u.path == "/api/symbols":
                    return self._json({"symbols": srv.state.symbol_list(), "primary": srv.state.symbol})
                if u.path == "/api/mt5/symbols":
                    q = parse_qs(u.query)
                    qry = (q.get("q") or [""])[0]
                    lim = int((q.get("limit") or ["80"])[0])
                    return self._json({"symbols": srv.state.mt5.list_broker_symbols(query=qry, limit=lim),
                                       "active": srv.state.symbol_list()})
                if u.path == "/api/news":
                    items = srv.state.news_feed.maybe_refresh()
                    return self._json({"items": items, "count": len(items),
                                       "updated": srv.state.news_feed.updated})
                if u.path == "/api/research":
                    rp = os.path.join(PROJECT_ROOT, "cache", "research_report.json")
                    if not os.path.isfile(rp):
                        return self._json({"error": "belum ada laporan; jalankan backend.research_report"}, 503)
                    try:
                        with open(rp, encoding="utf-8") as f:
                            return self._json(json.load(f))
                    except (json.JSONDecodeError, OSError):
                        return self._json({"error": "laporan rusak; jalankan ulang backend.research_report"}, 503)
                if u.path == "/api/mt5/status":
                    return self._json(srv.state.mt5.status())
                if u.path == "/api/settings":
                    st = getattr(srv.state, "app_settings", None) or {}
                    return self._json({
                        "sensitivity": srv.state.sensitivity,
                        "exit_protocol": srv.state.exit_protocol,
                        "tg_token": st.get("tg_token", ""),
                        "tg_chat_id": st.get("tg_chat_id", ""),
                        "tg_on": st.get("tg_on", True),
                        "notify_signals": st.get("notify_signals", True),
                        "notify_trades": st.get("notify_trades", True),
                    })
                if u.path == "/api/tg-test" and self.command == "POST":
                    pass  # ditangani do_POST
                # static file
                path = u.path
                if path == "/":
                    path = "/index.html"
                fp = os.path.normpath(os.path.join(WEB_DIR, path.lstrip("/")))
                if not fp.startswith(WEB_DIR) or not os.path.isfile(fp):
                    self.send_response(404)
                    self.end_headers()
                    return
                ext = os.path.splitext(fp)[1].lower()
                with open(fp, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                u = urlparse(self.path)
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    raw_body = self.rfile.read(length) if length > 0 else b"{}"
                    body = json.loads(raw_body or b"{}")
                except Exception:
                    body = None

                # --- Multi-pair management endpoints ---
                if u.path == "/api/symbols/add":
                    if body is None or not body.get("symbol"):
                        return self._json({"ok": False, "error": "field 'symbol' wajib diisi"}, 400)
                    ok, msg, sym = srv.state.add_mt5_symbol(body["symbol"])
                    return self._json({"ok": ok, "msg": msg, "symbol": sym,
                                       "symbols": srv.state.symbol_list()}, 200 if ok else 400)
                if u.path == "/api/symbols/remove":
                    if body is None or not body.get("symbol"):
                        return self._json({"ok": False, "error": "field 'symbol' wajib diisi"}, 400)
                    ok, msg = srv.state.remove_mt5_symbol(body["symbol"])
                    return self._json({"ok": ok, "msg": msg,
                                       "symbols": srv.state.symbol_list()}, 200 if ok else 400)

                # --- MetaTrader 5 endpoints ---
                if u.path == "/api/mt5/connect":
                    if body is None:
                        return self._json({"ok": False, "error": "body JSON tidak valid"}, 400)
                    if body:
                        srv.state.update_mt5_settings(body)
                    ok, msg = srv.state.mt5.connect()
                    srv.state.rebuild_and_broadcast(recompute_signals=False)
                    return self._json({"ok": ok, "msg": msg, "status": srv.state.mt5.status()},
                                      200 if ok else 502)
                if u.path == "/api/mt5/disconnect":
                    srv.state.mt5.disconnect()
                    srv.state.rebuild_and_broadcast(recompute_signals=False)
                    return self._json({"ok": True, "msg": "MT5 diputus", "status": srv.state.mt5.status()})
                if u.path == "/api/mt5/arm":
                    if body is None or "armed" not in body:
                        return self._json({"ok": False, "error": "field 'armed' (bool) wajib diisi"}, 400)
                    ok, msg = srv.state.mt5.set_armed(bool(body["armed"]))
                    srv.state.rebuild_and_broadcast(recompute_signals=False)
                    return self._json({"ok": ok, "msg": msg, "status": srv.state.mt5.status()},
                                      200 if ok else 400)
                if u.path == "/api/mt5/settings":
                    if body is None:
                        return self._json({"ok": False, "error": "body JSON tidak valid"}, 400)
                    settings = srv.state.update_mt5_settings(body)
                    srv.state.rebuild_and_broadcast(recompute_signals=False)
                    return self._json({"ok": True, "settings": settings, "status": srv.state.mt5.status()})
                if u.path == "/api/mt5/close":
                    if body is None:
                        return self._json({"ok": False, "error": "body JSON tidak valid"}, 400)
                    if body.get("all"):
                        n, errs = srv.state.mt5.close_all()
                        srv.state.rebuild_and_broadcast(recompute_signals=False)
                        return self._json({"ok": len(errs) == 0,
                                           "msg": f"{n} posisi ditutup" + (f" ({'; '.join(errs)})" if errs else ""),
                                           "status": srv.state.mt5.status()})
                    if "ticket" in body:
                        ok, msg = srv.state.mt5.close_position(body["ticket"])
                        srv.state.rebuild_and_broadcast(recompute_signals=False)
                        return self._json({"ok": ok, "msg": msg, "status": srv.state.mt5.status()},
                                          200 if ok else 400)
                    return self._json({"ok": False, "error": "kirim 'ticket' atau 'all: true'"}, 400)

                if u.path == "/api/tg-test":
                    body = body or {}
                    st = getattr(srv.state, "app_settings", {}) or {}
                    token = body.get("tg_token") or st.get("tg_token", "")
                    chat = body.get("tg_chat_id") or st.get("tg_chat_id", "")
                    if not token or not chat:
                        return self._json({"ok": False, "error": "token/chat kosong"}, 400)
                    try:
                        import requests as _rq
                        r = _rq.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                     json={"chat_id": chat, "text": "✅ Alpha-Trade: tes notifikasi berhasil"},
                                     timeout=12)
                        j = r.json()
                        if r.status_code == 200 and j.get("ok"):
                            return self._json({"ok": True, "msg": "tes terkirim — cek Telegram Anda"})
                        return self._json({"ok": False,
                                           "error": f"Telegram: {r.status_code} — {j.get('description','?')}"}, 502)
                    except Exception as e:
                        return self._json({"ok": False, "error": str(e)}, 502)
                if u.path != "/api/settings":
                    self.send_response(404)
                    self.end_headers()
                    return
                if body is None:
                    return self._json({"error": "body JSON tidak valid"}, 400)
                before = (srv.state.sensitivity, srv.state.exit_protocol)
                result = srv.state.apply_settings(body)
                mode_changed = (srv.state.sensitivity, srv.state.exit_protocol) != before
                # mode analisis berubah -> hitung ulang sinyal historis agar
                # marker di chart langsung mencerminkan mode baru
                srv.state.rebuild_and_broadcast(recompute_signals=mode_changed)
                return self._json(result)

        return H

    def start_http(self):
        self._http = ThreadingHTTPServer(("0.0.0.0", self.http_port), self._handler())
        t = threading.Thread(target=self._http.serve_forever, daemon=True)
        t.start()
        print(f"[http] dashboard: http://localhost:{self.http_port}", flush=True)

    # ----------------------------- WebSocket -------------------------------
    async def _ws_handler(self, ws):
        self.clients.add(ws)
        try:
            snap = self.state.snapshot()
            await ws.send(json.dumps({"type": "hello", "clients": len(self.clients),
                                      "snapshot": snap}, default=str))
            async for _ in ws:                            # abaikan pesan klien
                pass
        except Exception:
            pass
        finally:
            self.clients.discard(ws)

    async def broadcast(self, msg):
        if not self.clients:
            return
        payload = json.dumps(msg, default=str)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    def broadcast_threadsafe(self, msg):
        """Dipanggil dari thread/loop lain."""
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self.broadcast(msg), self._loop)

    async def start_ws(self):
        self._loop = asyncio.get_running_loop()
        try:
            self._ws_server = await websockets.serve(
                self._ws_handler, "0.0.0.0", self.ws_port)
            print(f"[ws] broadcast: ws://localhost:{self.ws_port}", flush=True)
        except OSError as e:
            # PaaS kadang hanya membuka satu port — dashboard tetap jalan
            # lewat polling /api/snapshot.
            print(f"[ws] port {self.ws_port} tidak tersedia ({e}); "
                  f"klien akan memakai polling", flush=True)

    async def start(self):
        self.start_http()
        await self.start_ws()

    def stop(self):
        if self._http:
            self._http.shutdown()
