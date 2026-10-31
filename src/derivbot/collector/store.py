"""Tick storage. SQLite for local collection; the Postgres `ticks` table (HANDBOOK 8) replaces it in Phase 4.

Same logical schema: PRIMARY KEY (symbol, epoch), so re-delivered ticks are ignored.
Quotes are stored as TEXT to keep the exact decimal value Deriv sent.
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

from derivbot.deriv.messages import Tick

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ticks (
  symbol      TEXT    NOT NULL,
  epoch       INTEGER NOT NULL,
  quote       TEXT    NOT NULL,
  received_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  source      TEXT    NOT NULL DEFAULT 'stream',
  PRIMARY KEY (symbol, epoch)
) WITHOUT ROWID;
"""


class SqliteTickStore:
    def __init__(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        cols = [r[1] for r in self._conn.execute("PRAGMA table_info(ticks)")]
        if "source" not in cols:  # databases created before the column existed: every old row came from the stream
            self._conn.execute("ALTER TABLE ticks ADD COLUMN source TEXT NOT NULL DEFAULT 'stream'")
            self._conn.commit()

    def insert_many(self, ticks: list[Tick], source: str = "stream") -> int:
        """Insert ticks, ignoring duplicates (the first source wins). Returns the number of new rows."""
        before = self._conn.total_changes
        self._conn.executemany(
            "INSERT OR IGNORE INTO ticks (symbol, epoch, quote, source) VALUES (?, ?, ?, ?)",
            [(t.symbol, t.epoch, str(t.quote), source) for t in ticks],
        )
        self._conn.commit()
        return self._conn.total_changes - before

    def count(self, symbol: str) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM ticks WHERE symbol=?", (symbol,)).fetchone()[0]

    def last_epoch(self, symbol: str) -> int | None:
        return self._conn.execute("SELECT MAX(epoch) FROM ticks WHERE symbol=?", (symbol,)).fetchone()[0]

    def epoch_range(self, symbol: str) -> tuple[int, int] | None:
        lo, hi = self._conn.execute(
            "SELECT MIN(epoch), MAX(epoch) FROM ticks WHERE symbol=?", (symbol,)
        ).fetchone()
        return None if lo is None else (lo, hi)

    def epochs(self, symbol: str) -> list[int]:
        return [r[0] for r in self._conn.execute(
            "SELECT epoch FROM ticks WHERE symbol=? ORDER BY epoch", (symbol,))]

    def epochs_and_quotes(self, symbol: str) -> list[tuple[int, Decimal]]:
        return [(r[0], Decimal(r[1])) for r in self._conn.execute(
            "SELECT epoch, quote FROM ticks WHERE symbol=? ORDER BY epoch", (symbol,))]

    def count_by_source(self, symbol: str) -> dict[str, int]:
        return dict(self._conn.execute(
            "SELECT source, COUNT(*) FROM ticks WHERE symbol=? GROUP BY source", (symbol,)).fetchall())

    def close(self) -> None:
        self._conn.close()
