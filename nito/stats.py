"""Statistiche sulle operazioni chiuse (usate da /stats e dal backtest)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .models import Trade


@dataclass
class Stats:
    trades: int
    wins: int
    losses: int
    breakeven: int
    win_rate: float
    total_pnl: float
    avg_r: float
    profit_factor: float | None
    max_drawdown_pct: float

    @property
    def profit_factor_text(self) -> str:
        if self.profit_factor is None:
            return "-"
        return "∞" if self.profit_factor == float("inf") else f"{self.profit_factor:.2f}"

    def as_lines(self, currency: str = "USDT") -> list[str]:
        if self.trades == 0:
            return ["Nessuna operazione chiusa."]
        pf = self.profit_factor_text
        return [
            f"Operazioni: {self.trades} (✅ {self.wins} · 🛑 {self.losses} · ⚪ {self.breakeven})",
            f"Win rate: {self.win_rate:.1f}%",
            f"P&L totale: {self.total_pnl:+.2f} {currency}",
            f"Media per operazione: {self.avg_r:+.2f}R",
            f"Profit factor: {pf}",
            f"Max drawdown: {self.max_drawdown_pct:.2f}%",
        ]


def compute_stats(trades: Sequence[Trade], start_equity: float) -> Stats:
    closed = sorted((t for t in trades if t.pnl is not None), key=lambda t: t.closed_at or 0)
    eps = 1e-9
    wins = [t for t in closed if (t.r_multiple or 0) > 0.05]
    losses = [t for t in closed if (t.r_multiple or 0) < -0.05]
    be = len(closed) - len(wins) - len(losses)
    gross_win = sum(t.pnl for t in closed if t.pnl > 0)  # type: ignore[operator]
    gross_loss = -sum(t.pnl for t in closed if t.pnl < 0)  # type: ignore[operator]
    rs = [t.r_multiple for t in closed if t.r_multiple is not None]

    equity = peak = start_equity
    max_dd = 0.0
    for t in closed:
        equity += t.pnl  # type: ignore[operator]
        peak = max(peak, equity)
        if peak > eps:
            max_dd = max(max_dd, (peak - equity) / peak * 100)

    return Stats(
        trades=len(closed),
        wins=len(wins),
        losses=len(losses),
        breakeven=be,
        win_rate=len(wins) / len(closed) * 100 if closed else 0.0,
        total_pnl=sum(t.pnl for t in closed),  # type: ignore[misc]
        avg_r=sum(rs) / len(rs) if rs else 0.0,
        profit_factor=(
            gross_win / gross_loss if gross_loss > eps else (float("inf") if gross_win > eps else None)
        ),
        max_drawdown_pct=max_dd,
    )
