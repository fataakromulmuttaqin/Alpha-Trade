"""
MetaTrader 5 Auto-Executor untuk Alpha-Trade
--------------------------------------------
Fitur utama:
  - Koneksi langsung ke terminal MT5 Windows lewat library resmi `MetaTrader5`.
  - Default STANDBY (armed=False) saat boot — user wajib klik ARM/ON dari UI
    sebelum order otomatis dikirim.
  - Pengaman akun DEMO-only secara default (menolak akun Real/Contest bila
    `demo_only=True`).
  - Semua batas risiko (risk %, max posisi, max spread poin, daily stop R,
    simbol broker, BE offset, terminal path) dapat diatur user dari UI & .env.
  - Eksekusi split 2 order: 50% target TP1 + 50% target TP2, dengan monitor
    otomatis menggeser SL order TP2 ke Break Even saat TP1 tersentuh.
  - Level SL/TP selalu dihitung dari jarak relatif terhadap harga Ask/Bid
    aktual di broker MT5 sehingga tetap akurat meski feed sinyal berasal dari
    Binance PAXGUSDT maupun MT5 XAUUSD langsung.
"""
from datetime import datetime, timezone
import math
import os
import threading
import time

from .mt5_feed import import_mt5, resolve_mt5_symbol
from .mt5_mcp import MT5McpAdapter

DEFAULT_MT5_SETTINGS = {
    "mt5_symbol": "",                # kosong = auto-detect XAUUSD / XAUUSDm / GOLD
    "mt5_terminal_path": "",         # opsional path ke terminal64.exe
    "mt5_transport": "python",       # python (IPC lokal) | mcp (HTTP MCP bawaan MT5)
    "mt5_mcp_url": "",               # mis. http://127.0.0.1:22346/mcp
    "mt5_mcp_key": "",               # Bearer API key MT5 MCP (tidak dikirim balik ke UI)
    "mt5_risk_pct": 1.0,             # total % risiko equity per sinyal (dibagi 50/50 TP1 & TP2)
    "mt5_max_open_trades": 1,        # maks jumlah pair/grup posisi terbuka bersamaan (0 = tanpa batas)
    "mt5_max_spread_points": 45,     # maks spread broker dalam points (0 = matikan filter)
    "mt5_daily_stop_r": 2.0,         # stop entry sisa hari UTC setelah rugi >= X R (0 = matikan)
    "mt5_be_offset_points": 0,       # offset poin saat geser SL ke Break Even
    "mt5_deviation": 25,             # maks slippage dalam points
    "mt5_magic": 202605,             # magic number khusus Alpha-Trade
    "mt5_demo_only": True,           # wajib akun DEMO secara default
}


def _env_float(name, default):
    v = os.getenv(name)
    if v is None or str(v).strip() == "":
        return default
    try:
        return float(v)
    except ValueError:
        return default


def _env_int(name, default):
    v = os.getenv(name)
    if v is None or str(v).strip() == "":
        return default
    try:
        return int(float(v))
    except ValueError:
        return default


def _env_bool(name, default):
    v = os.getenv(name)
    if v is None or str(v).strip() == "":
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def default_settings_from_env():
    """Bangun nilai awal pengaturan MT5 dari env (dapat ditimpa oleh UI/app_settings.json)."""
    tr = (os.getenv("AT_MT5_TRANSPORT", DEFAULT_MT5_SETTINGS["mt5_transport"]) or "python").strip().lower()
    if tr not in ("python", "mcp"):
        tr = "python"
    return {
        "mt5_symbol": os.getenv("AT_MT5_SYMBOL", DEFAULT_MT5_SETTINGS["mt5_symbol"]).strip(),
        "mt5_terminal_path": os.getenv("AT_MT5_PATH", DEFAULT_MT5_SETTINGS["mt5_terminal_path"]).strip(),
        "mt5_transport": tr,
        "mt5_mcp_url": os.getenv("AT_MT5_MCP_URL", DEFAULT_MT5_SETTINGS["mt5_mcp_url"]).strip(),
        "mt5_mcp_key": os.getenv("AT_MT5_MCP_KEY", DEFAULT_MT5_SETTINGS["mt5_mcp_key"]).strip(),
        "mt5_risk_pct": _env_float("AT_MT5_RISK_PCT", DEFAULT_MT5_SETTINGS["mt5_risk_pct"]),
        "mt5_max_open_trades": _env_int("AT_MT5_MAX_OPEN", DEFAULT_MT5_SETTINGS["mt5_max_open_trades"]),
        "mt5_max_spread_points": _env_int("AT_MT5_MAX_SPREAD", DEFAULT_MT5_SETTINGS["mt5_max_spread_points"]),
        "mt5_daily_stop_r": _env_float("AT_MT5_DAILY_STOP_R", DEFAULT_MT5_SETTINGS["mt5_daily_stop_r"]),
        "mt5_be_offset_points": _env_int("AT_MT5_BE_OFFSET", DEFAULT_MT5_SETTINGS["mt5_be_offset_points"]),
        "mt5_deviation": _env_int("AT_MT5_DEVIATION", DEFAULT_MT5_SETTINGS["mt5_deviation"]),
        "mt5_magic": _env_int("AT_MT5_MAGIC", DEFAULT_MT5_SETTINGS["mt5_magic"]),
        "mt5_demo_only": _env_bool("AT_MT5_DEMO_ONLY", DEFAULT_MT5_SETTINGS["mt5_demo_only"]),
    }


def _floor_step(val, step):
    if step <= 0:
        return val
    steps = math.floor((val + 1e-12) / step)
    dec = max(0, len(f"{step:.10f}".rstrip("0").split(".")[1]) if "." in f"{step:.10f}" else 0)
    return round(steps * step, dec)


def _round_price(val, digits):
    return round(float(val), int(digits))


class MT5Executor:
    """Eksekutor order MetaTrader 5: split 2 order (50% TP1 + 50% TP2) + auto-BE."""

    def __init__(self, settings=None, mt5_mod=None, on_event=None):
        self.mode = "mt5"
        self._mt5 = mt5_mod
        self._custom_mt5 = mt5_mod is not None
        self._lock = threading.RLock()
        self.on_event = on_event

        cfg = default_settings_from_env()
        if settings:
            for k in DEFAULT_MT5_SETTINGS:
                if k in settings and settings[k] is not None:
                    cfg[k] = settings[k]
        self.settings = cfg

        # Default STANDBY (OFF) saat boot agar aman — user klik ARM di UI
        self.armed = False
        self.connected = False
        self.resolved_symbol = ""
        self.last_error = ""
        self._executed_keys = set()       # anti-duplikasi sinyal per (sig_time, side)
        self._tracked_pairs = {}          # gid -> dict info leg1/leg2 untuk auto-BE
        self._events = []                 # log kejadian terbaru utk UI

    # ----------------------------- Logging ----------------------------------
    def _log(self, kind, msg, extra=None):
        rec = {
            "time": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "kind": kind,
            "msg": msg,
        }
        if extra:
            rec.update(extra)
        self._events.insert(0, rec)
        self._events = self._events[:30]
        print(f"[mt5:{kind}] {msg}", flush=True)
        if self.on_event:
            try:
                self.on_event(rec)
            except Exception:
                pass
        return rec

    # ----------------------------- Koneksi ----------------------------------
    def _ensure_pkg(self):
        if self._custom_mt5:
            return self._mt5
        tr = (self.settings.get("mt5_transport") or "python").lower()
        if tr == "mcp":
            url = (self.settings.get("mt5_mcp_url") or "").strip()
            if not url:
                return None
            if not isinstance(self._mt5, MT5McpAdapter):
                self._mt5 = MT5McpAdapter(
                    url=url,
                    api_key=(self.settings.get("mt5_mcp_key") or "").strip(),
                    magic=int(self.settings.get("mt5_magic", 202605)),
                )
            return self._mt5
        if self._mt5 is None or isinstance(self._mt5, MT5McpAdapter):
            self._mt5 = import_mt5()
        return self._mt5

    def connect(self):
        """Inisialisasi koneksi ke terminal MT5 (Python IPC atau MCP HTTP) dan selesaikan simbol broker."""
        with self._lock:
            tr = (self.settings.get("mt5_transport") or "python").lower()
            mt5 = self._ensure_pkg()
            if mt5 is None:
                self.connected = False
                self.last_error = (
                    "URL MT5 MCP belum diisi (mis. http://127.0.0.1:22346/mcp)"
                    if tr == "mcp"
                    else "Package MetaTrader5 belum terpasang (pip install MetaTrader5)"
                )
                return False, self.last_error

            path = (self.settings.get("mt5_terminal_path") or "").strip()
            ok = mt5.initialize(path=path) if path else mt5.initialize()
            if not ok:
                err = mt5.last_error() if hasattr(mt5, "last_error") else "unknown"
                self.connected = False
                self.last_error = f"Gagal konek terminal MT5 ({err}). Pastikan MT5 terbuka & login."
                return False, self.last_error

            acc = mt5.account_info()
            if acc is None:
                self.connected = False
                self.last_error = "Terminal MT5 terbuka tetapi belum login ke akun mana pun."
                return False, self.last_error

            try:
                self.resolved_symbol = resolve_mt5_symbol(mt5, self.settings.get("mt5_symbol"))
            except Exception as e:
                self.connected = True
                self.resolved_symbol = ""
                self.last_error = str(e)
                return False, self.last_error

            self.connected = True
            self.last_error = ""
            self._sync_tracked_from_positions()
            self._log("connect", f"Terhubung akun #{acc.login} ({acc.server}) | simbol={self.resolved_symbol}")
            return True, f"Terhubung akun #{acc.login} · {self.resolved_symbol}"

    def disconnect(self):
        with self._lock:
            self.armed = False
            if self._mt5 is not None and hasattr(self._mt5, "shutdown"):
                try:
                    self._mt5.shutdown()
                except Exception:
                    pass
            self.connected = False
            self._log("disconnect", "Koneksi MT5 diputus & Auto-Trade diset STANDBY")

    def update_settings(self, patch):
        """Perbarui konfigurasi MT5 yang diatur user secara dinamis."""
        with self._lock:
            reconnect_needed = False
            for k, default_v in DEFAULT_MT5_SETTINGS.items():
                if k not in patch or patch[k] is None:
                    continue
                v = patch[k]
                # Jangan hapus API key tersimpan jika form UI mengirim string kosong
                if k == "mt5_mcp_key" and str(v).strip() == "":
                    continue
                if isinstance(default_v, bool):
                    v = bool(v) if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")
                elif isinstance(default_v, int):
                    v = max(0, int(float(v)))
                elif isinstance(default_v, float):
                    v = max(0.0, float(v))
                else:
                    v = str(v).strip()
                    if k == "mt5_transport" and v not in ("python", "mcp"):
                        v = "python"
                if k in ("mt5_symbol", "mt5_terminal_path", "mt5_transport", "mt5_mcp_url", "mt5_mcp_key") and v != self.settings.get(k):
                    reconnect_needed = True
                self.settings[k] = v

            if reconnect_needed and not self._custom_mt5:
                self._mt5 = None
                if self.connected:
                    self.connect()
            elif reconnect_needed and self.connected:
                self.connect()
            return dict(self.settings)

    def set_armed(self, armed: bool):
        """Aktifkan (ARM) atau matikan (STANDBY) eksekusi otomatis MT5."""
        with self._lock:
            want = bool(armed)
            if not want:
                self.armed = False
                self._log("arm", "Auto-Trade MT5 diubah ke STANDBY (OFF)")
                return True, "Auto-Trade MT5: STANDBY"

            if not self.connected:
                ok, msg = self.connect()
                if not ok:
                    self.armed = False
                    return False, msg

            ok_acc, reason = self._check_account_allowed()
            if not ok_acc:
                self.armed = False
                self._log("reject", reason)
                return False, reason

            self.armed = True
            self._log("arm", f"Auto-Trade MT5 ARMED (ON) · simbol={self.resolved_symbol} "
                             f"risk={self.settings['mt5_risk_pct']}%")
            return True, f"Auto-Trade MT5: ARMED ({self.resolved_symbol})"

    # ----------------------------- Guard & Sizing ---------------------------
    def _check_account_allowed(self):
        mt5 = self._mt5
        if not mt5 or not self.connected:
            return False, "MT5 belum terhubung"
        acc = mt5.account_info()
        if acc is None:
            return False, "Info akun MT5 tidak tersedia"
        demo_mode = getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)
        is_demo = getattr(acc, "trade_mode", -1) == demo_mode
        if self.settings.get("mt5_demo_only", True) and not is_demo:
            return False, f"Ditolak: akun #{acc.login} bukan DEMO (pengaman Demo-Only aktif)"
        if not getattr(acc, "trade_allowed", True):
            return False, "Trading pada akun MT5 tidak diizinkan oleh broker/akun"
        term = mt5.terminal_info() if hasattr(mt5, "terminal_info") else None
        if term is not None and getattr(term, "tradeapi_disabled", False):
            return False, ("Python Trade API dinonaktifkan di MT5 — buka Tools -> Options -> "
                           "Expert Advisors lalu hapus centang 'Disable algorithmic trading via external Python API'")
        return True, ""

    def _detect_filling_mode(self, sym_info):
        mt5 = self._mt5
        fok = getattr(mt5, "ORDER_FILLING_FOK", 0)
        ioc = getattr(mt5, "ORDER_FILLING_IOC", 1)
        ret = getattr(mt5, "ORDER_FILLING_RETURN", 2)
        fm = getattr(sym_info, "filling_mode", 0)
        # Bitmask resmi MT5: SYMBOL_FILLING_FOK=1, SYMBOL_FILLING_IOC=2
        if fm & 2:
            return ioc
        if fm & 1:
            return fok
        return ret

    def compute_split_lots(self, equity, sl_distance, sym_info, risk_pct=None):
        """Hitung total lot berbasis % risiko equity & jarak SL, lalu bagi 50/50 ke (lot1, lot2).
        Bila total lot < 2 * volume_min, kembalikan (0.0, volume_min) sebagai single-leg aman
        agar tidak melanggar risiko dengan menggandakan lot minimum.
        """
        rpct = float(self.settings["mt5_risk_pct"] if risk_pct is None else risk_pct)
        if equity <= 0 or sl_distance <= 0 or rpct <= 0:
            return 0.0, 0.0, 0.0

        risk_usd = equity * (rpct / 100.0)
        tick_size = float(getattr(sym_info, "trade_tick_size", 0.0) or getattr(sym_info, "point", 0.01))
        tick_val = float(getattr(sym_info, "trade_tick_value_loss", 0.0)
                         or getattr(sym_info, "trade_tick_value", 0.0) or 1.0)
        if tick_size <= 0 or tick_val <= 0:
            return 0.0, 0.0, risk_usd

        loss_per_lot = (sl_distance / tick_size) * tick_val
        if loss_per_lot <= 0:
            return 0.0, 0.0, risk_usd

        vmin = float(getattr(sym_info, "volume_min", 0.01) or 0.01)
        vmax = float(getattr(sym_info, "volume_max", 100.0) or 100.0)
        vstep = float(getattr(sym_info, "volume_step", 0.01) or 0.01)

        raw_total = min(vmax, risk_usd / loss_per_lot)
        total = _floor_step(raw_total, vstep)

        if total < vmin:
            # Cek apakah lot minimum tidak melampaui 2x batas risiko (aman untuk akun kecil)
            if vmin * loss_per_lot <= risk_usd * 2.0:
                return 0.0, vmin, risk_usd
            return 0.0, 0.0, risk_usd

        half = _floor_step(total / 2.0, vstep)
        rem = _floor_step(total - half, vstep)
        if half >= vmin and rem >= vmin:
            return half, rem, risk_usd
        # Tidak bisa dibagi dua karena dekat volume_min -> 1 leg penuh
        return 0.0, total, risk_usd

    def _open_at_positions(self):
        mt5 = self._mt5
        if not mt5 or not self.connected:
            return []
        magic = int(self.settings.get("mt5_magic", 202605))
        pos = mt5.positions_get() or []
        return [p for p in pos if getattr(p, "magic", None) == magic]

    def _active_group_count(self):
        """Hitung jumlah grup sinyal (pair TP1+TP2) yang sedang terbuka."""
        groups = set()
        for p in self._open_at_positions():
            c = str(getattr(p, "comment", "") or "")
            if c.startswith("AT|"):
                parts = c.split("|")
                if len(parts) >= 2:
                    groups.add(parts[1])
                    continue
            groups.add(f"ticket:{getattr(p, 'ticket', 0)}")
        return len(groups)

    def _daily_closed_pnl_usd(self):
        """Total P/L realisasi hari ini (UTC) dari deal MT5 dengan magic Alpha-Trade."""
        mt5 = self._mt5
        if not mt5 or not self.connected or not hasattr(mt5, "history_deals_get"):
            return 0.0
        now = datetime.now(timezone.utc)
        day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        deals = mt5.history_deals_get(day_start, now) or []
        magic = int(self.settings.get("mt5_magic", 202605))
        total = 0.0
        for d in deals:
            if getattr(d, "magic", None) != magic:
                continue
            total += (float(getattr(d, "profit", 0.0))
                      + float(getattr(d, "swap", 0.0))
                      + float(getattr(d, "commission", 0.0)))
        return total

    def _is_daily_stop_hit(self, equity):
        stop_r = float(self.settings.get("mt5_daily_stop_r", 0.0) or 0.0)
        risk_pct = float(self.settings.get("mt5_risk_pct", 1.0) or 1.0)
        if stop_r <= 0 or equity <= 0 or risk_pct <= 0:
            return False, 0.0
        pnl = self._daily_closed_pnl_usd()
        one_r_usd = equity * (risk_pct / 100.0)
        limit_usd = -stop_r * one_r_usd
        return pnl <= limit_usd, pnl

    # ----------------------------- Eksekusi Sinyal --------------------------
    def execute_signal(self, symbol, row, entry_price=None, sig_time=None):
        """Eksekusi sinyal engine ke MT5 (split 50% TP1 + 50% TP2).
        Kompatibel dengan tanda tangan `TestnetExecutor.execute_signal(symbol, row)`.
        """
        with self._lock:
            if not self.armed:
                return None

            if not self.connected:
                ok, msg = self.connect()
                if not ok:
                    self._log("error", f"Eksekusi dibatalkan: {msg}")
                    return None

            ok_acc, reason = self._check_account_allowed()
            if not ok_acc:
                self._log("reject", reason)
                return None

            mt5 = self._mt5
            direction = int(row["signal"])
            if direction not in (1, -1):
                return None

            # Selesaikan simbol target (mendukung multi-pair: EURUSD, GBPUSD, USDJPY, XAUUSD, dll.)
            sym = self.resolved_symbol
            req_sym = (symbol or "").strip()
            if req_sym and req_sym.upper() not in ("PAXGUSDT", "GC=F"):
                try:
                    sym = resolve_mt5_symbol(mt5, req_sym)
                except Exception:
                    sym = self.resolved_symbol

            sig_key = f"{sym}|{sig_time or row.get('time', '')}|{direction}"
            if sig_time is not None and sig_key in self._executed_keys:
                return None

            acc = mt5.account_info()
            equity = float(getattr(acc, "equity", 0.0) or getattr(acc, "balance", 0.0))

            # 1) Daily stop check
            hit_stop, daily_pnl = self._is_daily_stop_hit(equity)
            if hit_stop:
                self._log("reject", f"Daily stop tercapai (P/L hari ini ${daily_pnl:.2f}) — sinyal dilewati")
                return None

            # 2) Max open groups check
            max_open = int(self.settings.get("mt5_max_open_trades", 0) or 0)
            cur_groups = self._active_group_count()
            if max_open > 0 and cur_groups >= max_open:
                self._log("reject", f"Batas posisi terbuka tercapai ({cur_groups}/{max_open}) — sinyal dilewati")
                return None

            # 3) Info simbol + harga live broker
            info = mt5.symbol_info(sym)
            tick = mt5.symbol_info_tick(sym)
            if info is None or tick is None:
                self._log("error", f"Gagal membaca harga/info simbol MT5 '{sym}'")
                return None

            point = float(getattr(info, "point", 0.01) or 0.01)
            digits = int(getattr(info, "digits", 2) or 2)
            spread_pts = round((float(tick.ask) - float(tick.bid)) / point, 1) if point > 0 else 0.0
            max_spread = int(self.settings.get("mt5_max_spread_points", 0) or 0)
            if max_spread > 0 and spread_pts > max_spread:
                self._log("reject", f"Spread {sym} terlalu lebar ({spread_pts:.1f} > {max_spread} pts) — sinyal dilewati")
                return None

            # 4) Hitung jarak relatif SL / TP1 / TP2 -> jangkar ke Ask/Bid live broker
            ref_entry = float(entry_price if entry_price is not None else row["entry"])
            sl_dist = abs(ref_entry - float(row["sl"]))
            tp1_dist = abs(float(row["tp1"]) - ref_entry)
            tp2_dist = abs(float(row["tp2"]) - ref_entry)

            stops_level_pts = float(getattr(info, "trade_stops_level", 0) or 0)
            min_stop_dist = (stops_level_pts + 2) * point
            if sl_dist < min_stop_dist:
                self._log("reject", f"Jarak SL ({sl_dist:.3f}) < stops_level broker ({min_stop_dist:.3f})")
                return None

            is_buy = direction == 1
            exec_px = float(tick.ask if is_buy else tick.bid)
            sl_px = _round_price(exec_px - sl_dist if is_buy else exec_px + sl_dist, digits)
            tp1_px = _round_price(exec_px + max(tp1_dist, min_stop_dist) if is_buy
                                  else exec_px - max(tp1_dist, min_stop_dist), digits)
            tp2_px = _round_price(exec_px + max(tp2_dist, min_stop_dist) if is_buy
                                  else exec_px - max(tp2_dist, min_stop_dist), digits)

            # 5) Auto-lot split 50/50
            lot1, lot2, risk_usd = self.compute_split_lots(equity, sl_dist, info)
            if lot1 <= 0 and lot2 <= 0:
                self._log("reject", f"Ukuran lot 0 (equity=${equity:.2f}, risk={self.settings['mt5_risk_pct']}%, SL={sl_dist:.2f})")
                return None

            gid = datetime.now(timezone.utc).strftime("%d%H%M%S")
            order_type = getattr(mt5, "ORDER_TYPE_BUY", 0) if is_buy else getattr(mt5, "ORDER_TYPE_SELL", 1)
            action_deal = getattr(mt5, "TRADE_ACTION_DEAL", 1)
            time_gtc = getattr(mt5, "ORDER_TIME_GTC", 0)
            filling = self._detect_filling_mode(info)
            magic = int(self.settings.get("mt5_magic", 202605))
            dev = int(self.settings.get("mt5_deviation", 25))
            retcode_done = getattr(mt5, "TRADE_RETCODE_DONE", 10009)

            def _send(vol, tp_px, leg_tag):
                req = {
                    "action": action_deal,
                    "symbol": sym,
                    "volume": float(vol),
                    "type": order_type,
                    "price": exec_px,
                    "sl": sl_px,
                    "tp": tp_px,
                    "deviation": dev,
                    "magic": magic,
                    "comment": f"AT|{gid}|{leg_tag}",
                    "type_time": time_gtc,
                    "type_filling": filling,
                }
                res = mt5.order_send(req)
                ok_send = res is not None and getattr(res, "retcode", None) == retcode_done
                return ok_send, res, req

            sent = {"gid": gid, "symbol": sym, "side": "BUY" if is_buy else "SELL",
                    "price": exec_px, "sl": sl_px, "tp1": tp1_px, "tp2": tp2_px,
                    "tickets": []}

            def _err_hint(res):
                rc = getattr(res, "retcode", None) if res else "None"
                cm = getattr(res, "comment", "") if res else ""
                if rc == 10027:
                    return f"10027: {cm} — aktifkan tombol Algo Trading di toolbar MT5 (Ctrl+E)"
                return f"{rc}: {cm}"

            t1 = None
            t2 = None
            if lot1 > 0:
                ok1, res1, _ = _send(lot1, tp1_px, "TP1")
                if ok1:
                    t1 = int(getattr(res1, "order", 0) or getattr(res1, "deal", 0))
                    sent["tickets"].append({"leg": "TP1", "ticket": t1, "volume": lot1, "tp": tp1_px})
                else:
                    self._log("error", f"Order leg TP1 gagal ({_err_hint(res1)})")

            if lot2 > 0:
                ok2, res2, _ = _send(lot2, tp2_px, "TP2")
                if ok2:
                    t2 = int(getattr(res2, "order", 0) or getattr(res2, "deal", 0))
                    sent["tickets"].append({"leg": "TP2", "ticket": t2, "volume": lot2, "tp": tp2_px})
                else:
                    self._log("error", f"Order leg TP2 gagal ({_err_hint(res2)})")

            if not sent["tickets"]:
                return None

            if sig_time is not None:
                self._executed_keys.add(sig_key)

            # Daftarkan ke monitor Auto-BE bila kedua leg (atau leg TP2) aktif
            if t2:
                self._tracked_pairs[gid] = {
                    "gid": gid,
                    "symbol": sym,
                    "side": "BUY" if is_buy else "SELL",
                    "ticket1": t1,
                    "ticket2": t2,
                    "entry": exec_px,
                    "sl": sl_px,
                    "tp1": tp1_px,
                    "tp2": tp2_px,
                    "digits": digits,
                    "point": point,
                    "be_done": False,
                    "risk_usd": round(risk_usd, 2),
                }

            legs_txt = " + ".join(f"{x['leg']} {x['volume']}L (#{x['ticket']})" for x in sent["tickets"])
            self._log("order",
                      f"{sent['side']} {sym} @ {exec_px} | SL {sl_px} | {legs_txt} (risk ${risk_usd:.2f})",
                      {"gid": gid})
            return sent

    # ----------------------------- Monitor Auto-BE --------------------------
    def _sync_tracked_from_positions(self):
        """Pulihkan tracking pair dari komentar posisi MT5 yang masih terbuka (pasca restart)."""
        by_gid = {}
        for p in self._open_at_positions():
            c = str(getattr(p, "comment", "") or "")
            if not c.startswith("AT|"):
                continue
            parts = c.split("|")
            if len(parts) < 3:
                continue
            gid, leg = parts[1], parts[2]
            by_gid.setdefault(gid, {})[leg] = p

        for gid, legs in by_gid.items():
            if gid in self._tracked_pairs:
                continue
            p2 = legs.get("TP2")
            p1 = legs.get("TP1")
            if not p2:
                continue
            info = self._mt5.symbol_info(p2.symbol)
            digits = int(getattr(info, "digits", 2) or 2) if info else 2
            point = float(getattr(info, "point", 0.01) or 0.01) if info else 0.01
            is_buy = getattr(p2, "type", 0) == getattr(self._mt5, "POSITION_TYPE_BUY", 0)
            entry = float(getattr(p2, "price_open", 0.0))
            cur_sl = float(getattr(p2, "sl", 0.0))
            already_be = (is_buy and cur_sl >= entry) or ((not is_buy) and cur_sl > 0 and cur_sl <= entry)
            self._tracked_pairs[gid] = {
                "gid": gid,
                "symbol": p2.symbol,
                "side": "BUY" if is_buy else "SELL",
                "ticket1": int(p1.ticket) if p1 else None,
                "ticket2": int(p2.ticket),
                "entry": entry,
                "sl": cur_sl,
                "tp1": float(getattr(p1, "tp", 0.0)) if p1 else 0.0,
                "tp2": float(getattr(p2, "tp", 0.0)),
                "digits": digits,
                "point": point,
                "be_done": already_be,
            }

    def check_breakeven(self):
        """Cek semua pair TP1+TP2 aktif: bila leg TP1 sudah close profit (atau harga
        mencapai TP1), geser SL leg TP2 ke Break Even (+ offset poin).
        Mengembalikan daftar event BE yang baru dieksekusi."""
        with self._lock:
            if not self.connected or not self._mt5:
                return []
            mt5 = self._mt5
            open_pos = {int(p.ticket): p for p in self._open_at_positions()}
            actions = []
            done_gids = []

            action_sltp = getattr(mt5, "TRADE_ACTION_SLTP", 6)
            retcode_done = getattr(mt5, "TRADE_RETCODE_DONE", 10009)
            be_off_pts = int(self.settings.get("mt5_be_offset_points", 0) or 0)

            for gid, pair in list(self._tracked_pairs.items()):
                t1, t2 = pair.get("ticket1"), pair.get("ticket2")
                p2 = open_pos.get(t2)
                if p2 is None:
                    done_gids.append(gid)
                    continue
                if pair.get("be_done"):
                    continue

                is_buy = pair["side"] == "BUY"
                tick = mt5.symbol_info_tick(pair["symbol"])
                if tick is None:
                    continue
                cur_px = float(tick.bid if is_buy else tick.ask)

                # Kondisi trigger BE:
                # 1) Leg TP1 sudah tertutup (tidak ada di open_pos) DAN harga saat ini
                #    berada di sisi profit (>= entry untuk BUY, <= entry untuk SELL), ATAU
                # 2) Harga sudah menyentuh/melewati level TP1
                tp1_level = float(pair.get("tp1") or 0.0)
                t1_closed = (t1 is not None) and (t1 not in open_pos)
                hit_tp1_px = (tp1_level > 0) and (
                    (is_buy and cur_px >= tp1_level) or ((not is_buy) and cur_px <= tp1_level)
                )
                in_profit = (is_buy and cur_px > pair["entry"]) or ((not is_buy) and cur_px < pair["entry"])

                if not (hit_tp1_px or (t1_closed and in_profit)):
                    continue

                offset = be_off_pts * float(pair.get("point", 0.01))
                be_sl = _round_price(
                    pair["entry"] + offset if is_buy else pair["entry"] - offset,
                    pair.get("digits", 2),
                )
                cur_sl = float(getattr(p2, "sl", 0.0))
                needs_move = (is_buy and (cur_sl == 0 or cur_sl < be_sl)) or (
                    (not is_buy) and (cur_sl == 0 or cur_sl > be_sl)
                )
                if not needs_move:
                    pair["be_done"] = True
                    continue

                req = {
                    "action": action_sltp,
                    "symbol": pair["symbol"],
                    "position": int(t2),
                    "sl": be_sl,
                    "tp": float(getattr(p2, "tp", pair["tp2"])),
                }
                res = mt5.order_send(req)
                if res is not None and getattr(res, "retcode", None) == retcode_done:
                    pair["be_done"] = True
                    pair["sl"] = be_sl
                    ev = self._log("be", f"TP1 tercapai -> SL posisi #{t2} ({pair['symbol']}) digeser ke BE {be_sl}")
                    actions.append(ev)
                else:
                    rc = getattr(res, "retcode", None) if res else "None"
                    cm = getattr(res, "comment", "") if res else ""
                    self._log("error", f"Gagal geser SL #{t2} ke BE ({rc}: {cm})")

            for gid in done_gids:
                self._tracked_pairs.pop(gid, None)
            return actions

    # ----------------------------- Tutup Posisi -----------------------------
    def close_position(self, ticket):
        """Tutup satu posisi MT5 berdasarkan ticket (hanya milik magic Alpha-Trade)."""
        with self._lock:
            if not self.connected or not self._mt5:
                return False, "MT5 belum terhubung"
            mt5 = self._mt5
            target = next((p for p in self._open_at_positions() if int(p.ticket) == int(ticket)), None)
            if target is None:
                return False, f"Posisi #{ticket} tidak ditemukan"

            info = mt5.symbol_info(target.symbol)
            tick = mt5.symbol_info_tick(target.symbol)
            if info is None or tick is None:
                return False, "Gagal baca harga simbol"

            is_buy = getattr(target, "type", 0) == getattr(mt5, "POSITION_TYPE_BUY", 0)
            close_type = getattr(mt5, "ORDER_TYPE_SELL", 1) if is_buy else getattr(mt5, "ORDER_TYPE_BUY", 0)
            close_px = float(tick.bid if is_buy else tick.ask)
            req = {
                "action": getattr(mt5, "TRADE_ACTION_DEAL", 1),
                "symbol": target.symbol,
                "volume": float(target.volume),
                "type": close_type,
                "position": int(target.ticket),
                "price": close_px,
                "deviation": int(self.settings.get("mt5_deviation", 25)),
                "magic": int(self.settings.get("mt5_magic", 202605)),
                "comment": "AT|CLOSE",
                "type_time": getattr(mt5, "ORDER_TIME_GTC", 0),
                "type_filling": self._detect_filling_mode(info),
            }
            res = mt5.order_send(req)
            if res is not None and getattr(res, "retcode", None) == getattr(mt5, "TRADE_RETCODE_DONE", 10009):
                self._log("close", f"Posisi #{ticket} ({target.symbol} {target.volume}L) ditutup @ {close_px}")
                return True, f"Posisi #{ticket} ditutup"
            rc = getattr(res, "retcode", None) if res else "None"
            return False, f"Gagal tutup #{ticket} (retcode={rc})"

    def close_all(self):
        """Tutup semua posisi terbuka milik Alpha-Trade."""
        closed, errs = 0, []
        for p in list(self._open_at_positions()):
            ok, msg = self.close_position(p.ticket)
            if ok:
                closed += 1
            else:
                errs.append(msg)
        return closed, errs

    def live_quote(self, sym):
        """Ambil quote Bid/Ask/digits live untuk satu simbol MT5 tertentu."""
        with self._lock:
            if not self.connected or not self._mt5 or not sym:
                return None
            mt5 = self._mt5
            info = mt5.symbol_info(sym)
            tick = mt5.symbol_info_tick(sym)
            if not info or not tick:
                return None
            pt = float(getattr(info, "point", 0.01) or 0.01)
            digits = int(getattr(info, "digits", 2) or 2)
            return {
                "name": sym,
                "bid": round(float(tick.bid), digits),
                "ask": round(float(tick.ask), digits),
                "digits": digits,
                "spread_points": round((float(tick.ask) - float(tick.bid)) / pt, 1) if pt > 0 else 0.0,
            }

    def list_broker_symbols(self, query="", limit=80):
        """Daftar simbol yang tersedia di broker MT5 (prioritas Market Watch + pair populer)."""
        with self._lock:
            if not self.connected:
                self.connect()
            if not self.connected or not self._mt5:
                return []
            q = (query or "").strip().upper()
            popular = {s: i for i, s in enumerate((
                "XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
                "EURJPY", "GBPJPY", "EURGBP", "XAGUSD", "US500", "USTECH100M", "US30M", "DE40", "JPN225", "UK100",
            ))}
            syms = self._mt5.symbols_get() or []
            rows = []
            for s in syms:
                name = str(getattr(s, "name", "") or "")
                if not name:
                    continue
                if q and q not in name.upper():
                    continue
                vis = bool(getattr(s, "visible", False))
                pop_rank = popular.get(name.upper(), 999)
                rows.append((pop_rank, 0 if vis else 1, len(name), name, int(getattr(s, "digits", 2) or 2), vis))
            rows.sort()
            return [{"symbol": r[3], "digits": r[4], "visible": r[5]} for r in rows[:int(limit)]]

    # ----------------------------- Status JSON (UI) -------------------------
    def status(self):
        """Kembalikan ringkasan state MT5 lengkap untuk REST /api/mt5/status & snapshot."""
        with self._lock:
            pkg_ok = self._ensure_pkg() is not None
            account = None
            sym_out = None
            positions = []
            daily_pnl = 0.0
            daily_stop_hit = False

            if self.connected and self._mt5 is not None:
                mt5 = self._mt5
                acc = mt5.account_info()
                if acc is not None:
                    demo_mode = getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)
                    tm = getattr(acc, "trade_mode", -1)
                    mode_lbl = "DEMO" if tm == demo_mode else ("CONTEST" if tm == 1 else "REAL")
                    term = mt5.terminal_info() if hasattr(mt5, "terminal_info") else None
                    eq = float(getattr(acc, "equity", 0.0))
                    daily_stop_hit, daily_pnl = self._is_daily_stop_hit(eq)
                    account = {
                        "login": getattr(acc, "login", 0),
                        "server": getattr(acc, "server", ""),
                        "currency": getattr(acc, "currency", "USD"),
                        "balance": round(float(getattr(acc, "balance", 0.0)), 2),
                        "equity": round(eq, 2),
                        "profit": round(float(getattr(acc, "profit", 0.0)), 2),
                        "margin_free": round(float(getattr(acc, "margin_free", 0.0)), 2),
                        "leverage": getattr(acc, "leverage", 0),
                        "trade_mode": mode_lbl,
                        "is_demo": tm == demo_mode,
                        "algo_allowed": not bool(getattr(term, "tradeapi_disabled", False)) if term else True,
                        "terminal_trade_allowed": bool(getattr(term, "trade_allowed", True)) if term else True,
                        "tradeapi_disabled": bool(getattr(term, "tradeapi_disabled", False)) if term else False,
                    }
                if self.resolved_symbol:
                    info = mt5.symbol_info(self.resolved_symbol)
                    tick = mt5.symbol_info_tick(self.resolved_symbol)
                    if info and tick:
                        pt = float(getattr(info, "point", 0.01) or 0.01)
                        sym_out = {
                            "name": self.resolved_symbol,
                            "bid": float(tick.bid),
                            "ask": float(tick.ask),
                            "spread_points": round((float(tick.ask) - float(tick.bid)) / pt, 1) if pt > 0 else 0.0,
                            "digits": int(getattr(info, "digits", 2) or 2),
                            "volume_min": float(getattr(info, "volume_min", 0.01) or 0.01),
                            "volume_step": float(getattr(info, "volume_step", 0.01) or 0.01),
                        }
                buy_t = getattr(mt5, "POSITION_TYPE_BUY", 0)
                for p in self._open_at_positions():
                    positions.append({
                        "ticket": int(p.ticket),
                        "symbol": p.symbol,
                        "side": "BUY" if getattr(p, "type", 0) == buy_t else "SELL",
                        "volume": float(p.volume),
                        "price_open": float(p.price_open),
                        "price_current": float(getattr(p, "price_current", p.price_open)),
                        "sl": float(getattr(p, "sl", 0.0)),
                        "tp": float(getattr(p, "tp", 0.0)),
                        "profit": round(float(getattr(p, "profit", 0.0)), 2),
                        "comment": str(getattr(p, "comment", "")),
                    })

            safe_settings = dict(self.settings)
            safe_settings["mt5_mcp_key_set"] = bool(safe_settings.get("mt5_mcp_key"))
            safe_settings["mt5_mcp_key"] = ""   # jangan bocorkan key mentah ke /api/snapshot
            return {
                "installed": pkg_ok,
                "connected": self.connected,
                "armed": self.armed,
                "transport": self.settings.get("mt5_transport", "python"),
                "resolved_symbol": self.resolved_symbol,
                "account": account,
                "symbol_info": sym_out,
                "positions": positions,
                "open_groups": len(set(
                    (p["comment"].split("|")[1] if p["comment"].startswith("AT|") and len(p["comment"].split("|")) >= 2
                     else f"t:{p['ticket']}") for p in positions
                )),
                "daily_pnl_usd": round(daily_pnl, 2),
                "daily_stop_hit": daily_stop_hit,
                "settings": safe_settings,
                "last_error": self.last_error,
                "events": self._events[:15],
            }
