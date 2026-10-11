"""Strutture dati condivise da tutto il bot."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> int:
        return 1 if self is Side.LONG else -1

    @property
    def entry_order_side(self) -> str:
        return "buy" if self is Side.LONG else "sell"

    @property
    def exit_order_side(self) -> str:
        return "sell" if self is Side.LONG else "buy"


@dataclass(frozen=True)
class Candle:
    ts: int  # apertura della candela, millisecondi UTC
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @classmethod
    def from_ccxt(cls, row: list) -> "Candle":
        ts, o, h, l, c, v = row[:6]
        return cls(int(ts), float(o), float(h), float(l), float(c), float(v or 0.0))


@dataclass(frozen=True)
class DailyLevels:
    """Trigger point giornalieri: apertura di oggi, massimo e minimo di ieri."""

    today_open: float | None = None
    prev_high: float | None = None
    prev_low: float | None = None


@dataclass(frozen=True)
class Setup:
    name: str
    side: Side
    reason: str


@dataclass
class Signal:
    symbol: str
    timeframe: str
    side: Side
    setups: list[Setup]
    entry: float
    stop_loss: float
    take_profit: float
    break_even_trigger: float | None
    candle_ts: int
    trend_up: bool
    fee_pct: float
    id: int | None = None

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry - self.stop_loss)

    @property
    def reward_per_unit(self) -> float:
        return abs(self.take_profit - self.entry)

    @property
    def reward_risk(self) -> float:
        return self.reward_per_unit / self.risk_per_unit

    @property
    def net_reward_risk(self) -> float:
        """R:R al netto delle commissioni di ingresso e uscita."""
        fees = self.entry * self.fee_pct / 100 * 2
        return (self.reward_per_unit - fees) / (self.risk_per_unit + fees)

    @property
    def setup_names(self) -> str:
        return " + ".join(s.name for s in self.setups)


class TradeStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    ABORTED = "aborted"  # entrata annullata perché lo SL non è stato piazzato


@dataclass
class Trade:
    symbol: str
    side: Side
    qty: float
    entry: float
    stop_loss: float
    take_profit: float
    break_even_trigger: float | None
    opened_at: int
    signal_id: int | None = None
    status: TradeStatus = TradeStatus.OPEN
    moved_to_be: bool = False
    exit_price: float | None = None
    closed_at: int | None = None
    pnl: float | None = None
    exit_reason: str | None = None
    initial_stop: float | None = None
    sl_order_id: str | None = None
    tp_order_id: str | None = None
    id: int | None = None
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.initial_stop is None:
            self.initial_stop = self.stop_loss

    @property
    def initial_risk(self) -> float:
        return abs(self.entry - (self.initial_stop or self.stop_loss)) * self.qty

    @property
    def r_multiple(self) -> float | None:
        if self.pnl is None or self.initial_risk == 0:
            return None
        return self.pnl / self.initial_risk
