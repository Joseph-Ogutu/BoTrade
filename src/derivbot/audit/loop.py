"""Runs the accuracy audit on a schedule inside the bot, stores the verdict, and alerts when a check fails.

Read only. It reuses the bot's own connected demo session, so no extra login is made. The verdict is kept in the key value
store (7 days) for the dashboard. If the session is not connected, or there are no trades yet, it skips and tries again at
the next interval: a skipped audit is shown as "pending", never as a pass.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from derivbot.audit.accuracy import Check

log = logging.getLogger(__name__)
KEY = "audit:last"


def verdict(checks: list[Check], trades: int, now: dt.datetime) -> dict[str, Any]:
    failed = [c for c in checks if not c.passed]
    return {"at": now.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "trades": trades, "checks": len(checks),
            "passed": not failed, "failed": [{"name": c.name, "detail": c.detail[:200]} for c in failed]}


def store_verdict(kv: Any, checks: list[Check], trades: int, now: dt.datetime) -> dict[str, Any]:
    """Save a verdict for the dashboard (7 days). Shared by the scheduled loop and the command line --store option."""
    v = verdict(checks, trades, now)
    kv.set(KEY, json.dumps(v), 7 * 86400)
    return v


class AuditLoop:
    def __init__(self, run: Callable[[], Awaitable[tuple[list[Check], int]]], kv: Any, alert: Callable[[str, str], None],
                 session_ready: Callable[[], bool], interval_s: float = 6 * 3600.0, first_delay_s: float = 600.0,
                 clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC)) -> None:
        self.run_audit, self.kv, self.alert, self.session_ready = run, kv, alert, session_ready
        self.interval_s, self.first_delay_s, self.clock = interval_s, first_delay_s, clock

    async def run_once(self) -> dict[str, Any] | None:
        if not self.session_ready():
            return None
        checks, n = await self.run_audit()
        if not n:
            return None
        v = store_verdict(self.kv, checks, n, self.clock())
        if v["passed"]:
            log.info("accuracy audit: all %d checks passed over %d trades", v["checks"], n)
        else:
            names = ", ".join(f["name"] for f in v["failed"])
            log.error("accuracy audit FAILED: %s", names)
            self.alert("critical", f"accuracy audit FAILED over {n} trades: {names}. Records and exchange disagree: investigate before "
                                   "trusting any numbers; see docs/runbook.md")
        return v

    async def run(self, halt: asyncio.Event) -> None:
        delay = self.first_delay_s
        while not halt.is_set():
            try:
                await asyncio.wait_for(halt.wait(), timeout=delay)
                return
            except TimeoutError:
                pass
            try:
                await self.run_once()
            except Exception as exc:  # noqa: BLE001 - an audit problem must never stop the service
                log.warning("accuracy audit could not run: %s", type(exc).__name__)
            delay = self.interval_s


def last_verdict(kv: Any) -> dict[str, Any] | None:
    try:
        raw = kv.get(KEY)
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None
