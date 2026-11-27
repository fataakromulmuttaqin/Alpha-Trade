"""Unit test integrasi MetaTrader 5 Alpha-Trade (feed, auto-detect, split order,
Auto-BE, demo-only lock, user-configurable risk guards, dan REST API).

Jalankan: python -m pytest backend/tests -q
"""
from datetime import datetime, timezone
import pathlib
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.mt5_feed import MT5MultiFeed, fetch_mt5_candles, resolve_mt5_symbol  # noqa: E402
from backend.mt5_executor import MT5Executor  # noqa: E402


class FakeMT5:
    """Mock lengkap API MetaTrader5 untuk pengujian deterministik tanpa terminal."""
    TIMEFRAME_M1 = 1
    TIMEFRAME_M5 = 5
    TIMEFRAME_M15 = 15
    TIMEFRAME_M30 = 30
    TIMEFRAME_H1 = 16385
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

    def __init__(self, symbols=("XAUUSDm", "EURUSD"), equity=10000.0,
                 trade_mode=0, bid=3340.00, ask=3340.20):
        self.initialized = False
        self.trade_mode = trade_mode
        self.equity = equity
        self.bid = bid
        self.ask = ask
        self.algo_allowed = True
        self.tradeapi_disabled = False
        self._symbols = {
            s: SimpleNamespace(
                name=s, visible=True, point=0.01, digits=2,
                trade_tick_size=0.01, trade_tick_value=1.0,
                trade_tick_value_loss=1.0,
                volume_min=0.01, volume_max=50.0, volume_step=0.01,
                filling_mode=2, trade_stops_level=10,
            ) for s in symbols
        }
        self.selected = set()
        self.orders_sent = []
        self.positions = []
        self.deals = []
        self._next_ticket = 1001
        self._rates_calls = []

    def initialize(self, path=None):
        self.initialized = True
        return True

    def shutdown(self):
        self.initialized = False

    def last_error(self):
        return (0, "OK")

    def account_info(self):
        return SimpleNamespace(
            login=5012345, server="Broker-Demo", currency="USD",
            balance=self.equity, equity=self.equity, profit=0.0,
            margin_free=self.equity, leverage=100,
            trade_mode=self.trade_mode, trade_allowed=True,
        )

    def terminal_info(self):
        return SimpleNamespace(trade_allowed=self.algo_allowed,
                               tradeapi_disabled=self.tradeapi_disabled)

    def symbol_info(self, name):
        return self._symbols.get(name)

    def symbols_get(self):
        return list(self._symbols.values())

    def symbol_select(self, name, enable):
        if name in self._symbols:
            self.selected.add(name)
            return True
        return False

    def symbol_info_tick(self, name):
        if name not in self._symbols:
            return None
        return SimpleNamespace(bid=self.bid, ask=self.ask, time=int(datetime.now(timezone.utc).timestamp()))

    def copy_rates_from_pos(self, symbol, tf, start_pos, count):
        self._rates_calls.append((symbol, tf, start_pos, count))
        t0 = 1735689600  # 2025-01-01 00:00:00 UTC
        step = 3600 if tf == self.TIMEFRAME_H1 else 900
        rows = []
        for i in range(count):
            p = 3300.0 + i * 0.5
            rows.append((t0 + i * step, p, p + 2.0, p - 1.5, p + 0.4, 250, 10, 0))
        dtype = np.dtype([
            ("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"),
            ("close", "f8"), ("tick_volume", "i8"), ("spread", "i4"), ("real_volume", "i8"),
        ])
        return np.array(rows, dtype=dtype)

    def positions_get(self):
        return list(self.positions)

    def history_deals_get(self, dt_from, dt_to):
        return list(self.deals)

    def order_send(self, req):
        self.orders_sent.append(dict(req))
        action = req["action"]
        if action == self.TRADE_ACTION_DEAL:
            # Close existing position
            if "position" in req:
                tk = int(req["position"])
                self.positions = [p for p in self.positions if p.ticket != tk]
                return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, order=tk, comment="closed")
            tk = self._next_ticket
            self._next_ticket += 1
            pos_type = self.POSITION_TYPE_BUY if req["type"] == self.ORDER_TYPE_BUY else self.POSITION_TYPE_SELL
            self.positions.append(SimpleNamespace(
                ticket=tk, symbol=req["symbol"], type=pos_type,
                volume=req["volume"], price_open=req["price"],
                price_current=req["price"], sl=req["sl"], tp=req["tp"],
                profit=0.0, magic=req["magic"], comment=req["comment"],
            ))
            return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, order=tk, deal=tk, comment="done")
        if action == self.TRADE_ACTION_SLTP:
            tk = int(req["position"])
            for p in self.positions:
                if p.ticket == tk:
                    p.sl = req["sl"]
                    p.tp = req["tp"]
            return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, order=tk, comment="sltp modified")
        return SimpleNamespace(retcode=10013, comment="invalid")


def test_resolve_mt5_symbol_autodetects_gold_variant():
    fake = FakeMT5(symbols=("EURUSD", "XAUUSDm", "BTCUSD"))
    sym = resolve_mt5_symbol(fake, "")
    assert sym == "XAUUSDm"
    assert "XAUUSDm" in fake.selected
    # PAXGUSDT juga otomatis dipetakan ke simbol emas broker
    assert resolve_mt5_symbol(fake, "PAXGUSDT") == "XAUUSDm"


def test_fetch_mt5_candles_skips_forming_bar_and_formats_df():
    fake = FakeMT5(symbols=("XAUUSD",))
    df = fetch_mt5_candles(fake, "XAUUSD", "1h", bars=10)
    assert fake._rates_calls[-1][2] == 1, "start_pos harus 1 (lewati candle berjalan)"
    assert list(df.columns) == ["open", "high", "low", "close", "volume", "close_time"]
    assert str(df.index.tz) == "UTC"
    assert len(df) == 10


def test_executor_starts_standby_and_blocks_orders_until_armed():
    fake = FakeMT5()
    ex = MT5Executor(mt5_mod=fake)
    assert ex.armed is False
    ok, _ = ex.connect()
    assert ok is True
    row = {"signal": 1, "entry": 3340.0, "sl": 3330.0, "tp1": 3360.0, "tp2": 3375.0}
    assert ex.execute_signal("PAXGUSDT", row, sig_time="2026-11-27T01:00:00+00:00") is None
    assert len(fake.orders_sent) == 0


def test_demo_only_guard_rejects_real_account():
    fake = FakeMT5(trade_mode=FakeMT5.ACCOUNT_TRADE_MODE_REAL)
    ex = MT5Executor(settings={"mt5_demo_only": True}, mt5_mod=fake)
    ok_arm, msg = ex.set_armed(True)
    assert ok_arm is False
    assert "DEMO" in msg
    assert ex.armed is False


def test_split_orders_50_50_relative_levels_and_auto_be():
    # Feed PAXGUSDT di 3355.0 (selisih +$15 dari broker XAUUSDm di 3340.20)
    # Jarak SL=10.0, TP1=20.0, TP2=35.0 -> harus dijangkar ke Ask broker 3340.20!
    fake = FakeMT5(symbols=("XAUUSDm",), equity=10000.0, bid=3340.00, ask=3340.20)
    ex = MT5Executor(settings={
        "mt5_risk_pct": 1.0,        # $100 total risiko; loss/lot @ SL 10.0 = (10/0.01)*1 = $1000 -> total 0.10 lot -> 0.05 + 0.05
        "mt5_max_open_trades": 1,
        "mt5_max_spread_points": 40,
        "mt5_be_offset_points": 10,  # +0.10 di atas entry saat BE
    }, mt5_mod=fake)
    ok_arm, _ = ex.set_armed(True)
    assert ok_arm is True

    row = {"signal": 1, "entry": 3355.0, "sl": 3345.0, "tp1": 3375.0, "tp2": 3390.0}
    res = ex.execute_signal("PAXGUSDT", row, sig_time="2026-11-27T02:00:00+00:00")
    assert res is not None
    assert len(res["tickets"]) == 2
    assert res["tickets"][0]["volume"] == pytest.approx(0.05)
    assert res["tickets"][1]["volume"] == pytest.approx(0.05)
    # Level terjangkar ke Ask MT5 3340.20
    assert res["sl"] == pytest.approx(3330.20)
    assert res["tp1"] == pytest.approx(3360.20)
    assert res["tp2"] == pytest.approx(3375.20)
    assert len(fake.positions) == 2

    # Sinyal duplikat di bar yang sama harus diabaikan
    assert ex.execute_signal("PAXGUSDT", row, sig_time="2026-11-27T02:00:00+00:00") is None

    # Simulasikan harga naik menyentuh TP1 & leg TP1 tertutup -> Auto-BE geser SL leg TP2
    t1 = res["tickets"][0]["ticket"]
    t2 = res["tickets"][1]["ticket"]
    fake.positions = [p for p in fake.positions if p.ticket != t1]
    fake.bid, fake.ask = 3360.50, 3360.70

    be_events = ex.check_breakeven()
    assert len(be_events) == 1
    p2 = next(p for p in fake.positions if p.ticket == t2)
    assert p2.sl == pytest.approx(3340.30)  # entry 3340.20 + 10 points (0.10)
    # Panggilan ulang idempoten
    assert ex.check_breakeven() == []


def test_spread_max_open_and_daily_stop_guards_user_configurable():
    fake = FakeMT5(equity=10000.0, bid=3340.00, ask=3340.60)  # spread = 60 points
    ex = MT5Executor(settings={"mt5_max_spread_points": 30, "mt5_daily_stop_r": 1.0, "mt5_risk_pct": 1.0}, mt5_mod=fake)
    ex.set_armed(True)
    row = {"signal": 1, "entry": 3340.0, "sl": 3330.0, "tp1": 3355.0, "tp2": 3370.0}

    # Ditolak karena spread 60 > 30
    assert ex.execute_signal("XAUUSDm", row, sig_time="t1") is None

    # User menaikkan batas spread sendiri -> lolos bila belum daily stop
    ex.update_settings({"mt5_max_spread_points": 80})
    # Simulasikan sudah rugi -$120 hari ini (> 1R = $100) -> ditolak daily stop
    fake.deals = [SimpleNamespace(magic=ex.settings["mt5_magic"], profit=-120.0, swap=0.0, commission=0.0)]
    assert ex.execute_signal("XAUUSDm", row, sig_time="t2") is None

    # User menaikkan daily stop jadi 2R -> lolos
    ex.update_settings({"mt5_daily_stop_r": 2.0})
    res = ex.execute_signal("XAUUSDm", row, sig_time="t3")
    assert res is not None
    assert len(fake.positions) == 2

    # Close all bekerja
    n_closed, errs = ex.close_all()
    assert n_closed == 2 and errs == []
    assert len(fake.positions) == 0


def test_mt5_multifeed_seed_and_poll():
    fake = FakeMT5(symbols=("XAUUSDm",))
    mf = MT5MultiFeed(symbol="", intervals=("15m", "1h"), buffer_bars=20, mt5_mod=fake)
    bufs = mf.seed()
    assert mf.symbol == "XAUUSDm"
    assert set(bufs.keys()) == {"15m", "1h"}
    assert len(bufs["1h"]) == 20


def test_python_tradeapi_flag_controls_arm_not_mql_trade_allowed():
    fake = FakeMT5()
    fake.algo_allowed = False       # MQL EA flag bisa False di sebagian build MT5
    fake.tradeapi_disabled = False  # Python API tetap diizinkan
    ex = MT5Executor(mt5_mod=fake)
    ok, _ = ex.set_armed(True)
    assert ok is True and ex.armed is True

    fake.tradeapi_disabled = True   # Python API diblokir di Options MT5 -> harus ditolak
    ok2, msg2 = ex.set_armed(True)
    assert ok2 is False and ex.armed is False and "Python Trade API" in msg2


def test_apply_settings_preserves_sensitivity_with_exit_protocol():
    from backend.run import AppState
    from backend.confluence_engine import Config
    from backend.testnet_executor import TestnetExecutor

    st = AppState("PAXGUSDT", Config(htf="15m"), TestnetExecutor(mode="off"), label="XAUUSD")
    st.apply_settings({"sensitivity": "aggressive", "exit_protocol": "nitro"})
    assert st.cfg.min_confluence == 1
    assert st.cfg.tp1_rr == 1.0 and st.cfg.tp2_rr == 1.5
    # Mengubah exit_protocol saja tidak boleh mereset min_confluence milik aggressive
    st.apply_settings({"exit_protocol": "quick"})
    assert st.cfg.min_confluence == 1
    assert st.cfg.tp1_rr == 1.5 and st.cfg.tp2_rr == 3.0


def test_mt5_mcp_calendar_news_and_high_impact_guard():
    from backend.mt5_mcp import MT5McpClient
    from backend.news_feed import NewsFeed

    class StubMcp(MT5McpClient):
        def __init__(self):
            super().__init__(url="http://127.0.0.1:22346/mcp", api_key="secret-key")
        def call_tool(self, name, arguments=None, _retry=True):
            if name == "get_time_information":
                return {"utc_time": "2026-11-27T12:00:00Z", "server_time": "2026-11-27T14:00:00"}
            if name == "economic_calendar_list_events_by_currency":
                return {"ok": True, "events": [
                    {"id": "84001", "name": "Core PCE Price Index m/m", "importance": "High importance", "source_url": "https://bea.gov"},
                    {"id": "84002", "name": "Minor Index", "importance": "Low importance", "source_url": ""},
                ]}
            if name == "economic_calendar_list_values":
                return {"ok": True, "values": [
                    {"id": "1", "event_id": "84001", "time": "2026-11-27T15:30:00", "forecast_value": 0.3, "prev_value": 0.2},
                    {"id": "2", "event_id": "84002", "time": "2026-11-27T15:30:00"},
                ]}
            if name == "get_news_list":
                return {"ok": True, "news": []}
            return {}

    nf = NewsFeed(ttl=60, mcp_client=StubMcp())
    items = nf.refresh(force=True)
    assert len(items) == 1
    assert items[0]["provider"] == "mt5-cal"
    assert items[0]["score"] == 6
    assert "Core PCE" in items[0]["title"]
    # 15:30 waktu server (UTC+2) -> 13:30 UTC; sinyal jam 13:45 UTC harus tertahan news guard
    hit = nf.high_impact_near("2026-11-27T13:45:00+00:00", window_min=30)
    assert hit is not None and "Core PCE" in hit["title"]
    assert nf.high_impact_near("2026-11-27T16:00:00+00:00", window_min=30) is None


def test_mt5_mcp_adapter_and_key_masking():
    from backend.mt5_mcp import MT5McpAdapter, MT5McpClient

    class StubMcp(MT5McpClient):
        def __init__(self):
            super().__init__(url="http://127.0.0.1:22346/mcp", api_key="super-secret-token")
            self.calls = []
        def initialize(self):
            return {"serverInfo": {"name": "MetaTrader 5 MCP"}}
        def call_tool(self, name, arguments=None, _retry=True):
            self.calls.append((name, arguments))
            if name == "terminal_get_info":
                return {"ok": True, "terminal": {"build": 6361, "mcp_trade_allowed": True, "connection": {"state": "synchronized"}}}
            if name == "get_trading_account_info":
                return {"account": {"login": "5012345", "server": "MetaQuotes-Demo", "type": "demo", "balance": 100000, "equity": 100000, "currency": "USD", "read_only": False}}
            if name == "get_marketwatch_symbols":
                return {"symbols": [{"symbol": "XAUUSD", "selected": True, "digits": 2, "point": 0.01, "tick_size": 0.01, "tick_value": 0.0, "contract_size": 100.0, "currency_profit": "USD", "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01, "bid": 3650.0, "ask": 3650.2}]}
            if name == "add_marketwatch_symbol":
                return {"ok": True}
            if name == "get_trading_open_positions":
                return {"positions": [], "orders": []}
            if name == "get_trading_history_positions":
                return {"positions": []}
            if name == "trade_send_market_order":
                return {"ok": True, "order": 7701, "comment": "Done"}
            return {}

    stub = StubMcp()
    adapter = MT5McpAdapter(client=stub)
    ex = MT5Executor(settings={"mt5_transport": "mcp", "mt5_mcp_url": "http://127.0.0.1:22346/mcp", "mt5_mcp_key": "super-secret-token"}, mt5_mod=adapter)
    ok, _ = ex.set_armed(True)
    assert ok is True
    # Key mentah tidak boleh bocor di status()
    st = ex.status()
    assert st["settings"]["mt5_mcp_key"] == ""
    assert st["settings"]["mt5_mcp_key_set"] is True
    # Update setting lain tanpa mengirim key -> key lama tetap terjaga
    ex.update_settings({"mt5_risk_pct": 0.5, "mt5_mcp_key": ""})
    assert ex.settings["mt5_mcp_key"] == "super-secret-token"
    # Order via MCP mengirim split 2x trade_send_market_order
    row = {"signal": 1, "entry": 3650.0, "sl": 3640.0, "tp1": 3665.0, "tp2": 3680.0}
    res = ex.execute_signal("XAUUSD", row, sig_time="2026-11-28T01:00:00+00:00")
    assert res is not None and len(res["tickets"]) == 2
    sent_orders = [c for c in stub.calls if c[0] == "trade_send_market_order"]
    assert len(sent_orders) == 2 and sent_orders[0][1]["type"] == "buy"


def test_dynamic_price_digits_and_multi_pair_papersim():
    from backend.godmode_engine import price_digits
    from backend.paper_sim import PaperSim
    import pandas as pd

    assert price_digits(1.09342) == 5    # EURUSD / GBPUSD
    assert price_digits(159.559) == 3    # USDJPY / XAGUSD
    assert price_digits(3652.84) == 2    # XAUUSD / US500

    # Pastikan PaperSim menjaga presisi 5 desimal pada EURUSD (tidak terbulatkan ke 1.09!)
    sim = PaperSim(path=None, default_symbol="EURUSD")
    sim.persist_enabled = False
    sim.queue_signal(pd.Timestamp("2026-01-01 00:00:00"), 1, 1.09342, 1.09142, 1.09542, 1.09742, mode="aggressive", kind="GOLDEN")
    sim.on_candle(pd.Timestamp("2026-01-01 00:05:00"), 1.09345, 1.09400, 1.09300, 1.09380)
    tr = sim.snapshot_list(5)[0]
    assert tr["symbol"] == "EURUSD"
    assert tr["entry"] == pytest.approx(1.09345)
    assert tr["sl"] == pytest.approx(1.09145)
    assert tr["risk"] == pytest.approx(0.002)
    assert tr["units"] > 0 and tr["risk_usd"] == pytest.approx(100.0)
