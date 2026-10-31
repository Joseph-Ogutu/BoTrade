"""Notifier (HANDBOOK 6.1): alerts, heartbeat, daily report. Delivery problems never stop trading logic.

- every outgoing text is redacted (registered secrets, URL query strings, bearer tokens) and bounded in length;
- the same alert is not sent twice within a window, except it is never swallowed silently: the count is reported;
- a failing transport is counted and reported by status(); alert submission from trading code is non-blocking (a queue).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from derivbot.common.redaction import redact_text
from derivbot.kv.store import HeartbeatKV
from derivbot.notify.transport import Transport, TransportError

log = logging.getLogger(__name__)
LEVELS = {"info": 0, "warning": 1, "critical": 2}
PREFIX = {"info": "INFO", "warning": "WARNING", "critical": "CRITICAL"}
MAX_LEN = 3500


class Notifier:
    def __init__(self, transport: Transport, chat_ids: list[int], min_level: str = "warning",
                 dedupe_window_s: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        if min_level not in LEVELS:
            raise ValueError("unknown level")
        self._t, self._chats, self._min = transport, list(chat_ids), LEVELS[min_level]
        self._window, self._clock = dedupe_window_s, clock
        self._last: dict[tuple[str, str], float] = {}
        self.sent = self.suppressed = self.failed = 0
        self.last_error: str | None = None
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=1000)

    # ------------------------------------------------------------------ sending
    async def alert(self, level: str, text: str) -> bool:
        if level not in LEVELS:
            raise ValueError("unknown level")
        if LEVELS[level] < self._min:
            return False
        body = f"[{PREFIX[level]}] {redact_text(text)}"[:MAX_LEN]
        key, now = (level, body), self._clock()
        if key in self._last and now - self._last[key] < self._window:
            self.suppressed += 1
            return False
        self._last[key] = now
        ok = True
        for chat in self._chats:
            try:
                await self._t.send(chat, body)
                self.sent += 1
            except TransportError as exc:
                ok = False
                self.failed += 1
                self.last_error = redact_text(str(exc))
        return ok

    async def broadcast(self, text: str) -> bool:
        """Informational message (heartbeat, daily report): not subject to the alert level filter or the dedupe window,
        but redacted and bounded like everything else."""
        body = redact_text(text)[:MAX_LEN]
        ok = True
        for chat in self._chats:
            try:
                await self._t.send(chat, body)
                self.sent += 1
            except TransportError as exc:
                ok = False
                self.failed += 1
                self.last_error = redact_text(str(exc))
        return ok

    def submit(self, level: str, text: str) -> None:
        """Non-blocking entry for synchronous code (the executor's on_alert). Drops the oldest on overflow, loudly."""
        try:
            self._queue.put_nowait((level, text))
        except asyncio.QueueFull:
            self.failed += 1
            self.last_error = "alert queue full"

    async def run(self, halt: asyncio.Event) -> None:
        while not halt.is_set():
            try:
                level, text = await asyncio.wait_for(self._queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                continue
            try:
                await self.alert(level, text)
            except Exception:  # noqa: BLE001
                log.exception("alert delivery failed")
        while not self._queue.empty():            # drain on shutdown so a final critical alert is not lost
            level, text = self._queue.get_nowait()
            await self.alert(level, text)

    def status(self) -> dict[str, Any]:
        return {"sent": self.sent, "suppressed": self.suppressed, "failed": self.failed, "last_error": self.last_error}


class Heartbeater:
    """Beats the Redis heartbeat key often (the in-host liveness signal), sends a Telegram heartbeat message rarely, and
    pings an external dead-man's switch each time (so someone notices when the whole process or machine stops)."""

    def __init__(self, hb: HeartbeatKV, component: str, notifier: Notifier | None = None,
                 status_provider: Callable[[], dict[str, Any]] = lambda: {}, interval_s: float = 10.0,
                 message_every_s: float = 3600.0, dead_mans_switch: Callable[[], Awaitable[None]] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.hb, self.component, self.n = hb, component, notifier
        self.status_provider, self.interval_s, self.message_every_s = status_provider, interval_s, message_every_s
        self.dms, self._clock = dead_mans_switch, clock
        self._last_msg: float | None = None
        self.dms_failures = 0

    async def beat_once(self) -> None:
        self.hb.beat(self.component)
        if self.dms is not None:
            try:
                await self.dms()
            except Exception:  # noqa: BLE001 - the ping target being down must not harm trading
                self.dms_failures += 1
        now = self._clock()
        if self.n is not None and (self._last_msg is None or now - self._last_msg >= self.message_every_s):
            self._last_msg = now
            info = ", ".join(f"{k}={v}" for k, v in sorted(self.status_provider().items()))
            await self.n.broadcast(f"heartbeat {self.component}: alive. {info}")

    async def run(self, halt: asyncio.Event) -> None:
        while not halt.is_set():
            await self.beat_once()
            try:
                await asyncio.wait_for(halt.wait(), timeout=self.interval_s)
            except asyncio.TimeoutError:
                pass
