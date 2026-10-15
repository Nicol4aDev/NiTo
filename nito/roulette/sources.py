"""Sorgenti automatiche dei risultati: feed JSON via HTTP e simulatore.

I provider di live casino non offrono API pubbliche per i risultati: il feed
JSON va puntato verso un servizio a cui hai accesso e che sei autorizzato a
usare. La sorgente è generica: in `roulette.toml` si indica dove si trovano,
nella risposta, la lista dei giri, il numero e l'id univoco del giro.
"""

from __future__ import annotations

import logging
import random
from typing import Any

import httpx

from .config import TableConfig
from .wheel import parse_pocket

log = logging.getLogger(__name__)


def get_path(data: Any, path: str) -> Any:
    """Segue un percorso puntato, es. "data.results" o "games.0.outcome.number"."""
    if not path:
        return data
    for part in path.split("."):
        if isinstance(data, dict):
            if part not in data:
                raise KeyError(path)
            data = data[part]
        elif isinstance(data, list):
            try:
                data = data[int(part)]
            except (ValueError, IndexError) as exc:
                raise KeyError(path) from exc
        else:
            raise KeyError(path)
    return data


def parse_feed(table: TableConfig, payload: Any) -> list[tuple[int, str]]:
    """Estrae i giri (numero, id) in ordine cronologico; ignora gli elementi non validi.

    Solleva KeyError se `items_path` non esiste nella risposta.
    """
    root = get_path(payload, table.items_path)
    items = root if isinstance(root, list) else [root]
    spins: list[tuple[int, str]] = []
    for item in items:
        try:
            raw = get_path(item, table.number_path)
            number = parse_pocket(str(raw), table.wheel)
            ext_id = get_path(item, table.id_path)
        except (KeyError, ValueError) as exc:
            log.debug("%s: elemento del feed ignorato (%s)", table.id, exc)
            continue
        if ext_id is None or ext_id == "":
            continue
        spins.append((number, str(ext_id)))
    if table.newest_first:
        spins.reverse()
    return spins


class HttpJsonSource:
    def __init__(self, table: TableConfig, client: httpx.AsyncClient):
        self.table = table
        self.client = client
        self.primed = False  # il primo recupero riempie lo storico senza alert
        self.failures = 0

    async def fetch(self) -> list[tuple[int, str]]:
        resp = await self.client.get(
            self.table.url, headers=self.table.headers, timeout=self.table.timeout_sec
        )
        resp.raise_for_status()
        return parse_feed(self.table, resp.json())


class Simulator:
    """Giri casuali, per provare il bot senza una fonte reale."""

    def __init__(self, table: TableConfig, rng: random.Random | None = None):
        self.table = table
        self.rng = rng or random.SystemRandom()

    def spin(self) -> int:
        return self.rng.choice(self.table.wheel.pockets)
