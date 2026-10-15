"""Persistenza su SQLite: giri usciti per tavolo e alert degli utenti."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS spins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_id TEXT NOT NULL,
    number INTEGER NOT NULL,
    ts INTEGER NOT NULL,
    ext_id TEXT
);
-- ext_id NULL (inserimenti manuali, simulatore) non va mai in conflitto
CREATE UNIQUE INDEX IF NOT EXISTS spins_ext ON spins(table_id, ext_id);
CREATE INDEX IF NOT EXISTS spins_table ON spins(table_id, id);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    length INTEGER NOT NULL,
    scope TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    UNIQUE(chat_id, category, length, scope, target)
);
"""


@dataclass(frozen=True)
class Alert:
    """Avvisa `chat_id` quando `category` esce `length` volte di fila.

    scope: "all" (tutti i tavoli), "provider" (target = slug del provider) o
    "table" (target = id del tavolo).
    """

    id: int
    chat_id: int
    category: str
    length: int
    scope: str
    target: str = ""


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- giri ---
    def insert_spin(self, table_id: str, number: int, ts: int, ext_id: str | None = None) -> bool:
        """False se il giro (stesso ext_id) era già registrato."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO spins(table_id, number, ts, ext_id) VALUES(?,?,?,?)",
            (table_id, number, ts, ext_id),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def recent(self, table_id: str, limit: int) -> list[int]:
        """Ultimi `limit` numeri in ordine cronologico (il più recente per ultimo)."""
        rows = self.conn.execute(
            "SELECT number FROM spins WHERE table_id = ? ORDER BY id DESC LIMIT ?",
            (table_id, limit),
        ).fetchall()
        return [r["number"] for r in reversed(rows)]

    def last_ts(self, table_id: str) -> int | None:
        row = self.conn.execute(
            "SELECT ts FROM spins WHERE table_id = ? ORDER BY id DESC LIMIT 1", (table_id,)
        ).fetchone()
        return row["ts"] if row else None

    def count(self, table_id: str) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM spins WHERE table_id = ?", (table_id,))
        return row.fetchone()["n"]

    def delete_last(self, table_id: str) -> int | None:
        row = self.conn.execute(
            "SELECT id, number FROM spins WHERE table_id = ? ORDER BY id DESC LIMIT 1", (table_id,)
        ).fetchone()
        if row is None:
            return None
        self.conn.execute("DELETE FROM spins WHERE id = ?", (row["id"],))
        self.conn.commit()
        return row["number"]

    # --- alert ---
    def add_alert(
        self, chat_id: int, category: str, length: int, scope: str, target: str, now: int
    ) -> int | None:
        """Id del nuovo alert, None se identico a uno già esistente nella chat."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO alerts(chat_id, category, length, scope, target, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (chat_id, category, length, scope, target, now),
        )
        self.conn.commit()
        return int(cur.lastrowid) if cur.rowcount == 1 else None

    def alerts(self, chat_id: int | None = None) -> list[Alert]:
        if chat_id is None:
            rows = self.conn.execute("SELECT * FROM alerts ORDER BY id").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM alerts WHERE chat_id = ? ORDER BY id", (chat_id,)
            ).fetchall()
        return [
            Alert(r["id"], r["chat_id"], r["category"], r["length"], r["scope"], r["target"])
            for r in rows
        ]

    def delete_alert(self, chat_id: int, alert_id: int) -> bool:
        """Ogni chat può cancellare solo i propri alert."""
        cur = self.conn.execute(
            "DELETE FROM alerts WHERE id = ? AND chat_id = ?", (alert_id, chat_id)
        )
        self.conn.commit()
        return cur.rowcount == 1
