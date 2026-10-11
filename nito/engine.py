"""Orchestrazione: scansione dei mercati → segnale → controlli di rischio →
(conferma) → esecuzione → monitoraggio delle posizioni."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Protocol

from .brokers import Broker, BrokerError
from .config import Config, Mode
from .formatting import (
    fmt_price,
    format_break_even,
    format_closed,
    format_opened,
    format_position,
    format_signal,
)
from .market_data import MarketData, now_ms
from .models import Side, Signal, Trade
from .risk import RiskGuard, position_size
from .signals import build_signal, reprice
from .stats import compute_stats
from .storage import Store
from .strategy import Series, detect, pick_side

log = logging.getLogger(__name__)

Buttons = list[list[tuple[str, str]]]


class Notifier(Protocol):
    async def send(self, text: str, buttons: Buttons | None = None) -> None: ...


def settle_currency(symbol: str) -> str:
    if ":" in symbol:
        return symbol.split(":")[1]
    return symbol.split("/")[1] if "/" in symbol else "USDT"


class Engine:
    def __init__(
        self,
        cfg: Config,
        data: MarketData,
        broker: Broker,
        store: Store,
        notifier: Notifier,
        clock: Callable[[], int] = now_ms,
    ):
        self.cfg = cfg
        self.data = data
        self.broker = broker
        self.store = store
        self.notifier = notifier
        self.clock = clock
        self.guard = RiskGuard(cfg.risk, store)
        self.lock = asyncio.Lock()
        self.scan_lock = asyncio.Lock()  # /scan manuale e job pianificato non si sovrappongono
        self.allow_short = cfg.strategy.allow_short and cfg.market.market_type == "swap"
        self.currency = settle_currency(cfg.market.symbols[0])
        self._errors: dict[str, int] = {}

    # ------------------------------------------------------------------ utilità
    async def _notify(self, text: str, buttons: Buttons | None = None) -> None:
        try:
            await self.notifier.send(text, buttons)
        except Exception:  # una notifica fallita non deve fermare il trading
            log.exception("invio notifica fallito")

    async def _error(self, key: str, exc: Exception) -> None:
        n = self._errors.get(key, 0) + 1
        self._errors[key] = n
        log.error("%s: %s", key, exc)
        if n == 1 or n % 20 == 0:
            await self._notify(f"⚠️ Errore su {key} (x{n}): {exc}")

    def _ok(self, key: str) -> None:
        self._errors.pop(key, None)

    # ---------------------------------------------------------------- analisi
    async def _trend(self, symbol: str) -> bool | None:
        candles = await self.data.closed_candles(symbol, self.cfg.market.timeframe, self.cfg.market.candles)
        s = Series.build(candles, self.cfg.strategy)
        i = len(s) - 1
        return s.trend_up(i) if s.ready(i, 0) else None

    async def analyze(self, symbol: str, btc_trend: bool | None = None) -> Signal | None:
        m, st = self.cfg.market, self.cfg.strategy
        candles = await self.data.closed_candles(symbol, m.timeframe, m.candles)
        if not candles:
            return None
        last_ts = candles[-1].ts
        if self.store.get_state(f"last_candle:{symbol}") == str(last_ts):
            return None  # candela già analizzata
        levels = await self.data.daily_levels(symbol)
        s = Series.build(candles, st)
        i = len(s) - 1
        setups = detect(s, i, levels, st, self.allow_short)
        # segnata solo ad analisi riuscita: se un fetch fallisce si riprova al giro dopo
        self.store.set_state(f"last_candle:{symbol}", str(last_ts))
        side = pick_side(setups, st.min_confluence)
        if side is None:
            return None
        if m.btc_filter and symbol != m.btc_symbol and btc_trend is not None:
            if btc_trend != (side is Side.LONG):
                log.info("%s %s scartato: BTC in trend opposto", symbol, side.value)
                return None
        sig, reason = build_signal(
            s, i, side, [x for x in setups if x.side is side], self.cfg.risk, symbol, m.timeframe
        )
        if sig is None:
            log.info("%s %s scartato: %s", symbol, side.value, reason)
        return sig

    async def scan(self) -> list[Signal]:
        async with self.scan_lock:
            return await self._scan()

    async def _scan(self) -> list[Signal]:
        if self.guard.paused:
            return []
        m = self.cfg.market
        btc_trend = None
        if m.btc_filter:
            try:
                btc_trend = await self._trend(m.btc_symbol)
                self._ok("BTC")
            except Exception as exc:
                await self._error("BTC", exc)
        sent = []
        for symbol in m.symbols:
            try:
                sig = await self.analyze(symbol, btc_trend)
                if sig is not None and await self.on_signal(sig):
                    sent.append(sig)
                self._ok(symbol)
            except Exception as exc:  # un simbolo in errore non ferma gli altri
                await self._error(symbol, exc)
        return sent

    # ----------------------------------------------------------------- segnali
    async def on_signal(self, sig: Signal) -> bool:
        now = self.clock()
        if self.guard.in_cooldown(sig.symbol, now):
            return False
        block = self.guard.block_reason(sig.symbol, self.store.open_trades())
        if block:
            log.info("segnale %s non inviato: %s", sig.symbol, block)
            return False
        equity = await self.broker.equity()
        r = self.cfg.risk
        qty = position_size(equity, r.risk_per_trade_pct, sig.entry, sig.stop_loss, r.fee_pct, r.max_leverage)
        if qty <= 0:
            return False
        self.guard.mark_signal(sig.symbol, now)

        ex = self.cfg.execution
        confirm = self.cfg.mode is not Mode.SIGNALS and ex.require_confirmation
        if confirm:
            self.store.save_signal(sig, "pending", now, now + ex.confirmation_timeout_sec * 1000)
            note = f"Confermi l'esecuzione? Il segnale scade tra {ex.confirmation_timeout_sec}s."
        else:
            self.store.save_signal(sig, "sent", now)
            note = ""
        text = format_signal(
            sig, qty, equity, r.risk_per_trade_pct, self.cfg.mode.value, self.cfg.timezone, now, note
        )
        if confirm:
            await self._notify(text, [[("✅ Esegui", f"exec:{sig.id}"), ("❌ Ignora", f"skip:{sig.id}")]])
        else:
            await self._notify(text)
            await self.execute(sig)
        return True

    async def confirm(self, signal_id: int) -> str:
        stored = self.store.get_signal(signal_id)
        if stored is None:
            return "Segnale non trovato."
        if stored.status != "pending":
            return f"Segnale #{signal_id} già gestito ({stored.status})."
        now = self.clock()
        if stored.expires_at is not None and now > stored.expires_at:
            self.store.claim_pending_signal(signal_id, "expired")
            return f"⌛ Segnale #{signal_id} scaduto: non eseguito."
        sig = stored.signal
        price = await self.data.last_price(sig.symbol)
        sign = sig.side.sign
        if (price - sig.stop_loss) * sign <= 0 or (price - sig.take_profit) * sign >= 0:
            self.store.claim_pending_signal(signal_id, "rejected")
            return f"Prezzo attuale {fmt_price(price)} già oltre SL o TP: segnale #{signal_id} annullato."
        drift = abs(price - sig.entry)
        if drift > self.cfg.execution.max_slippage_r * sig.risk_per_unit:
            self.store.claim_pending_signal(signal_id, "rejected")
            return (
                f"Prezzo attuale {fmt_price(price)} troppo lontano dall'entrata "
                f"{fmt_price(sig.entry)} (> {self.cfg.execution.max_slippage_r}R): segnale annullato."
            )
        if not self.store.claim_pending_signal(signal_id, "confirmed"):
            return f"Segnale #{signal_id} già gestito."
        trade = await self.execute(reprice(sig, price))
        return f"Segnale #{signal_id} eseguito." if trade else f"Segnale #{signal_id} non eseguito."

    def skip(self, signal_id: int) -> str:
        if self.store.claim_pending_signal(signal_id, "skipped"):
            return f"Segnale #{signal_id} ignorato."
        return f"Segnale #{signal_id} già gestito."

    async def execute(self, sig: Signal) -> Trade | None:
        async with self.lock:
            now = self.clock()
            block = self.guard.block_reason(sig.symbol, self.store.open_trades())
            if block:
                self.store.set_signal_status(sig.id, "rejected", block)  # type: ignore[arg-type]
                await self._notify(f"⛔ Segnale #{sig.id} non eseguito: {block}")
                return None
            r = self.cfg.risk
            try:
                equity = await self.broker.equity()
                qty = position_size(
                    equity, r.risk_per_trade_pct, sig.entry, sig.stop_loss, r.fee_pct, r.max_leverage
                )
                trade = await self.broker.open_trade(sig, qty, now)
            except BrokerError as exc:
                self.store.set_signal_status(sig.id, "failed", str(exc))  # type: ignore[arg-type]
                await self._notify(f"🚨 Esecuzione segnale #{sig.id} fallita: {exc}")
                return None
            except Exception as exc:
                log.exception("errore imprevisto in esecuzione")
                self.store.set_signal_status(sig.id, "failed", str(exc))  # type: ignore[arg-type]
                await self._notify(
                    f"🚨 Errore imprevisto eseguendo il segnale #{sig.id}: {exc}\n"
                    "Controlla subito le posizioni sull'exchange."
                )
                return None
            self.store.insert_trade(trade)
            self.store.set_signal_status(sig.id, "executed")  # type: ignore[arg-type]
            if self.cfg.mode is not Mode.SIGNALS:
                await self._notify(format_opened(trade, self.cfg.mode.value))
            return trade

    # ---------------------------------------------------------------- monitor
    async def monitor(self) -> None:
        async with self.lock:
            now = self.clock()
            self.store.expire_pending(now)
            for trade in self.store.open_trades():
                key = f"monitor {trade.symbol}"
                try:
                    events = await self.broker.refresh(trade, now)
                    self._ok(key)
                except BrokerError as exc:
                    await self._notify(f"🚨 {trade.symbol}: {exc}")
                    continue
                except Exception as exc:
                    await self._error(key, exc)
                    continue
                self.store.update_trade(trade)
                for ev in events:
                    if ev.kind == "closed":
                        text = format_closed(trade, self.currency)
                        await self._notify(text + (f"\n{ev.message}" if ev.message else ""))
                        halt = self.guard.on_trade_closed(trade)
                        if halt:
                            await self._notify(f"🛑 {halt}")
                    elif ev.kind == "break_even":
                        await self._notify(format_break_even(trade))
                    elif ev.kind == "warning":
                        await self._notify(f"⚠️ {trade.symbol}: {ev.message}")
            await self._check_equity(now)

    async def _check_equity(self, now: int) -> None:
        try:
            equity = await self.broker.equity()
            self._ok("saldo")
        except Exception as exc:
            await self._error("saldo", exc)
            return
        if not self.store.get_state("initial_equity"):
            self.store.set_state("initial_equity", repr(equity))
        halt = self.guard.update_equity(equity, now)
        if halt:
            await self._notify(f"🛑 {halt}")

    async def panic(self) -> list[str]:
        """Kill switch: blocca nuovi trade e chiude tutto a mercato."""
        self.guard.kill("/panic")
        out = []
        async with self.lock:
            now = self.clock()
            for trade in self.store.open_trades():
                try:
                    await self.broker.close_trade(trade, "panic", now)
                    self.store.update_trade(trade)
                    out.append(format_closed(trade, self.currency))
                except Exception as exc:
                    out.append(f"🚨 {trade.symbol}: chiusura fallita ({exc}). INTERVIENI MANUALMENTE.")
        return out

    async def startup(self) -> None:
        open_trades = self.store.open_trades()
        warnings = await self.broker.reconcile(open_trades)
        await self._check_equity(self.clock())
        m = self.cfg.market
        lines = [
            f"🤖 <b>NiTo avviato</b> in modalità <b>{self.cfg.mode.value.upper()}</b>",
            f"{m.exchange} · {m.timeframe} · {', '.join(m.symbols)}",
            f"Posizioni aperte riprese: {len(open_trades)}",
        ]
        if self.cfg.mode is Mode.LIVE:
            lines.append("⚠️ ORDINI REALI" + (" sul TESTNET" if m.testnet else " CON DENARO VERO"))
        if self.guard.kill_reason:
            lines.append(f"🛑 Kill switch attivo: {self.guard.kill_reason} (usa /resume)")
        lines += [f"⚠️ {w}" for w in warnings]
        await self._notify("\n".join(lines))

    # ------------------------------------------------------------ report
    async def status_text(self) -> str:
        g, r = self.guard, self.cfg.risk
        try:
            equity = await self.broker.equity()
            eq_txt = f"{equity:.2f} {self.currency}"
        except Exception as exc:
            equity, eq_txt = None, f"non disponibile ({exc})"
        start = g.day_start_equity
        day_txt = ""
        if equity is not None and start:
            day_txt = f" · oggi {(equity - start) / start * 100:+.2f}%"
        state = "▶️ attivo"
        if g.paused:
            state = "⏸ in pausa (/resume)"
        if g.kill_reason:
            state = f"🛑 kill switch: {g.kill_reason}"
        elif g.daily_halt:
            state = f"🛑 {g.daily_halt}"
        m = self.cfg.market
        return "\n".join(
            [
                f"<b>Stato NiTo</b> — {self.cfg.mode.value.upper()}",
                f"Stato: {state}",
                f"Capitale: {eq_txt}{day_txt}",
                f"Posizioni aperte: {len(self.store.open_trades())}/{r.max_open_positions}",
                f"Perdite consecutive: {g.consecutive_losses}/{r.max_consecutive_losses}",
                f"Rischio per trade: {r.risk_per_trade_pct}% · perdita max giornaliera: {r.max_daily_loss_pct}%",
                f"Mercati: {m.exchange} {m.timeframe} · {', '.join(m.symbols)}",
            ]
        )

    async def positions_text(self) -> str:
        trades = self.store.open_trades()
        if not trades:
            return "Nessuna posizione aperta."
        lines = []
        for t in trades:
            try:
                last = await self.data.last_price(t.symbol)
            except Exception:
                last = None
            lines.append(format_position(t, last))
        return "\n\n".join(lines)

    def stats_text(self) -> str:
        trades = self.store.closed_trades()
        start = float(self.store.get_state("initial_equity") or self.cfg.risk.account_equity)
        st = compute_stats(trades, start)
        return "<b>Statistiche</b>\n" + "\n".join(st.as_lines(self.currency))
