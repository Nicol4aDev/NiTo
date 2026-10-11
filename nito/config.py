"""Caricamento e validazione della configurazione.

I parametri operativi stanno in `config.toml`; i segreti (token Telegram,
chiavi API) arrivano solo da variabili d'ambiente / file `.env`.

La validazione applica dei limiti massimi non configurabili (HARD_LIMITS):
anche sbagliando il file di configurazione non si può, per esempio, rischiare
il 10% del capitale per trade o usare leva 50x.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from enum import Enum
from pathlib import Path
from typing import Any

LIVE_CONFIRM_PHRASE = "HO_CAPITO_I_RISCHI"

HARD_LIMITS = {
    "risk_per_trade_pct": 2.0,
    "max_daily_loss_pct": 10.0,
    "max_leverage": 10,
    "max_open_positions": 10,
}

ALL_SETUPS = (
    "viagra",
    "fake_ema10",
    "third_touch_60",
    "third_touch_223",
    "shimano",
    "emergence",
    "anti_crossing",
    "downward_fake",
    "bomb",
)


class ConfigError(ValueError):
    pass


class Mode(str, Enum):
    SIGNALS = "signals"  # solo segnali, esiti tracciati virtualmente
    PAPER = "paper"  # simulazione completa con capitale virtuale
    LIVE = "live"  # ordini reali sull'exchange


@dataclass
class MarketConfig:
    exchange: str = "bybit"
    market_type: str = "swap"  # swap (perpetual) | spot
    symbols: list[str] = field(
        default_factory=lambda: ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"]
    )
    timeframe: str = "5m"
    candles: int = 600
    btc_filter: bool = True
    btc_symbol: str = "BTC/USDT:USDT"
    testnet: bool = True


@dataclass
class StrategyConfig:
    ema_fast: int = 5
    ema_signal: int = 10
    ema_mid: int = 60
    ema_slow: int = 223
    atr_period: int = 14
    enabled: list[str] = field(default_factory=lambda: list(ALL_SETUPS))
    trend_filter: bool = True
    allow_short: bool = True
    viagra_min_closes: int = 5
    shimano_min_closes: int = 8
    touch_lookback: int = 60
    touches_required: int = 3
    cross_gap_pct: float = 0.15
    cross_lookback: int = 5
    fake_cross_lookback: int = 100
    bomb_threshold_pct: float = 2.0
    min_confluence: int = 1


@dataclass
class RiskConfig:
    risk_per_trade_pct: float = 1.0
    reward_risk: float = 2.0
    min_reward_risk: float = 1.2
    min_net_reward_risk: float = 1.0
    sl_mode: str = "candle"  # candle | swing
    swing_lookback: int = 5
    sl_atr_buffer: float = 0.1
    min_sl_pct: float = 0.2
    max_sl_pct: float = 3.0
    break_even_at_r: float = 1.0
    max_open_positions: int = 3
    max_daily_loss_pct: float = 3.0
    max_consecutive_losses: int = 4
    max_leverage: int = 3
    cooldown_minutes: int = 30
    fee_pct: float = 0.06
    account_equity: float = 1000.0  # capitale virtuale per signals/paper


@dataclass
class ExecutionConfig:
    require_confirmation: bool = True
    confirmation_timeout_sec: int = 120
    max_slippage_r: float = 0.25
    monitor_interval_sec: int = 15
    margin_mode: str = "isolated"


@dataclass
class Secrets:
    telegram_token: str = ""
    telegram_chat_id: str = ""
    allowed_user_ids: frozenset[int] = frozenset()
    api_key: str = ""
    api_secret: str = ""
    api_password: str = ""
    live_confirm: str = ""


@dataclass
class Config:
    mode: Mode = Mode.SIGNALS
    timezone: str = "Europe/Rome"
    database: str = "nito.sqlite3"
    market: MarketConfig = field(default_factory=MarketConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    secrets: Secrets = field(default_factory=Secrets)

    @property
    def is_live(self) -> bool:
        return self.mode is Mode.LIVE


def _build(cls: type, data: dict[str, Any], section: str) -> Any:
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"Chiavi sconosciute in [{section}]: {', '.join(sorted(unknown))}")
    return cls(**data)


def _parse_ids(raw: str) -> frozenset[int]:
    ids = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError as exc:
            raise ConfigError(f"TELEGRAM_ALLOWED_USERS contiene un id non numerico: {part!r}") from exc
    return frozenset(ids)


def secrets_from_env(env: dict[str, str] | None = None) -> Secrets:
    env = dict(os.environ) if env is None else env
    return Secrets(
        telegram_token=env.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=env.get("TELEGRAM_CHAT_ID", ""),
        allowed_user_ids=_parse_ids(env.get("TELEGRAM_ALLOWED_USERS", "")),
        api_key=env.get("EXCHANGE_API_KEY", ""),
        api_secret=env.get("EXCHANGE_API_SECRET", ""),
        api_password=env.get("EXCHANGE_API_PASSWORD", ""),
        live_confirm=env.get("LIVE_TRADING_CONFIRM", ""),
    )


def config_from_dict(data: dict[str, Any], secrets: Secrets | None = None) -> Config:
    data = dict(data)
    general = data.pop("general", {})
    sections = {
        "market": MarketConfig,
        "strategy": StrategyConfig,
        "risk": RiskConfig,
        "execution": ExecutionConfig,
    }
    unknown = set(data) - set(sections)
    if unknown:
        raise ConfigError(f"Sezioni sconosciute: {', '.join(sorted(unknown))}")
    try:
        mode = Mode(general.pop("mode", "signals"))
    except ValueError as exc:
        raise ConfigError("general.mode deve essere 'signals', 'paper' o 'live'") from exc
    cfg = Config(
        mode=mode,
        timezone=general.pop("timezone", "Europe/Rome"),
        database=general.pop("database", "nito.sqlite3"),
        market=_build(MarketConfig, data.get("market", {}), "market"),
        strategy=_build(StrategyConfig, data.get("strategy", {}), "strategy"),
        risk=_build(RiskConfig, data.get("risk", {}), "risk"),
        execution=_build(ExecutionConfig, data.get("execution", {}), "execution"),
        secrets=secrets or Secrets(),
    )
    if general:
        raise ConfigError(f"Chiavi sconosciute in [general]: {', '.join(sorted(general))}")
    validate(cfg)
    return cfg


def load_config(path: str | Path = "config.toml", env: dict[str, str] | None = None) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"File di configurazione non trovato: {path}")
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return config_from_dict(data, secrets_from_env(env))


def validate(cfg: Config, require_telegram: bool = False) -> None:
    """Solleva ConfigError se la configurazione non è sicura o coerente."""
    r, s, m = cfg.risk, cfg.strategy, cfg.market
    errors: list[str] = []

    if not 0 < r.risk_per_trade_pct <= HARD_LIMITS["risk_per_trade_pct"]:
        errors.append(
            f"risk.risk_per_trade_pct deve essere tra 0 e {HARD_LIMITS['risk_per_trade_pct']}"
        )
    if not 0 < r.max_daily_loss_pct <= HARD_LIMITS["max_daily_loss_pct"]:
        errors.append(
            f"risk.max_daily_loss_pct deve essere tra 0 e {HARD_LIMITS['max_daily_loss_pct']}"
        )
    if not 1 <= r.max_leverage <= HARD_LIMITS["max_leverage"]:
        errors.append(f"risk.max_leverage deve essere tra 1 e {HARD_LIMITS['max_leverage']}")
    if not 1 <= r.max_open_positions <= HARD_LIMITS["max_open_positions"]:
        errors.append(
            f"risk.max_open_positions deve essere tra 1 e {HARD_LIMITS['max_open_positions']}"
        )
    if r.min_reward_risk < 1.0:
        errors.append("risk.min_reward_risk non può essere minore di 1.0")
    if r.reward_risk < r.min_reward_risk:
        errors.append("risk.reward_risk deve essere >= risk.min_reward_risk")
    if not 0 < r.min_sl_pct < r.max_sl_pct:
        errors.append("serve 0 < risk.min_sl_pct < risk.max_sl_pct")
    if r.sl_mode not in ("candle", "swing"):
        errors.append("risk.sl_mode deve essere 'candle' o 'swing'")
    if r.break_even_at_r < 0:
        errors.append("risk.break_even_at_r non può essere negativo (0 = disattivato)")
    if r.fee_pct < 0:
        errors.append("risk.fee_pct non può essere negativo")
    if r.account_equity <= 0:
        errors.append("risk.account_equity deve essere positivo")

    if m.market_type not in ("swap", "spot"):
        errors.append("market.market_type deve essere 'swap' o 'spot'")
    if not m.symbols:
        errors.append("market.symbols non può essere vuoto")
    if m.candles < s.ema_slow + 50:
        errors.append(f"market.candles deve essere almeno {s.ema_slow + 50} per scaldare la EMA lenta")

    unknown = set(s.enabled) - set(ALL_SETUPS)
    if unknown:
        errors.append(f"strategy.enabled contiene setup sconosciuti: {', '.join(sorted(unknown))}")
    if not s.enabled:
        errors.append("strategy.enabled non può essere vuoto")
    if not s.ema_fast < s.ema_signal < s.ema_mid < s.ema_slow:
        errors.append("le EMA devono essere crescenti: ema_fast < ema_signal < ema_mid < ema_slow")
    if s.min_confluence < 1:
        errors.append("strategy.min_confluence deve essere >= 1")

    if cfg.is_live:
        sec = cfg.secrets
        if sec.live_confirm != LIVE_CONFIRM_PHRASE:
            errors.append(
                f"modalità live: imposta LIVE_TRADING_CONFIRM={LIVE_CONFIRM_PHRASE} nel file .env"
            )
        if not sec.api_key or not sec.api_secret:
            errors.append("modalità live: EXCHANGE_API_KEY ed EXCHANGE_API_SECRET sono obbligatorie")
        if m.market_type != "swap":
            errors.append(
                "modalità live supportata solo su derivati (market_type='swap'), dove lo stop loss "
                "reduce-only resta sull'exchange anche se il bot si spegne"
            )

    if require_telegram:
        sec = cfg.secrets
        if not sec.telegram_token:
            errors.append("TELEGRAM_BOT_TOKEN mancante")
        if not sec.telegram_chat_id:
            errors.append("TELEGRAM_CHAT_ID mancante")
        if not sec.allowed_user_ids:
            errors.append("TELEGRAM_ALLOWED_USERS mancante: nessuno potrebbe comandare il bot")

    if errors:
        raise ConfigError("Configurazione non valida:\n- " + "\n- ".join(errors))
