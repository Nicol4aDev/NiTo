from __future__ import annotations

import pytest

from nito.config import Config, StrategyConfig, config_from_dict
from nito.models import Candle
from nito.strategy import Series

DAY = 86_400_000
T0 = 1_760_054_400_000  # mezzanotte UTC


def make_series(
    close: list[float],
    *,
    open_: list[float] | None = None,
    high: list[float] | None = None,
    low: list[float] | None = None,
    e10: list[float] | float,
    e60: list[float] | float,
    e223: list[float] | float,
    e5: list[float] | float | None = None,
    atr: float = 0.5,
    step_ms: int = 300_000,
) -> Series:
    """Serie con EMA impostate a mano: permette di testare le regole in modo esatto."""
    n = len(close)
    open_ = open_ or list(close)
    high = high or [max(o, c) for o, c in zip(open_, close)]
    low = low or [min(o, c) for o, c in zip(open_, close)]

    def arr(v):
        return list(v) if isinstance(v, list) else [v] * n

    candles = [Candle(T0 + i * step_ms, open_[i], high[i], low[i], close[i]) for i in range(n)]
    return Series(
        candles=candles,
        open=list(open_),
        high=list(high),
        low=list(low),
        close=list(close),
        ema_fast=arr(e5 if e5 is not None else e10),
        ema_signal=arr(e10),
        ema_mid=arr(e60),
        ema_slow=arr(e223),
        atr=[atr] * n,
    )


@pytest.fixture
def cfg() -> Config:
    return config_from_dict({})


@pytest.fixture
def strat() -> StrategyConfig:
    return StrategyConfig()


class FakeNotifier:
    def __init__(self):
        self.messages: list[tuple[str, object]] = []

    async def send(self, text, buttons=None):
        self.messages.append((text, buttons))

    def texts(self) -> str:
        return "\n---\n".join(t for t, _ in self.messages)


@pytest.fixture
def notifier() -> FakeNotifier:
    return FakeNotifier()


class FakeExchange:
    """Simula le chiamate ccxt usate da LiveBroker, registrandole in `calls`."""

    def __init__(self, price: float = 100.0):
        self.price = price
        self.calls: list[tuple] = []
        self.positions: dict[str, dict] = {}
        self.orders: dict[str, dict] = {}
        self.fail_stop = 0  # numero di stop da rifiutare
        self.fail_tp = False
        self.fail_close = False
        self.liquidation: float | None = None
        self.fill_price: float | None = None
        self._next = 1
        self.balance = 1000.0
        self.markets = {
            "SOL/USDT:USDT": {
                "symbol": "SOL/USDT:USDT",
                "settle": "USDT",
                "quote": "USDT",
                "contractSize": 1,
                "limits": {"amount": {"min": 0.1}},
            }
        }

    async def load_markets(self):
        return self.markets

    def market(self, symbol):
        return self.markets[symbol]

    def amount_to_precision(self, symbol, amount):
        return f"{int(amount * 10) / 10:.1f}"

    def price_to_precision(self, symbol, price):
        return f"{price:.3f}"

    async def fetch_balance(self):
        return {"total": {"USDT": self.balance}}

    async def fetch_ticker(self, symbol):
        return {"last": self.price}

    async def set_margin_mode(self, mode, symbol):
        self.calls.append(("margin", mode, symbol))

    async def set_leverage(self, lev, symbol):
        self.calls.append(("leverage", lev, symbol))

    async def fetch_positions(self, symbols=None):
        return [p for s, p in self.positions.items() if symbols is None or s in symbols]

    async def fetch_my_trades(self, symbol, since=None):
        return []

    def _id(self) -> str:
        oid = str(self._next)
        self._next += 1
        return oid

    async def create_order(self, symbol, type_, side, amount, price=None, params=None):
        params = params or {}
        self.calls.append(("order", side, amount, dict(params)))
        if "stopLossPrice" in params:
            if self.fail_stop > 0:
                self.fail_stop -= 1
                raise RuntimeError("stop rifiutato")
            oid = self._id()
            self.orders[oid] = {"kind": "sl", "price": params["stopLossPrice"]}
            return {"id": oid}
        if "takeProfitPrice" in params:
            if self.fail_tp:
                raise RuntimeError("tp rifiutato")
            oid = self._id()
            self.orders[oid] = {"kind": "tp", "price": params["takeProfitPrice"]}
            return {"id": oid}
        if params.get("reduceOnly"):
            if self.fail_close:
                raise RuntimeError("close rifiutato")
            self.positions.pop(symbol, None)
            return {"id": self._id()}
        # ingresso a mercato
        self.positions[symbol] = {
            "symbol": symbol,
            "contracts": amount,
            "side": "long" if side == "buy" else "short",
            "entryPrice": self.fill_price or self.price,
            "liquidationPrice": self.liquidation,
        }
        return {"id": self._id()}

    async def cancel_order(self, oid, symbol, params=None):
        self.calls.append(("cancel", oid))
        self.orders.pop(oid, None)


@pytest.fixture
def fake_exchange() -> FakeExchange:
    return FakeExchange()


async def no_sleep(_):
    return None
