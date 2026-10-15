"""Registra i giri dei tavoli, calcola le serie e decide quali alert inviare."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from html import escape

from .config import RouletteConfig, TableConfig
from .store import Alert, Store
from .wheel import CATEGORIES, categories_of, current_streak, fmt_pocket


@dataclass(frozen=True)
class Notification:
    chat_id: int
    text: str


def table_title(table: TableConfig) -> str:
    return f"<b>{escape(table.name)}</b> · {escape(table.provider)}"


def fmt_numbers(numbers: Iterable[int], limit: int = 12) -> str:
    """Numeri dal più recente al più vecchio, come sul tabellone del tavolo."""
    recent = list(numbers)[-limit:]
    return " ".join(fmt_pocket(n) for n in reversed(recent)) or "—"


def matches(alert: Alert, table: TableConfig) -> bool:
    if alert.scope == "table":
        return alert.target == table.id
    if alert.scope == "provider":
        return alert.target == table.provider_key
    return alert.scope == "all"


class Tracker:
    def __init__(self, cfg: RouletteConfig, store: Store, clock: Callable[[], float] = time.time):
        self.cfg = cfg
        self.store = store
        self.clock = clock
        self.tables = {t.id: t for t in cfg.tables}
        self.recent: dict[str, deque[int]] = {
            t.id: deque(store.recent(t.id, cfg.history), maxlen=cfg.history) for t in cfg.tables
        }
        self.last_ts: dict[str, int | None] = {t.id: store.last_ts(t.id) for t in cfg.tables}

    def now_ms(self) -> int:
        return int(self.clock() * 1000)

    def streaks(self, table_id: str) -> dict[str, int]:
        numbers = self.recent[table_id]
        return {key: current_streak(numbers, key) for key in CATEGORIES}

    def add_spins(
        self,
        table_id: str,
        spins: Iterable[tuple[int, str | None]],
        notify: bool = True,
    ) -> tuple[int, list[Notification]]:
        """Registra i giri (numero, id esterno) in ordine cronologico.

        Restituisce quanti giri erano nuovi e le notifiche da inviare. Se ne
        arrivano diversi insieme (sequenza inserita a mano, feed in ritardo) si
        confronta lo stato prima e dopo la sequenza: un solo messaggio, senza
        alert sui passaggi intermedi ormai superati. Con notify=False (primo
        recupero dello storico all'avvio) non segnala nulla.
        """
        ts = self.now_ms()
        new = [n for n, ext_id in spins if self.store.insert_spin(table_id, n, ts, ext_id)]
        if not new:
            return 0, []
        before = self.streaks(table_id) if notify else {}
        self.recent[table_id].extend(new)
        self.last_ts[table_id] = ts
        if not notify:
            return len(new), []
        return len(new), self._evaluate(self.tables[table_id], new, before)

    def undo(self, table_id: str) -> int | None:
        number = self.store.delete_last(table_id)
        if number is not None:
            history = self.recent[table_id]
            history.clear()
            history.extend(self.store.recent(table_id, self.cfg.history))
            self.last_ts[table_id] = self.store.last_ts(table_id)
        return number

    def _evaluate(
        self, table: TableConfig, batch: list[int], before: dict[str, int]
    ) -> list[Notification]:
        after = self.streaks(table.id)

        # soglie per chat e categoria: più alert sovrapposti nella stessa chat
        # (es. "rosso 6 ovunque" e "rosso 8 su Evolution") producono un solo messaggio
        thresholds: dict[int, dict[str, set[int]]] = {}
        for alert in self.store.alerts():
            if matches(alert, table) and alert.category in CATEGORIES:
                thresholds.setdefault(alert.chat_id, {}).setdefault(alert.category, set()).add(
                    alert.length
                )

        notes = []
        for chat_id, by_key in thresholds.items():
            lines = []
            for key, cat in CATEGORIES.items():
                lengths = by_key.get(key)
                if not lengths:
                    continue
                lowest, old, new = min(lengths), before[key], after[key]
                broken = new < old + len(batch)  # la serie di prima si è interrotta
                if broken and old >= lowest and self.cfg.notify_break:
                    run = old
                    for n in batch:
                        if key not in categories_of(n):
                            break
                        run += 1
                    lines.append(f"✋ {cat.label}: serie interrotta dopo {run}")
                start = 0 if broken else old
                if any(start < length <= new for length in lengths):
                    lines.append(f"🚨 {cat.label}: <b>{new} di fila</b>")
                elif new > lowest and self.cfg.notify_continuation:
                    lines.append(f"➕ {cat.label}: continua, <b>{new} di fila</b>")
            if lines:
                text = "\n".join(
                    [
                        f"🎰 {table_title(table)}",
                        *lines,
                        f"Uscito: {' '.join(fmt_pocket(n) for n in batch)}",
                        f"Ultimi: {fmt_numbers(self.recent[table.id], 10)}",
                    ]
                )
                notes.append(Notification(chat_id, text))
        return notes
