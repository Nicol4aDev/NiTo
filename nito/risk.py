"""Gestione del rischio: dimensionamento e blocchi di sicurezza."""

from __future__ import annotations

from datetime import datetime, timezone

from .config import RiskConfig
from .models import Trade
from .storage import Store


def position_size(
    equity: float,
    risk_pct: float,
    entry: float,
    stop: float,
    fee_pct: float,
    max_leverage: float,
) -> float:
    """Quantità (in unità della crypto base) tale che lo stop costi `risk_pct` del capitale.

    Le commissioni di ingresso e uscita sono incluse nel rischio. Il nozionale
    non supera mai equity × max_leverage.
    """
    if equity <= 0 or entry <= 0:
        return 0.0
    per_unit = abs(entry - stop) + entry * fee_pct / 100 * 2
    if per_unit <= 0:
        return 0.0
    qty = equity * risk_pct / 100 / per_unit
    return min(qty, equity * max_leverage / entry)


def utc_day(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


class RiskGuard:
    """Blocchi che impediscono nuove operazioni. Lo stato è persistito su SQLite,
    quindi un riavvio del bot non azzera kill switch e perdita giornaliera."""

    def __init__(self, cfg: RiskConfig, store: Store):
        self.cfg = cfg
        self.store = store

    # --- kill switch manuale / automatico ---
    @property
    def kill_reason(self) -> str:
        return self.store.get_state("kill_switch", "")

    def kill(self, reason: str) -> None:
        self.store.set_state("kill_switch", reason)

    def resume(self) -> None:
        self.store.set_state("kill_switch", "")
        self.store.set_state("consecutive_losses", "0")

    @property
    def paused(self) -> bool:
        return self.store.get_state("paused", "0") == "1"

    def set_paused(self, value: bool) -> None:
        self.store.set_state("paused", "1" if value else "0")

    # --- perdita giornaliera ---
    @property
    def daily_halt(self) -> str:
        return self.store.get_state("daily_halt", "")

    @property
    def day_start_equity(self) -> float | None:
        raw = self.store.get_state("day_start_equity", "")
        return float(raw) if raw else None

    def update_equity(self, equity: float, now_ms: int) -> str | None:
        """Aggiorna giorno e perdita giornaliera. Restituisce un messaggio se scatta un blocco."""
        today = utc_day(now_ms)
        if self.store.get_state("day", "") != today:
            self.store.set_state("day", today)
            self.store.set_state("day_start_equity", repr(equity))
            self.store.set_state("daily_halt", "")
            return None
        start = self.day_start_equity
        if start and not self.daily_halt:
            loss_pct = (start - equity) / start * 100
            if loss_pct >= self.cfg.max_daily_loss_pct:
                reason = (
                    f"perdita giornaliera {loss_pct:.2f}% >= {self.cfg.max_daily_loss_pct}%: "
                    "nuove operazioni bloccate fino a domani (UTC)"
                )
                self.store.set_state("daily_halt", reason)
                return reason
        return None

    # --- perdite consecutive ---
    @property
    def consecutive_losses(self) -> int:
        return int(self.store.get_state("consecutive_losses", "0") or 0)

    def on_trade_closed(self, trade: Trade) -> str | None:
        if trade.pnl is None:
            return None
        losses = self.consecutive_losses + 1 if trade.pnl < 0 else 0
        self.store.set_state("consecutive_losses", str(losses))
        if self.cfg.max_consecutive_losses and losses >= self.cfg.max_consecutive_losses:
            reason = f"{losses} perdite consecutive: bot in pausa, usa /resume dopo aver controllato"
            self.kill(reason)
            return reason
        return None

    # --- cooldown per simbolo ---
    def in_cooldown(self, symbol: str, now_ms: int) -> bool:
        raw = self.store.get_state(f"last_signal:{symbol}", "")
        if not raw:
            return False
        return now_ms - int(raw) < self.cfg.cooldown_minutes * 60_000

    def mark_signal(self, symbol: str, now_ms: int) -> None:
        self.store.set_state(f"last_signal:{symbol}", str(now_ms))

    # --- controllo complessivo ---
    def block_reason(self, symbol: str, open_trades: list[Trade]) -> str | None:
        if self.kill_reason:
            return f"kill switch attivo ({self.kill_reason})"
        if self.daily_halt:
            return self.daily_halt
        if any(t.symbol == symbol for t in open_trades):
            return f"posizione già aperta su {symbol}"
        if len(open_trades) >= self.cfg.max_open_positions:
            return f"raggiunto il massimo di {self.cfg.max_open_positions} posizioni aperte"
        return None
