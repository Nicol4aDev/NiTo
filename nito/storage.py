"""Persistenza su SQLite: segnali, trade e stato dei blocchi di sicurezza."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .models import Setup, Side, Signal, Trade, TradeStatus

SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    side TEXT NOT NULL,
    setups TEXT NOT NULL,
    entry REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL NOT NULL,
    break_even_trigger REAL,
    candle_ts INTEGER NOT NULL,
    trend_up INTEGER NOT NULL,
    fee_pct REAL NOT NULL,
    status TEXT NOT NULL,
    expires_at INTEGER,
    note TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER,
    mode TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    entry REAL NOT NULL,
    stop_loss REAL NOT NULL,
    initial_stop REAL NOT NULL,
    take_profit REAL NOT NULL,
    break_even_trigger REAL,
    moved_to_be INTEGER NOT NULL DEFAULT 0,
    opened_at INTEGER NOT NULL,
    status TEXT NOT NULL,
    exit_price REAL,
    closed_at INTEGER,
    pnl REAL,
    exit_reason TEXT,
    sl_order_id TEXT,
    tp_order_id TEXT,
    extra TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class StoredSignal:
    signal: Signal
    status: str
    expires_at: int | None
    note: str | None


class Store:
    def __init__(self, path: str | Path = ":memory:", mode: str = "signals"):
        self.mode = mode
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- stato chiave/valore ---
    # Le chiavi sono separate per modalità: passare da paper a live con lo stesso
    # database non deve ereditare capitale virtuale, blocchi o perdita giornaliera.
    def get_state(self, key: str, default: str = "") -> str:
        row = self.conn.execute(
            "SELECT value FROM state WHERE key = ?", (f"{self.mode}:{key}",)
        ).fetchone()
        return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO state(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (f"{self.mode}:{key}", value),
        )
        self.conn.commit()

    # --- segnali ---
    def save_signal(
        self, sig: Signal, status: str, created_at: int, expires_at: int | None = None
    ) -> int:
        setups = json.dumps([[s.name, s.side.value, s.reason] for s in sig.setups])
        cur = self.conn.execute(
            "INSERT INTO signals(created_at, symbol, timeframe, side, setups, entry, stop_loss, "
            "take_profit, break_even_trigger, candle_ts, trend_up, fee_pct, status, expires_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                created_at,
                sig.symbol,
                sig.timeframe,
                sig.side.value,
                setups,
                sig.entry,
                sig.stop_loss,
                sig.take_profit,
                sig.break_even_trigger,
                sig.candle_ts,
                int(sig.trend_up),
                sig.fee_pct,
                status,
                expires_at,
            ),
        )
        self.conn.commit()
        sig.id = int(cur.lastrowid)
        return sig.id

    def set_signal_status(self, signal_id: int, status: str, note: str | None = None) -> None:
        self.conn.execute(
            "UPDATE signals SET status = ?, note = COALESCE(?, note) WHERE id = ?",
            (status, note, signal_id),
        )
        self.conn.commit()

    def claim_pending_signal(self, signal_id: int, new_status: str) -> bool:
        """Passa un segnale da 'pending' a `new_status` in modo atomico (evita doppi click)."""
        cur = self.conn.execute(
            "UPDATE signals SET status = ? WHERE id = ? AND status = 'pending'",
            (new_status, signal_id),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def get_signal(self, signal_id: int) -> StoredSignal | None:
        row = self.conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
        if row is None:
            return None
        setups = [Setup(n, Side(sd), r) for n, sd, r in json.loads(row["setups"])]
        sig = Signal(
            symbol=row["symbol"],
            timeframe=row["timeframe"],
            side=Side(row["side"]),
            setups=setups,
            entry=row["entry"],
            stop_loss=row["stop_loss"],
            take_profit=row["take_profit"],
            break_even_trigger=row["break_even_trigger"],
            candle_ts=row["candle_ts"],
            trend_up=bool(row["trend_up"]),
            fee_pct=row["fee_pct"],
            id=row["id"],
        )
        return StoredSignal(sig, row["status"], row["expires_at"], row["note"])

    def expire_pending(self, now_ms: int) -> list[int]:
        rows = self.conn.execute(
            "SELECT id FROM signals WHERE status = 'pending' AND expires_at IS NOT NULL "
            "AND expires_at < ?",
            (now_ms,),
        ).fetchall()
        ids = [r["id"] for r in rows]
        if ids:
            self.conn.executemany(
                "UPDATE signals SET status = 'expired' WHERE id = ? AND status = 'pending'",
                [(i,) for i in ids],
            )
            self.conn.commit()
        return ids

    # --- trade ---
    def insert_trade(self, t: Trade) -> int:
        cur = self.conn.execute(
            "INSERT INTO trades(signal_id, mode, symbol, side, qty, entry, stop_loss, initial_stop, "
            "take_profit, break_even_trigger, moved_to_be, opened_at, status, exit_price, closed_at, "
            "pnl, exit_reason, sl_order_id, tp_order_id, extra) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                t.signal_id,
                self.mode,
                t.symbol,
                t.side.value,
                t.qty,
                t.entry,
                t.stop_loss,
                t.initial_stop,
                t.take_profit,
                t.break_even_trigger,
                int(t.moved_to_be),
                t.opened_at,
                t.status.value,
                t.exit_price,
                t.closed_at,
                t.pnl,
                t.exit_reason,
                t.sl_order_id,
                t.tp_order_id,
                json.dumps(t.extra),
            ),
        )
        self.conn.commit()
        t.id = int(cur.lastrowid)
        return t.id

    def update_trade(self, t: Trade) -> None:
        if t.id is None:
            raise ValueError("trade senza id")
        self.conn.execute(
            "UPDATE trades SET qty=?, entry=?, stop_loss=?, take_profit=?, break_even_trigger=?, "
            "moved_to_be=?, status=?, exit_price=?, closed_at=?, pnl=?, exit_reason=?, "
            "sl_order_id=?, tp_order_id=?, extra=? WHERE id=?",
            (
                t.qty,
                t.entry,
                t.stop_loss,
                t.take_profit,
                t.break_even_trigger,
                int(t.moved_to_be),
                t.status.value,
                t.exit_price,
                t.closed_at,
                t.pnl,
                t.exit_reason,
                t.sl_order_id,
                t.tp_order_id,
                json.dumps(t.extra),
                t.id,
            ),
        )
        self.conn.commit()

    @staticmethod
    def _trade_from_row(row: sqlite3.Row) -> Trade:
        return Trade(
            id=row["id"],
            signal_id=row["signal_id"],
            symbol=row["symbol"],
            side=Side(row["side"]),
            qty=row["qty"],
            entry=row["entry"],
            stop_loss=row["stop_loss"],
            initial_stop=row["initial_stop"],
            take_profit=row["take_profit"],
            break_even_trigger=row["break_even_trigger"],
            moved_to_be=bool(row["moved_to_be"]),
            opened_at=row["opened_at"],
            status=TradeStatus(row["status"]),
            exit_price=row["exit_price"],
            closed_at=row["closed_at"],
            pnl=row["pnl"],
            exit_reason=row["exit_reason"],
            sl_order_id=row["sl_order_id"],
            tp_order_id=row["tp_order_id"],
            extra=json.loads(row["extra"] or "{}"),
        )

    def open_trades(self) -> list[Trade]:
        rows = self.conn.execute(
            "SELECT * FROM trades WHERE status = 'open' AND mode = ? ORDER BY id", (self.mode,)
        ).fetchall()
        return [self._trade_from_row(r) for r in rows]

    def closed_trades(self, limit: int | None = None) -> list[Trade]:
        sql = "SELECT * FROM trades WHERE status = 'closed' AND mode = ? ORDER BY closed_at DESC"
        params: tuple = (self.mode,)
        if limit:
            sql += " LIMIT ?"
            params = (self.mode, limit)
        return [self._trade_from_row(r) for r in self.conn.execute(sql, params).fetchall()]
