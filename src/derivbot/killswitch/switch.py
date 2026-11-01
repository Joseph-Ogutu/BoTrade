"""Kill switch (HANDBOOK 10.4).

Engaging NEVER depends on a Deriv connection: this module imports nothing from the Deriv adapter (a test enforces it).
It does not even depend on Redis or Postgres. State is kept in four places and ANY of them saying "engaged" wins:

  1. memory in this process        (immediate, cannot fail)
  2. the flag file on the host     (survives a crash and a total loss of Redis and Postgres)
  3. Redis key kill_switch         (shared with other processes)
  4. kill_switch_events in Postgres (the durable, auditable record)

Engage writes all four on a best-effort basis and only the first cannot fail, so an outage of any store cannot stop an
engage. Release is the opposite: it is refused unless it can be logged, because a release without a record is the
dangerous direction. A deleted flag file or a flushed Redis does not release anything.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from sqlalchemy import Engine, text

from derivbot.common.redaction import redact_text
from derivbot.db.audit import append_audit
from derivbot.kv.store import KV

KEY = "kill_switch"
SOURCES = frozenset({"telegram", "api", "flag_file", "executor_exception", "reconciler", "boot", "cli", "risk"})


class EventLog(Protocol):
    def append(self, action: str, source: str, reason: str) -> None: ...
    def last_action(self) -> str | None: ...


class MemoryEventLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str]] = []

    def append(self, action: str, source: str, reason: str) -> None:
        self.events.append((action, source, reason))

    def last_action(self) -> str | None:
        return self.events[-1][0] if self.events else None


class PgEventLog:
    """kill_switch_events plus an audit_events row, in one transaction."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append(self, action: str, source: str, reason: str) -> None:
        with self._engine.begin() as c:
            c.execute(text("INSERT INTO kill_switch_events (action, source, reason) VALUES (:a, :s, :r)"),
                      {"a": action, "s": source, "r": reason})
            append_audit(c, "killswitch", "KILL_SWITCH", {"action": action, "source": source, "reason": reason})

    def last_action(self) -> str | None:
        with self._engine.connect() as c:
            return c.execute(text("SELECT action FROM kill_switch_events ORDER BY id DESC LIMIT 1")).scalar()


def _clean(reason: str) -> str:
    return redact_text(reason.strip())[:200]


class KillSwitch:
    def __init__(self, kv: KV, log: EventLog, flag_path: str | Path) -> None:
        self.kv, self.log, self.flag_path = kv, log, Path(flag_path)
        self._mem = False
        self._degraded: dict[str, str] = {}
        self._pending: list[tuple[str, str, str]] = []      # events that could not be logged yet

    # ------------------------------------------------------------------ reading
    def _file_present(self) -> bool:
        try:
            return self.flag_path.exists()
        except OSError:
            return True        # cannot even stat the marker: fail closed

    def is_engaged(self) -> bool:
        if self._mem or self._file_present():
            return True
        try:
            value = self.kv.get(KEY)
        except Exception as exc:  # noqa: BLE001 - any store failure means "cannot verify", which fails closed
            self._degraded["redis_read"] = f"cannot read kill switch from redis ({type(exc).__name__})"
            return True
        self._degraded.pop("redis_read", None)
        return value == "1"

    # ------------------------------------------------------------------ writing helpers (never raise)
    def _write_file(self) -> None:
        try:
            self.flag_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.flag_path.with_suffix(".tmp")
            tmp.write_text("engaged\n")
            os.replace(tmp, self.flag_path)
            self._degraded.pop("flag_file", None)
        except OSError as exc:
            self._degraded["flag_file"] = f"cannot write flag file ({type(exc).__name__})"

    def _write_kv(self, value: str) -> bool:
        try:
            self.kv.set(KEY, value)
            self._degraded.pop("redis_write", None)
            return True
        except Exception as exc:  # noqa: BLE001
            self._degraded["redis_write"] = f"cannot write kill switch to redis ({type(exc).__name__})"
            return False

    def _log(self, action: str, source: str, reason: str) -> bool:
        try:
            self.log.append(action, source, reason)
            self._degraded.pop("event_log", None)
            return True
        except Exception as exc:  # noqa: BLE001
            self._degraded["event_log"] = f"cannot log kill switch event ({type(exc).__name__})"
            self._pending.append((action, source, reason))
            return False

    # ------------------------------------------------------------------ operations
    def engage(self, source: str, reason: str) -> None:
        if source not in SOURCES:
            raise ValueError(f"unknown kill switch source {source!r}")
        self._mem = True                       # first, and it cannot fail
        self._write_file()
        self._write_kv("1")
        self._log("engage", source, _clean(reason))

    def release(self, source: str, reason: str) -> None:
        if source not in SOURCES:
            raise ValueError(f"unknown kill switch source {source!r}")
        if not reason or not reason.strip():
            raise ValueError("a release needs a reason")
        reason = _clean(reason)
        try:
            self.log.append("release", source, reason)      # a release that cannot be logged does not happen
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"release not logged, switch stays engaged ({type(exc).__name__})") from exc
        self._degraded.pop("event_log", None)
        self._mem = False
        try:
            self.flag_path.unlink(missing_ok=True)
        except OSError:
            pass
        if not self._write_kv("0") or self._file_present():
            # The record says released but a store still says engaged: record that truth and stay engaged.
            self._mem = True
            self._log("engage", "boot", "release incomplete: a store could not be cleared")
            raise RuntimeError("release incomplete: a store could not be cleared, switch stays engaged")

    def sync(self) -> None:
        """Called every second by a watcher: flush unlogged events, honour the flag file, restore the marker."""
        self._flush_pending()
        if self._file_present() and not self._mem:
            self.engage("flag_file", "flag file present")
            return
        if self._mem and not self._file_present():
            self._write_file()
        if not self._mem:
            try:
                if self.kv.get(KEY) == "1":
                    self._mem = True           # another process engaged: adopt it (it logged its own event)
            except Exception:  # noqa: BLE001
                pass

    def _flush_pending(self) -> None:
        while self._pending:
            action, source, reason = self._pending[0]
            try:
                self.log.append(action, source, reason)
            except Exception:  # noqa: BLE001
                return
            self._pending.pop(0)
            self._degraded.pop("event_log", None)

    def load_on_boot(self) -> None:
        """Restore from whatever survived. Any store saying engaged wins; if history cannot be read, fail closed."""
        evidence = []
        try:
            last = self.log.last_action()
            if last == "engage":
                evidence.append("event log")
        except Exception as exc:  # noqa: BLE001
            self._degraded["event_log"] = f"cannot read kill switch history ({type(exc).__name__})"
            evidence.append("history unreadable (failing closed)")
            last = None
        try:
            if self.kv.get(KEY) == "1":
                evidence.append("redis")
        except Exception as exc:  # noqa: BLE001
            self._degraded["redis_read"] = f"cannot read kill switch from redis ({type(exc).__name__})"
        if self._file_present():
            evidence.append("flag file")
        if not evidence:
            self._mem = False
            self._write_kv("0")
            return
        self._mem = True
        self._write_file()
        self._write_kv("1")
        if last != "engage":
            self._log("engage", "boot", f"restored on boot from: {', '.join(evidence)}")

    def status(self) -> dict[str, object]:
        engaged = self.is_engaged()
        try:
            last = self.log.last_action()
        except Exception:  # noqa: BLE001
            last = None
        return {"engaged": engaged, "degraded": sorted(self._degraded.values()), "last_logged_action": last,
                "pending_log_events": len(self._pending)}


async def watch_flag_file(ks: KillSwitch, halt, interval_s: float = 1.0) -> None:
    """Poll the flag file and shared stores. A flag file is the path that works when nothing else does."""
    import asyncio

    while not halt.is_set():
        ks.sync()
        try:
            await asyncio.wait_for(halt.wait(), timeout=interval_s)
        except TimeoutError:
            pass
