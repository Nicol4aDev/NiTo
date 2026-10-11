"""Messaggi Telegram (HTML) in italiano."""

from __future__ import annotations

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from .models import Side, Signal, Trade

REASONS = {
    "tp": "🎯 Take Profit raggiunto",
    "sl": "🛑 Stop Loss colpito",
    "be": "⚪ Chiusa a break-even",
    "sl_software": "🚨 Chiusura di emergenza (stop non eseguito dall'exchange)",
    "panic": "🧯 Chiusa con /panic",
    "manual": "✋ Chiusa manualmente",
}


def fmt_price(p: float) -> str:
    if p >= 1000:
        return f"{p:.2f}"
    if p >= 1:
        return f"{p:.4f}"
    return f"{p:.6g}"


def pct(a: float, b: float) -> str:
    return f"{(b - a) / a * 100:+.2f}%"


def fmt_time(ts_ms: int, tz: str) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=ZoneInfo(tz)).strftime("%d/%m/%Y %H:%M")


def short_symbol(symbol: str) -> str:
    return symbol.split(":")[0].replace("/", "")


def base_asset(symbol: str) -> str:
    return symbol.split("/")[0]


def side_label(side: Side) -> str:
    return "🟢 LONG" if side is Side.LONG else "🔴 SHORT"


def format_signal(
    sig: Signal,
    qty: float,
    equity: float,
    risk_pct: float,
    mode: str,
    tz: str,
    created_at: int,
    note: str = "",
) -> str:
    trend = "🟢 rialzista (EMA60 > EMA223)" if sig.trend_up else "🔴 ribassista (EMA60 < EMA223)"
    lines = [
        f"<b>{side_label(sig.side)} {escape(short_symbol(sig.symbol))}</b> · {escape(sig.timeframe)}"
        + (f" · #{sig.id}" if sig.id else ""),
        f"Setup: <b>{escape(sig.setup_names)}</b>",
    ]
    for s in sig.setups:
        lines.append(f"  • <i>{escape(s.reason)}</i>")
    lines += [
        f"Trend: {trend}",
        "",
        f"▶️ Entrata: <code>{fmt_price(sig.entry)}</code>",
        f"🎯 TP: <code>{fmt_price(sig.take_profit)}</code> ({pct(sig.entry, sig.take_profit)})",
        f"🛑 SL: <code>{fmt_price(sig.stop_loss)}</code> ({pct(sig.entry, sig.stop_loss)})",
        f"⚖️ R:R {sig.reward_risk:.2f} (netto commissioni {sig.net_reward_risk:.2f})",
    ]
    if sig.break_even_trigger:
        lines.append(
            f"🔒 Stop a break-even quando tocca <code>{fmt_price(sig.break_even_trigger)}</code>"
        )
    risk_amount = equity * risk_pct / 100
    lines += [
        "",
        f"💰 Size: <code>{qty:.6g}</code> {escape(base_asset(sig.symbol))} "
        f"(rischio {risk_amount:.2f} = {risk_pct:g}% di {equity:.2f})",
        f"⏱ {fmt_time(created_at, tz)} · modalità <b>{escape(mode.upper())}</b>",
    ]
    if note:
        lines += ["", escape(note)]
    return "\n".join(lines)


def format_opened(trade: Trade, mode: str) -> str:
    return (
        f"✅ <b>Posizione aperta</b> {side_label(trade.side)} {escape(short_symbol(trade.symbol))} "
        f"({escape(mode.upper())})\n"
        f"Entrata <code>{fmt_price(trade.entry)}</code> · qty <code>{trade.qty:.6g}</code>\n"
        f"TP <code>{fmt_price(trade.take_profit)}</code> · SL <code>{fmt_price(trade.stop_loss)}</code>"
    )


def format_closed(trade: Trade, currency: str = "USDT") -> str:
    reason = REASONS.get(trade.exit_reason or "", escape(trade.exit_reason or "chiusa"))
    r = trade.r_multiple
    r_txt = f"{r:+.2f}R · " if r is not None else ""
    return (
        f"{reason}\n"
        f"<b>{side_label(trade.side)} {escape(short_symbol(trade.symbol))}</b>"
        + (f" (segnale #{trade.signal_id})" if trade.signal_id else "")
        + "\n"
        f"Entrata <code>{fmt_price(trade.entry)}</code> → uscita "
        f"<code>{fmt_price(trade.exit_price or 0)}</code>\n"
        f"Risultato: <b>{r_txt}{(trade.pnl or 0):+.2f} {currency}</b>"
    )


def format_break_even(trade: Trade) -> str:
    return (
        f"🔒 <b>{escape(short_symbol(trade.symbol))}</b>: stop spostato a break-even "
        f"(<code>{fmt_price(trade.stop_loss)}</code>). Il trade ora non può più andare in perdita "
        "salvo gap di prezzo."
    )


def format_position(trade: Trade, last: float | None) -> str:
    line = (
        f"{side_label(trade.side)} <b>{escape(short_symbol(trade.symbol))}</b> "
        f"qty <code>{trade.qty:.6g}</code> @ <code>{fmt_price(trade.entry)}</code>\n"
        f"   TP <code>{fmt_price(trade.take_profit)}</code> · SL <code>{fmt_price(trade.stop_loss)}</code>"
        + (" 🔒BE" if trade.moved_to_be else "")
    )
    if last is not None:
        risk = abs(trade.entry - (trade.initial_stop or trade.stop_loss))
        r_now = (last - trade.entry) * trade.side.sign / risk if risk else 0.0
        line += f"\n   Ora <code>{fmt_price(last)}</code> ({r_now:+.2f}R)"
    return line
