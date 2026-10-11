"""Trigger point giornalieri (apertura di oggi, massimo/minimo di ieri).

Il giorno è quello UTC, come la candela giornaliera degli exchange crypto.
"""

from __future__ import annotations

from collections.abc import Sequence

from .models import Candle, DailyLevels

DAY_MS = 86_400_000


def day_start(ts_ms: int) -> int:
    return ts_ms - ts_ms % DAY_MS


def levels_from_intraday(candles: Sequence[Candle]) -> list[DailyLevels]:
    """Livelli disponibili *al momento* di ogni candela intraday (nessun lookahead).

    Un giorno conta solo se la serie contiene la sua prima candela (quella che
    apre a mezzanotte UTC): con un giorno parziale apertura e massimi/minimi
    sarebbero sbagliati, quindi il livello resta None.
    """
    out: list[DailyLevels] = []
    current_day: int | None = None
    complete = False
    today_open: float | None = None
    day_high = day_low = 0.0
    prev_high: float | None = None
    prev_low: float | None = None

    for c in candles:
        d = day_start(c.ts)
        if d != current_day:
            if current_day is not None and complete and d - current_day == DAY_MS:
                prev_high, prev_low = day_high, day_low
            else:
                prev_high = prev_low = None
            current_day = d
            complete = c.ts == d
            today_open = c.open
            day_high, day_low = c.high, c.low
        else:
            day_high = max(day_high, c.high)
            day_low = min(day_low, c.low)
        out.append(DailyLevels(today_open if complete else None, prev_high, prev_low))
    return out


def levels_from_daily(daily: Sequence[Candle], now_ms: int) -> DailyLevels:
    """Livelli a partire dalle candele giornaliere dell'exchange."""
    today = day_start(now_ms)
    by_day = {c.ts: c for c in daily}
    cur = by_day.get(today)
    prev = by_day.get(today - DAY_MS)
    return DailyLevels(
        today_open=cur.open if cur else None,
        prev_high=prev.high if prev else None,
        prev_low=prev.low if prev else None,
    )
