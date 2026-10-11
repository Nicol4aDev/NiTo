"""Dati di mercato tramite ccxt (endpoint pubblici, nessuna chiave necessaria)."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .config import Config, ConfigError, Mode
from .levels import levels_from_daily
from .models import Candle, DailyLevels


def now_ms() -> int:
    return int(time.time() * 1000)


def timeframe_ms(timeframe: str) -> int:
    units = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
    try:
        return int(timeframe[:-1]) * units[timeframe[-1]] * 1000
    except (KeyError, ValueError) as exc:
        raise ConfigError(f"timeframe non valido: {timeframe}") from exc


def create_exchange(cfg: Config) -> Any:
    """Istanza ccxt asincrona. Le chiavi servono solo in modalità live.

    Il testnet si attiva solo in live: in signals/paper i dati devono essere
    quelli reali del mercato.
    """
    import ccxt.async_support as ccxt_async

    klass = getattr(ccxt_async, cfg.market.exchange, None)
    if klass is None:
        raise ConfigError(f"exchange non supportato da ccxt: {cfg.market.exchange}")
    params: dict[str, Any] = {
        "enableRateLimit": True,
        "options": {"defaultType": cfg.market.market_type},
    }
    if cfg.mode is Mode.LIVE:
        params["apiKey"] = cfg.secrets.api_key
        params["secret"] = cfg.secrets.api_secret
        if cfg.secrets.api_password:
            params["password"] = cfg.secrets.api_password
    exchange = klass(params)
    if cfg.mode is Mode.LIVE and cfg.market.testnet:
        exchange.set_sandbox_mode(True)
    return exchange


class MarketData:
    def __init__(self, exchange: Any, clock: Callable[[], int] = now_ms):
        self.ex = exchange
        self.clock = clock

    async def closed_candles(self, symbol: str, timeframe: str, limit: int) -> list[Candle]:
        """Solo candele chiuse: quella in formazione viene scartata (niente repaint)."""
        tf = timeframe_ms(timeframe)
        rows = await self.ex.fetch_ohlcv(symbol, timeframe, limit=limit + 1)
        now = self.clock()
        candles = [Candle.from_ccxt(r) for r in rows if r[0] + tf <= now]
        return candles[-limit:]

    async def daily_levels(self, symbol: str) -> DailyLevels:
        rows = await self.ex.fetch_ohlcv(symbol, "1d", limit=3)
        return levels_from_daily([Candle.from_ccxt(r) for r in rows], self.clock())

    async def last_price(self, symbol: str) -> float:
        ticker = await self.ex.fetch_ticker(symbol)
        return float(ticker["last"])
