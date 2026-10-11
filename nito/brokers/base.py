from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..models import Signal, Trade


class BrokerError(Exception):
    """Errore che impedisce di aprire o gestire una posizione."""


@dataclass
class BrokerEvent:
    kind: str  # "closed" | "break_even" | "warning"
    message: str = ""


class Broker(ABC):
    name = "base"

    @abstractmethod
    async def equity(self) -> float: ...

    @abstractmethod
    async def open_trade(self, signal: Signal, qty: float, now_ms: int) -> Trade:
        """Apre la posizione CON stop loss già attivo, oppure solleva BrokerError."""

    @abstractmethod
    async def refresh(self, trade: Trade, now_ms: int) -> list[BrokerEvent]:
        """Aggiorna la posizione (chiusure, break-even). Modifica `trade` sul posto."""

    @abstractmethod
    async def close_trade(self, trade: Trade, reason: str, now_ms: int) -> None:
        """Chiude subito la posizione a mercato."""

    async def reconcile(self, open_trades: list[Trade]) -> list[str]:
        """Controlli all'avvio; restituisce eventuali avvisi."""
        return []
