"""Costruzione del segnale: entrata, Stop Loss strutturale e Take Profit a R multiplo."""

from __future__ import annotations

from dataclasses import replace

from .config import RiskConfig
from .models import Setup, Side, Signal
from .strategy import Series


def build_signal(
    s: Series,
    i: int,
    side: Side,
    setups: list[Setup],
    risk: RiskConfig,
    symbol: str,
    timeframe: str,
) -> tuple[Signal | None, str]:
    """Restituisce (segnale, "") oppure (None, motivo dello scarto).

    - SL: minimo della candela di ingresso (o dello swing recente) meno un
      buffer in ATR, come suggerito nelle descrizioni del metodo.
    - SL troppo stretto viene allargato a `min_sl_pct` (altrimenti rumore e
      commissioni lo colpiscono quasi sempre); SL troppo largo scarta il segnale.
    - TP = entrata ± rischio × `reward_risk`.
    """
    entry = s.close[i]
    sign = side.sign
    atr_now = s.atr[i] or 0.0
    buffer = risk.sl_atr_buffer * atr_now

    if risk.sl_mode == "swing":
        start = max(0, i - risk.swing_lookback + 1)
        ref = min(s.low[start : i + 1]) if side is Side.LONG else max(s.high[start : i + 1])
    else:
        ref = s.low[i] if side is Side.LONG else s.high[i]
    stop = ref - sign * buffer

    dist = (entry - stop) * sign
    min_dist = entry * risk.min_sl_pct / 100
    if dist < min_dist:
        stop = entry - sign * min_dist
        dist = min_dist
    if dist > entry * risk.max_sl_pct / 100:
        return None, f"SL troppo largo: {dist / entry * 100:.2f}% > {risk.max_sl_pct}%"

    target = entry + sign * dist * risk.reward_risk
    be = None
    if 0 < risk.break_even_at_r < risk.reward_risk:
        be = entry + sign * dist * risk.break_even_at_r

    signal = Signal(
        symbol=symbol,
        timeframe=timeframe,
        side=side,
        setups=setups,
        entry=entry,
        stop_loss=stop,
        take_profit=target,
        break_even_trigger=be,
        candle_ts=s.candles[i].ts,
        trend_up=s.trend_up(i),
        fee_pct=risk.fee_pct,
    )
    if signal.net_reward_risk < risk.min_net_reward_risk:
        return None, (
            f"R:R netto commissioni insufficiente: {signal.net_reward_risk:.2f} "
            f"< {risk.min_net_reward_risk}"
        )
    return signal, ""


def reprice(sig: Signal, price: float) -> Signal:
    """Segnale aggiornato al prezzo di esecuzione: lo SL strutturale resta fermo,
    TP e break-even si ricalcolano per mantenere lo stesso R:R."""
    risk = (price - sig.stop_loss) * sig.side.sign
    if risk <= 0:
        raise ValueError("prezzo già oltre lo stop")
    rr = sig.reward_risk
    be = None
    if sig.break_even_trigger is not None:
        be_r = abs(sig.break_even_trigger - sig.entry) / sig.risk_per_unit
        be = price + sig.side.sign * risk * be_r
    return replace(
        sig,
        entry=price,
        take_profit=price + sig.side.sign * risk * rr,
        break_even_trigger=be,
    )
