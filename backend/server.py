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

            def do_GET(self):
                u = urlparse(self.path)
                if u.path == "/api/health":
                    return self._json(srv.state.health())
                if u.path == "/api/config":
                    return self._json({"http_port": srv.http_port,
                                       "ws_port": srv.ws_port})
                if u.path == "/api/snapshot":
                    q = parse_qs(u.query)
                    snap = srv.state.snapshot(view_tf=(q.get("tf") or [None])[0])
                    if snap is None:
                        return self._json({"error": "buffer belum siap"}, 503)
                    return self._json(snap)
                if u.path == "/api/candles":
                    q = parse_qs(u.query)
                    tf = (q.get("tf") or [""])[0]
                    limit = int((q.get("limit") or ["1500"])[0])
                    rows = srv.state.candles(tf, limit)
                    if rows is None:
                        return self._json({"error": f"tf '{tf}' tidak tersedia"}, 404)
                    return self._json({"tf": tf, "candles": rows})
                if u.path == "/api/signals":
                    return self._json(srv.state.signals())
                if u.path == "/api/research":
                    rp = os.path.join(PROJECT_ROOT, "cache", "research_report.json")
                    if not os.path.isfile(rp):
                        return self._json({"error": "belum ada laporan; jalankan backend.research_report"}, 503)
                    try:
                        with open(rp, encoding="utf-8") as f:
                            return self._json(json.load(f))
                    except (json.JSONDecodeError, OSError):
                        return self._json({"error": "laporan rusak; jalankan ulang backend.research_report"}, 503)
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
                if u.path != "/api/settings":
                    self.send_response(404)
                    self.end_headers()
                    return
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(length) or b"{}")
                except Exception:
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
        self._ws_server = await websockets.serve(
            self._ws_handler, "0.0.0.0", self.ws_port)
        print(f"[ws] broadcast: ws://localhost:{self.ws_port}", flush=True)

    async def start(self):
        self.start_http()
        await self.start_ws()

    def stop(self):
        if self._http:
            self._http.shutdown()
