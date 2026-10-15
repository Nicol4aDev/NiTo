"""Configurazione del bot (`config.toml`): token Telegram, utenti autorizzati, tavoli."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from .wheel import Wheel

SOURCES = ("manual", "simulator", "http_json")
DEFAULT_INTERVAL = {"simulator": 30.0, "http_json": 15.0}

_TABLE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class ConfigError(ValueError):
    pass


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


@dataclass
class TableConfig:
    id: str
    name: str
    provider: str
    source: str = "manual"  # manual | simulator | http_json
    wheel: Wheel = Wheel.EUROPEAN
    interval_sec: float = 0.0  # 0 = default della sorgente
    # solo http_json
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    items_path: str = ""
    number_path: str = ""
    id_path: str = ""
    newest_first: bool = True
    timeout_sec: float = 10.0

    @property
    def provider_key(self) -> str:
        return slugify(self.provider)

    @property
    def is_manual(self) -> bool:
        return self.source == "manual"


@dataclass
class RouletteConfig:
    database: str = "roulette.sqlite3"
    history: int = 500  # giri tenuti in memoria per tavolo
    notify_continuation: bool = True  # dopo l'alert, avvisa a ogni giro in più della serie
    notify_break: bool = True  # avvisa quando una serie segnalata si interrompe
    max_alerts_per_chat: int = 30
    tables: list[TableConfig] = field(default_factory=list)
    telegram_token: str = ""
    allowed_user_ids: frozenset[int] = frozenset()

    def table(self, table_id: str) -> TableConfig | None:
        return next((t for t in self.tables if t.id == table_id), None)

    def providers(self) -> dict[str, str]:
        """slug → nome del provider, nell'ordine in cui compaiono."""
        out: dict[str, str] = {}
        for t in self.tables:
            out.setdefault(t.provider_key, t.provider)
        return out


def _parse_ids(value: Any) -> frozenset[int]:
    """Accetta 123456789, [123, 456] oppure "123, 456"."""
    items = value if isinstance(value, list) else str(value).replace(";", ",").split(",")
    ids = set()
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        if not text.isdigit():
            raise ConfigError(f"[telegram] allowed_users contiene un id non numerico: {text!r}")
        ids.add(int(text))
    return frozenset(ids)


def _table(data: dict[str, Any], index: int) -> TableConfig:
    known = {f.name for f in fields(TableConfig)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"Chiavi sconosciute nel tavolo #{index + 1}: {', '.join(sorted(unknown))}")
    missing = [k for k in ("id", "name", "provider") if not data.get(k)]
    if missing:
        raise ConfigError(f"Tavolo #{index + 1}: mancano {', '.join(missing)}")
    data = dict(data)
    try:
        data["wheel"] = Wheel(data.get("wheel", "european"))
    except ValueError as exc:
        raise ConfigError(f"Tavolo {data['id']}: wheel deve essere 'european' o 'american'") from exc
    # url e header possono riferirsi a variabili d'ambiente: "Bearer ${FEED_TOKEN}"
    if "url" in data:
        data["url"] = os.path.expandvars(str(data["url"]))
    if "headers" in data:
        data["headers"] = {str(k): os.path.expandvars(str(v)) for k, v in data["headers"].items()}
    return TableConfig(**data)


def config_from_dict(data: dict[str, Any]) -> RouletteConfig:
    data = dict(data)
    general = dict(data.pop("general", {}))
    telegram = dict(data.pop("telegram", {}))
    tables = data.pop("tables", [])
    if data:
        raise ConfigError(f"Sezioni sconosciute: {', '.join(sorted(data))}")
    unknown = set(telegram) - {"token", "allowed_users"}
    if unknown:
        raise ConfigError(f"Chiavi sconosciute in [telegram]: {', '.join(sorted(unknown))}")
    known = {f.name for f in fields(RouletteConfig)} - {"tables", "telegram_token", "allowed_user_ids"}
    unknown = set(general) - known
    if unknown:
        raise ConfigError(f"Chiavi sconosciute in [general]: {', '.join(sorted(unknown))}")
    cfg = RouletteConfig(
        **general,
        tables=[_table(t, i) for i, t in enumerate(tables)],
        telegram_token=str(telegram.get("token", "")).strip(),
        allowed_user_ids=_parse_ids(telegram.get("allowed_users", [])),
    )
    for t in cfg.tables:
        if not t.interval_sec:
            t.interval_sec = DEFAULT_INTERVAL.get(t.source, 0.0)
    validate(cfg)
    return cfg


def load_config(path: str | Path = "config.toml") -> RouletteConfig:
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"File di configurazione non trovato: {path}\n"
            "Copia config.example.toml in config.toml e inserisci token e user id."
        )
    with path.open("rb") as fh:
        try:
            data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path} non è scritto correttamente: {exc}") from exc
    return config_from_dict(data)


def validate(cfg: RouletteConfig, require_telegram: bool = False) -> None:
    errors: list[str] = []
    if not cfg.tables:
        errors.append("serve almeno un tavolo ([[tables]])")
    if cfg.history < 50:
        errors.append("general.history deve essere almeno 50")
    if cfg.max_alerts_per_chat < 1:
        errors.append("general.max_alerts_per_chat deve essere almeno 1")

    seen: set[str] = set()
    for t in cfg.tables:
        where = f"tavolo {t.id!r}"
        if not _TABLE_ID.match(t.id):
            errors.append(f"{where}: id ammessi a-z, 0-9, '-' e '_', massimo 32 caratteri")
        if t.id in seen:
            errors.append(f"{where}: id duplicato")
        seen.add(t.id)
        if not t.provider_key or len(t.provider_key) > 32:
            errors.append(f"{where}: nome del provider non valido o troppo lungo")
        if t.source not in SOURCES:
            errors.append(f"{where}: source deve essere uno tra {', '.join(SOURCES)}")
            continue
        if t.source != "manual" and t.interval_sec < 1:
            errors.append(f"{where}: interval_sec deve essere almeno 1")
        if t.source == "http_json":
            if not t.url.startswith(("http://", "https://")):
                errors.append(f"{where}: url mancante o non http(s)")
            if not t.number_path:
                errors.append(f"{where}: number_path è obbligatorio (dove si trova il numero)")
            if not t.id_path:
                errors.append(
                    f"{where}: id_path è obbligatorio (id univoco del giro, serve a non contare "
                    "due volte lo stesso risultato)"
                )
            if t.timeout_sec <= 0:
                errors.append(f"{where}: timeout_sec deve essere positivo")

    if require_telegram:
        if not cfg.telegram_token:
            errors.append("token mancante: incollalo in config.toml, sezione [telegram], voce token")
        if not cfg.allowed_user_ids:
            errors.append(
                "allowed_users vuoto: metti il tuo user id Telegram in config.toml, sezione [telegram]"
            )

    if errors:
        raise ConfigError("Configurazione non valida:\n- " + "\n- ".join(errors))
