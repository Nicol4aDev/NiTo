"""Broker simulato: nessun ordine reale, capitale virtuale persistito su SQLite."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ..models import Signal, Trade, TradeStatus
from ..simulation import close_trade, step
from ..storage import Store
from .base import Broker, BrokerError, BrokerEvent

PriceFn = Callable[[str], Awaitable[float]]


class PaperBroker(Broker):
    name = "paper"

    def __init__(self, store: Store, start_equity: float, fee_pct: float, price: PriceFn):
        self.store = store
        self.fee_pct = fee_pct
        self.price = price
        if not store.get_state("paper_equity"):
            store.set_state("paper_equity", repr(start_equity))

    async def equity(self) -> float:
        return float(self.store.get_state("paper_equity"))

    def _book(self, pnl: float) -> None:
        self.store.set_state("paper_equity", repr(float(self.store.get_state("paper_equity")) + pnl))

    async def open_trade(self, signal: Signal, qty: float, now_ms: int) -> Trade:
        if qty <= 0:
            raise BrokerError("quantità nulla")
        return Trade(
            symbol=signal.symbol,
            side=signal.side,
            qty=qty,
            entry=signal.entry,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            break_even_trigger=signal.break_even_trigger,
            opened_at=now_ms,
            signal_id=signal.id,
        )

    async def refresh(self, trade: Trade, now_ms: int) -> list[BrokerEvent]:
        last = await self.price(trade.symbol)
        events = step(trade, last, last, last, now_ms, self.fee_pct)
        if trade.status is TradeStatus.CLOSED:
            self._book(trade.pnl or 0.0)
            return [BrokerEvent("closed")]
        if "break_even" in events:
            return [BrokerEvent("break_even")]
        return []

    async def close_trade(self, trade: Trade, reason: str, now_ms: int) -> None:
        last = await self.price(trade.symbol)
        close_trade(trade, last, now_ms, reason, self.fee_pct)
        self._book(trade.pnl or 0.0)
