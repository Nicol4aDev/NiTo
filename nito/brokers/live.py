"""Esecuzione reale tramite ccxt (derivati perpetual, modalità one-way).

Regole di sicurezza implementate qui:
1. Lo stop loss è un ordine reduce-only sull'exchange: resta attivo anche se il
   bot si spegne o perde la connessione.
2. Se lo stop loss non viene accettato, la posizione appena aperta viene chiusa
   subito a mercato. Nessuna posizione resta aperta senza stop.
3. Se il prezzo di liquidazione cadrebbe prima dello stop (leva troppo alta), la
   posizione viene chiusa.
4. Il monitor fa da rete di sicurezza: se il prezzo ha superato lo stop e la
   posizione è ancora aperta, chiude a mercato; se il TP non è stato piazzato,
   chiude al raggiungimento del target.
5. Lo spostamento a break-even piazza PRIMA il nuovo stop e POI cancella il
   vecchio, così non c'è mai un istante senza protezione.

Testato con exchange simulato nei test automatici: prima di usare soldi veri
va provato sul testnet dell'exchange (market.testnet = true).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ..config import Config
from ..models import Side, Signal, Trade
from ..simulation import break_even_price, close_trade
from .base import Broker, BrokerError, BrokerEvent

log = logging.getLogger(__name__)

SOFTWARE_STOP_MARGIN_R = 0.2  # oltre lo stop di 0.2R senza chiusura = stop non eseguito
MAX_BE_ATTEMPTS = 3


class LiveBroker(Broker):
    name = "live"

    def __init__(
        self,
        exchange: Any,
        cfg: Config,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.ex = exchange
        self.cfg = cfg
        self.fee_pct = cfg.risk.fee_pct
        self.sleep = sleep
        self._prepared: set[str] = set()

    # --- utilità ---
    def _contract_size(self, symbol: str) -> float:
        return float(self.ex.market(symbol).get("contractSize") or 1)

    def _amount(self, symbol: str, qty_base: float) -> float:
        return float(self.ex.amount_to_precision(symbol, qty_base / self._contract_size(symbol)))

    def _px(self, symbol: str, price: float) -> float:
        return float(self.ex.price_to_precision(symbol, price))

    async def _last(self, symbol: str) -> float:
        ticker = await self.ex.fetch_ticker(symbol)
        return float(ticker["last"])

    async def _position(self, symbol: str) -> dict | None:
        positions = await self.ex.fetch_positions([symbol])
        for p in positions or []:
            if p.get("symbol") == symbol and abs(float(p.get("contracts") or 0)) > 0:
                return p
        return None

    async def _wait_position(self, symbol: str, attempts: int = 6) -> dict | None:
        for _ in range(attempts):
            pos = await self._position(symbol)
            if pos is not None:
                return pos
            await self.sleep(0.5)
        return None

    async def equity(self) -> float:
        await self.ex.load_markets()
        market = self.ex.market(self.cfg.market.symbols[0])
        currency = market.get("settle") or market.get("quote")
        balance = await self.ex.fetch_balance()
        total = (balance.get("total") or {}).get(currency)
        if total is None:
            raise BrokerError(f"saldo {currency} non disponibile")
        return float(total)

    async def _prepare(self, symbol: str) -> None:
        if symbol in self._prepared:
            return
        try:
            await self.ex.set_margin_mode(self.cfg.execution.margin_mode, symbol)
        except Exception as exc:  # già impostato o non supportato
            log.warning("set_margin_mode %s: %s", symbol, exc)
        try:
            await self.ex.set_leverage(self.cfg.risk.max_leverage, symbol)
        except Exception as exc:  # spesso "leverage not modified"
            log.warning("set_leverage %s: %s", symbol, exc)
        self._prepared.add(symbol)

    async def _place_stop(self, symbol: str, side: Side, amount: float, price: float) -> str:
        last_exc: Exception | None = None
        for _ in range(2):
            try:
                order = await self.ex.create_order(
                    symbol,
                    "market",
                    side.exit_order_side,
                    amount,
                    None,
                    {"stopLossPrice": price, "reduceOnly": True},
                )
                if not order or not order.get("id"):
                    raise BrokerError("l'exchange non ha restituito l'id dello stop")
                return str(order["id"])
            except Exception as exc:
                last_exc = exc
                await self.sleep(1)
        assert last_exc is not None
        raise last_exc

    async def _place_take_profit(self, symbol: str, side: Side, amount: float, price: float) -> str:
        order = await self.ex.create_order(
            symbol,
            "market",
            side.exit_order_side,
            amount,
            None,
            {"takeProfitPrice": price, "reduceOnly": True},
        )
        if not order or not order.get("id"):
            raise BrokerError("l'exchange non ha restituito l'id del take profit")
        return str(order["id"])

    async def _market_close(self, symbol: str, side: Side, amount: float) -> bool:
        for _ in range(3):
            try:
                await self.ex.create_order(
                    symbol, "market", side.exit_order_side, amount, None, {"reduceOnly": True}
                )
                return True
            except Exception as exc:
                log.error("chiusura a mercato %s fallita: %s", symbol, exc)
                await self.sleep(1)
        return False

    async def _cancel(self, symbol: str, order_id: str | None) -> None:
        if not order_id:
            return
        for params in ({}, {"trigger": True}):
            try:
                await self.ex.cancel_order(order_id, symbol, params)
                return
            except Exception as exc:  # già eseguito/cancellato
                log.info("cancel %s %s %s: %s", symbol, order_id, params, exc)

    async def _exit_price(self, trade: Trade) -> float | None:
        try:
            fills = await self.ex.fetch_my_trades(trade.symbol, trade.opened_at)
        except Exception as exc:
            log.warning("fetch_my_trades %s: %s", trade.symbol, exc)
            return None
        exits = [f for f in fills or [] if f.get("side") == trade.side.exit_order_side]
        qty = sum(float(f.get("amount") or 0) for f in exits)
        if qty <= 0:
            return None
        return sum(float(f["price"]) * float(f.get("amount") or 0) for f in exits) / qty

    # --- interfaccia Broker ---
    async def open_trade(self, signal: Signal, qty: float, now_ms: int) -> Trade:
        sym, side = signal.symbol, signal.side
        await self.ex.load_markets()
        market = self.ex.market(sym)
        amount = self._amount(sym, qty)
        min_amount = ((market.get("limits") or {}).get("amount") or {}).get("min")
        if amount <= 0 or (min_amount and amount < float(min_amount)):
            raise BrokerError(f"quantità {amount} sotto il minimo dell'exchange ({min_amount})")
        if await self._position(sym) is not None:
            raise BrokerError(f"esiste già una posizione su {sym} non gestita dal bot")

        await self._prepare(sym)
        try:
            await self.ex.create_order(sym, "market", side.entry_order_side, amount)
        except Exception as exc:
            raise BrokerError(f"ordine di ingresso rifiutato: {exc}") from exc

        try:
            return await self._protect(signal, amount, now_ms)
        except BrokerError:
            raise
        except Exception as exc:
            # Errore imprevisto (rete, API) con la posizione forse aperta e senza stop:
            # si prova comunque a chiuderla.
            closed = await self._market_close(sym, side, amount)
            suffix = "posizione chiusa" if closed else "CHIUSURA FALLITA: INTERVIENI MANUALMENTE!"
            raise BrokerError(f"errore dopo l'ingresso ({exc}): {suffix}") from exc

    async def _protect(self, signal: Signal, amount: float, now_ms: int) -> Trade:
        """Dopo l'ingresso: legge la posizione e piazza SL e TP sull'exchange."""
        sym, side = signal.symbol, signal.side
        pos = await self._wait_position(sym)
        if pos is None:
            # reduce-only: se la posizione in realtà non esiste l'ordine viene solo rifiutato
            await self._market_close(sym, side, amount)
            raise BrokerError(
                f"ordine di ingresso inviato ma nessuna posizione trovata su {sym}: "
                "tentata chiusura di sicurezza, controlla subito l'exchange"
            )
        contracts = abs(float(pos["contracts"]))
        fill = float(pos.get("entryPrice") or signal.entry)
        sl = self._px(sym, signal.stop_loss)
        risk = (fill - sl) * side.sign

        async def abort(why: str) -> BrokerError:
            closed = await self._market_close(sym, side, contracts)
            suffix = "" if closed else " — CHIUSURA FALLITA: INTERVIENI MANUALMENTE!"
            return BrokerError(why + suffix)

        if risk <= 0:
            raise await abort("il prezzo di esecuzione ha già superato lo stop: posizione chiusa")
        liq = pos.get("liquidationPrice")
        if liq and (float(liq) - sl) * side.sign >= 0:
            raise await abort(
                f"prezzo di liquidazione {liq} prima dello stop {sl}: posizione chiusa, riduci la leva"
            )

        tp = self._px(sym, fill + side.sign * risk * signal.reward_risk)
        be = None
        if signal.break_even_trigger is not None:
            be_r = abs(signal.break_even_trigger - signal.entry) / signal.risk_per_unit
            be = self._px(sym, fill + side.sign * risk * be_r)

        try:
            sl_id = await self._place_stop(sym, side, contracts, sl)
        except Exception as exc:
            raise await abort(f"stop loss NON accettato ({exc}): posizione chiusa per sicurezza") from exc

        tp_id = None
        try:
            tp_id = await self._place_take_profit(sym, side, contracts, tp)
        except Exception as exc:
            log.warning("take profit %s non piazzato, gestito dal monitor: %s", sym, exc)

        trade = Trade(
            symbol=sym,
            side=side,
            qty=contracts * self._contract_size(sym),
            entry=fill,
            stop_loss=sl,
            take_profit=tp,
            break_even_trigger=be,
            opened_at=now_ms,
            signal_id=signal.id,
            sl_order_id=sl_id,
            tp_order_id=tp_id,
        )
        trade.extra["contracts"] = contracts
        return trade

    async def refresh(self, trade: Trade, now_ms: int) -> list[BrokerEvent]:
        sym, side = trade.symbol, trade.side
        pos = await self._position(sym)
        if pos is None:
            await self._finalize(trade, now_ms)
            return [BrokerEvent("closed")]

        contracts = abs(float(pos["contracts"]))
        last = await self._last(sym)
        sign = side.sign
        risk = abs(trade.entry - (trade.initial_stop or trade.stop_loss))

        if (trade.stop_loss - last) * sign >= SOFTWARE_STOP_MARGIN_R * risk:
            await self._close_now(trade, contracts, "sl_software", now_ms)
            return [BrokerEvent("closed", "stop sull'exchange non eseguito: chiusura di emergenza")]
        if trade.tp_order_id is None and (last - trade.take_profit) * sign >= 0:
            await self._close_now(trade, contracts, "tp", now_ms)
            return [BrokerEvent("closed")]

        be = trade.break_even_trigger
        attempts = int(trade.extra.get("be_attempts", 0))
        if be is not None and not trade.moved_to_be and (last - be) * sign >= 0:
            if attempts >= MAX_BE_ATTEMPTS:
                return []
            new_stop = break_even_price(trade, self.fee_pct)
            new_stop = self._px(sym, min(new_stop, be) if sign > 0 else max(new_stop, be))
            try:
                new_id = await self._place_stop(sym, side, contracts, new_stop)
            except Exception as exc:
                trade.extra["be_attempts"] = attempts + 1
                return [BrokerEvent("warning", f"spostamento a break-even fallito: {exc}")]
            await self._cancel(sym, trade.sl_order_id)
            trade.sl_order_id = new_id
            trade.stop_loss = new_stop
            trade.moved_to_be = True
            return [BrokerEvent("break_even")]
        return []

    async def _close_now(self, trade: Trade, contracts: float, reason: str, now_ms: int) -> None:
        if not await self._market_close(trade.symbol, trade.side, contracts):
            raise BrokerError(f"chiusura di {trade.symbol} fallita: INTERVIENI MANUALMENTE")
        await self._finalize(trade, now_ms, reason)

    async def _finalize(self, trade: Trade, now_ms: int, reason: str | None = None) -> None:
        await self._cancel(trade.symbol, trade.sl_order_id)
        await self._cancel(trade.symbol, trade.tp_order_id)
        price = await self._exit_price(trade)
        if price is None:
            price = await self._last(trade.symbol)
        if reason is None:
            to_tp = abs(price - trade.take_profit)
            to_sl = abs(price - trade.stop_loss)
            if to_tp < to_sl:
                reason = "tp"
            else:
                reason = "be" if trade.moved_to_be else "sl"
        close_trade(trade, price, now_ms, reason, self.fee_pct)

    async def close_trade(self, trade: Trade, reason: str, now_ms: int) -> None:
        pos = await self._position(trade.symbol)
        if pos is None:
            await self._finalize(trade, now_ms)
            return
        await self._close_now(trade, abs(float(pos["contracts"])), reason, now_ms)

    async def reconcile(self, open_trades: list[Trade]) -> list[str]:
        managed = {t.symbol for t in open_trades}
        warnings = []
        try:
            positions = await self.ex.fetch_positions(self.cfg.market.symbols)
        except Exception as exc:
            return [f"impossibile leggere le posizioni: {exc}"]
        for p in positions or []:
            sym = p.get("symbol")
            if abs(float(p.get("contracts") or 0)) > 0 and sym not in managed:
                warnings.append(f"posizione aperta su {sym} non gestita dal bot (nessuno stop del bot)")
        return warnings
