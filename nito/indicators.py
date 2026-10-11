"""Indicatori tecnici in Python puro (nessuna dipendenza esterna).

Tutti i calcoli sono causali: il valore all'indice i dipende solo dalle
candele 0..i, quindi si possono precalcolare sull'intera serie anche nel
backtest senza introdurre lookahead.
"""

from __future__ import annotations

from collections.abc import Sequence


def ema(values: Sequence[float], period: int) -> list[float | None]:
    """Media mobile esponenziale, inizializzata con la SMA dei primi `period` valori.

    Restituisce None finché non ci sono abbastanza dati.
    """
    if period < 1:
        raise ValueError("period deve essere >= 1")
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def true_range(high: Sequence[float], low: Sequence[float], close: Sequence[float]) -> list[float]:
    tr: list[float] = []
    for i in range(len(high)):
        if i == 0:
            tr.append(high[i] - low[i])
        else:
            pc = close[i - 1]
            tr.append(max(high[i] - low[i], abs(high[i] - pc), abs(low[i] - pc)))
    return tr


def atr(
    high: Sequence[float], low: Sequence[float], close: Sequence[float], period: int = 14
) -> list[float | None]:
    """Average True Range con smoothing di Wilder."""
    tr = true_range(high, low, close)
    out: list[float | None] = [None] * len(tr)
    if len(tr) < period:
        return out
    prev = sum(tr[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(tr)):
        prev = (prev * (period - 1) + tr[i]) / period
        out[i] = prev
    return out
