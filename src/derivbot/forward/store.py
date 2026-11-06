"""Writes forward ledger rows to Postgres without ever blocking the tick loop, and reads the counts back.

Rows are queued in memory by the tick handler and flushed in batches by a background task. A failed write keeps the rows and tries
again, because a lost outcome would silently change what the test measured (Amendment 5, A5.4). INSERT ... ON CONFLICT DO NOTHING on
(variant, signal_epoch) makes a retry harmless. If the queue grows (the database is down) an alert is raised once.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable

from sqlalchemy import Connection, Engine, text

from derivbot.forward.shadow import Outcome

log = logging.getLogger(__name__)
ALERT_QUEUE = 500
MAX_QUEUE = 100_000


class LedgerStore:
    def __init__(self, engine: Engine, alert: Callable[[str, str], None] = lambda lvl, txt: None) -> None:
        self.engine, self.alert = engine, alert
        self._outcomes: list[Outcome] = []
        self._discards: list[tuple[str, int, str]] = []
        self._lock = threading.Lock()
        self._alerted = False
        self.dropped = 0

    def push_outcome(self, o: Outcome) -> None:
        with self._lock:
            if len(self._outcomes) >= MAX_QUEUE:
                self.dropped += 1                                   # only if the database has been down for a very long time
                return
            self._outcomes.append(o)

    def push_discard(self, variant: str, signal_epoch: int, reason: str) -> None:
        with self._lock:
            self._discards.append((variant, signal_epoch, reason))

    def pending(self) -> int:
        with self._lock:
            return len(self._outcomes) + len(self._discards)

    def flush(self) -> int:
        """Write everything queued. Returns the number of rows written; on a database error the rows stay queued."""
        with self._lock:
            outs, dis = list(self._outcomes), list(self._discards)
        if not outs and not dis:
            return 0
        try:
            with self.engine.begin() as c:
                for o in outs:
                    c.execute(text(
                        "INSERT INTO shadow_outcomes (variant, strategy_id, strategy_version, symbol, side, signal_epoch, entry_epoch, "
                        "exit_epoch, entry_quote, exit_quote, won) VALUES (:v, :sid, :ver, :sym, :side, :se, :ee, :xe, :eq, :xq, :w) "
                        "ON CONFLICT (variant, signal_epoch) DO NOTHING"),
                        {"v": o.variant, "sid": o.strategy_id, "ver": o.strategy_version, "sym": o.symbol, "side": o.side,
                         "se": o.signal_epoch, "ee": o.entry_epoch, "xe": o.exit_epoch, "eq": o.entry_quote, "xq": o.exit_quote,
                         "w": o.won})
                for v, e, r in dis:
                    c.execute(text("INSERT INTO shadow_discards (variant, signal_epoch, reason) VALUES (:v, :e, :r)"),
                              {"v": v, "e": e, "r": r})
        except Exception as exc:  # noqa: BLE001
            log.warning("forward ledger write failed (%d rows kept): %s", len(outs) + len(dis), type(exc).__name__)
            if self.pending() >= ALERT_QUEUE and not self._alerted:
                self._alerted = True
                self.alert("critical", f"forward test ledger cannot be written to the database ({self.pending()} rows waiting)")
            return 0
        with self._lock:
            del self._outcomes[: len(outs)]
            del self._discards[: len(dis)]
        self._alerted = False
        return len(outs) + len(dis)

    async def run(self, halt: asyncio.Event, interval_s: float = 2.0) -> None:
        while not halt.is_set():
            await asyncio.to_thread(self.flush)
            try:
                await asyncio.wait_for(halt.wait(), timeout=interval_s)
            except TimeoutError:
                pass
        await asyncio.to_thread(self.flush)                         # last rows on the way out


def counts(conn: Connection) -> dict[str, dict[str, int]]:
    """Per variant: completed and discarded contracts. Counts only: nothing here reveals an outcome."""
    out: dict[str, dict[str, int]] = {}
    for variant, n in conn.execute(text("SELECT variant, count(*) FROM shadow_outcomes GROUP BY 1")):
        out.setdefault(variant, {"completed": 0, "discarded": 0})["completed"] = int(n)
    for variant, n in conn.execute(text("SELECT variant, count(*) FROM shadow_discards GROUP BY 1")):
        out.setdefault(variant, {"completed": 0, "discarded": 0})["discarded"] = int(n)
    return out
