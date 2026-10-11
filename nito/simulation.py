"""Gestione simulata di una posizione: usata dal paper trading e dal backtest."""

from __future__ import annotations

from .models import Trade, TradeStatus


def break_even_price(trade: Trade, fee_pct: float) -> float:
    """Stop a pareggio che copre anche le commissioni di andata e ritorno."""
    return trade.entry * (1 + trade.side.sign * 2 * fee_pct / 100)


def trade_pnl(trade: Trade, exit_price: float, fee_pct: float) -> float:
    gross = (exit_price - trade.entry) * trade.side.sign * trade.qty
    fees = (trade.entry + exit_price) * trade.qty * fee_pct / 100
    return gross - fees


def close_trade(trade: Trade, price: float, ts: int, reason: str, fee_pct: float) -> None:
    trade.exit_price = price
    trade.closed_at = ts
    trade.exit_reason = reason
    trade.pnl = trade_pnl(trade, price, fee_pct)
    trade.status = TradeStatus.CLOSED


def step(
    trade: Trade, open_: float, high: float, low: float, ts: int, fee_pct: float
) -> list[str]:
    """Applica una candela (o un tick con open=high=low) alla posizione.

    Ordine conservativo: prima lo stop, poi il target, infine lo spostamento a
    break-even (che vale dalla candela successiva). Se il prezzo apre oltre lo
    stop (gap) l'uscita avviene all'apertura, non al prezzo dello stop.
    Restituisce gli eventi: "stop", "target", "break_even".
    """
    if trade.status is not TradeStatus.OPEN:
        return []
    long = trade.side.sign > 0
    sl, tp = trade.stop_loss, trade.take_profit

    stop_hit = low <= sl if long else high >= sl
    if stop_hit:
        price = min(sl, open_) if long else max(sl, open_)
        close_trade(trade, price, ts, "be" if trade.moved_to_be else "sl", fee_pct)
        return ["stop"]

    target_hit = high >= tp if long else low <= tp
    if target_hit:
        price = max(tp, open_) if long else min(tp, open_)
        close_trade(trade, price, ts, "tp", fee_pct)
        return ["target"]

    be = trade.break_even_trigger
    if be is not None and not trade.moved_to_be:
        reached = high >= be if long else low <= be
        if reached:
            new_stop = break_even_price(trade, fee_pct)
            trade.stop_loss = min(new_stop, be) if long else max(new_stop, be)
            trade.moved_to_be = True
            return ["break_even"]
    return []
