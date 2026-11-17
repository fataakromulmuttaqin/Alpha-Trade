"""
Gerbang risiko sebelum order dikirim (dipakai testnet_executor.execute_signal).

Aturan (semua opsional, default longgar):
  - max_daily_dd_pct   : stop entry baru jika equity turun > X% dari awal hari (UTC).
  - max_open_positions : maksimum posisi terbuka bersamaan.
  - cooldown_bars      : jeda (bar) setelah N loss berturut-turut.

Stateless terhadap broker: state diisi lewat on_trade_closed() dan begin_day().
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class RiskConfig:
    max_daily_dd_pct: float = 3.0
    max_open_positions: int = 1
    cooldown_after_losses: int = 2
    cooldown_bars: int = 10


@dataclass
class RiskState:
    day: str = ""
    day_start_equity: float = 0.0
    open_positions: int = 0
    consec_losses: int = 0
    cooldown_until_bar: int = -1
    bar: int = 0
    log: list = field(default_factory=list)


class RiskGate:
    def __init__(self, cfg=RiskConfig()):
        self.cfg = cfg
        self.st = RiskState()

    def tick(self):
        self.st.bar += 1

    def begin_day(self, equity, now=None):
        d = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
        if d != self.st.day:
            self.st.day = d
            self.st.day_start_equity = equity

    def check(self, equity):
        """Kembalikan (boleh, alasan). Dipanggil sebelum setiap entry baru."""
        st, cfg = self.st, self.cfg
        if st.open_positions >= cfg.max_open_positions:
            return False, f"posisi terbuka {st.open_positions} >= {cfg.max_open_positions}"
        if st.day_start_equity > 0:
            dd = (equity / st.day_start_equity - 1) * 100
            if dd <= -cfg.max_daily_dd_pct:
                return False, f"DD harian {dd:.2f}% <= -{cfg.max_daily_dd_pct}%"
        if st.bar < st.cooldown_until_bar:
            return False, f"cooldown sampai bar {st.cooldown_until_bar}"
        return True, "ok"

    def on_entry(self):
        self.st.open_positions += 1

    def on_trade_closed(self, pnl):
        st, cfg = self.st, self.cfg
        st.open_positions = max(0, st.open_positions - 1)
        if pnl < 0:
            st.consec_losses += 1
            if st.consec_losses >= cfg.cooldown_after_losses:
                st.cooldown_until_bar = st.bar + cfg.cooldown_bars
                st.consec_losses = 0
        else:
            st.consec_losses = 0
