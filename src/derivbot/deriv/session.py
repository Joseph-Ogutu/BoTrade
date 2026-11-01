"""Authenticated Deriv WebSocket session and its reconnect supervisor.

EVERYTHING about authenticated behaviour here is UNVERIFIED until demo credentials exist: OTP lifetime and reuse
errors, whether subscriptions survive a reconnect, the handshake failure shape. The session never logs its URL (an OTP
URL is a secret) and obtains a FRESH url for every connection through the url_provider, because an OTP is single-use
(docs, 120 s) and must never be reused.

A request that is pending when the connection closes fails with ConnectionLost. It is never retried here: whether to
retry anything is the caller's decision, and for a buy the answer is always no.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any

import websockets

from derivbot.common.redaction import redact_text
from derivbot.deriv.ratelimit import RateLimiter

log = logging.getLogger(__name__)


class ConnectionLost(Exception):
    pass


class RequestTimeout(Exception):
    pass


class DerivSession:
    def __init__(self, url_provider: Callable[[], Awaitable[str]], limiter: RateLimiter | None = None,
                 connect_timeout: float = 10.0, endpoint_classifier: Callable[[str], str | None] | None = None) -> None:
        self._url_provider = url_provider
        self._classifier = endpoint_classifier
        self.endpoint_mode: str | None = None       # set at connect from the URL; the URL itself is never exposed
        self._limiter = limiter
        self._connect_timeout = connect_timeout
        self._ws: Any = None
        self._reader: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._handlers: dict[str, list[Callable[[dict], None]]] = {}
        self._next_id = 0
        self._closed = asyncio.Event()
        self._closed.set()

    # ------------------------------------------------------------------ lifecycle
    async def connect(self) -> None:
        url = await self._url_provider()                  # a fresh OTP URL for every connection
        self.endpoint_mode = self._classifier(url) if self._classifier else None
        self._ws = await websockets.connect(url, open_timeout=self._connect_timeout, ping_interval=20, ping_timeout=20)
        self._closed.clear()
        self._reader = asyncio.create_task(self._read_loop(), name="deriv-session-reader")

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._closed.is_set()

    async def wait_closed(self) -> None:
        await self._closed.wait()

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
        if self._reader is not None:
            try:
                await asyncio.wait_for(self._reader, 2)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    log.warning("unparseable message ignored")
                    continue
                rid = msg.get("req_id")
                fut = self._pending.pop(rid, None) if isinstance(rid, int) else None
                if fut is not None and not fut.done():
                    fut.set_result(msg)
                    continue
                # Unsolicited, duplicate or late reply: route by type. A duplicate reply to a finished request
                # lands here and is ignored by design, so a duplicated "buy" reply cannot buy twice.
                for handler in self._handlers.get(str(msg.get("msg_type")), ()):
                    try:
                        handler(msg)
                    except Exception:  # noqa: BLE001
                        log.exception("message handler failed")
        except websockets.ConnectionClosed:
            pass
        except Exception as exc:  # noqa: BLE001
            log.warning("session read loop ended: %s", redact_text(f"{type(exc).__name__}: {exc}"))
        finally:
            self._closed.set()
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionLost("connection closed with a request pending"))
            self._pending.clear()

    # ------------------------------------------------------------------ requests
    def next_req_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def on(self, msg_type: str, handler: Callable[[dict], None]) -> None:
        self._handlers.setdefault(msg_type, []).append(handler)

    async def request(self, req: dict[str, Any], timeout: float = 10.0) -> dict[str, Any]:
        """Send one request and wait for the reply with the same req_id. Never retries."""
        if not self.connected:
            raise ConnectionLost("not connected")
        msg_type = next((k for k in req if k not in ("req_id", "subscribe", "passthrough")), "other")
        if self._limiter is not None:
            await self._limiter.acquire(msg_type)
        rid = req.get("req_id")
        if not isinstance(rid, int):
            rid = self.next_req_id()
            req = {**req, "req_id": rid}
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            try:
                await self._ws.send(json.dumps(req))
            except websockets.ConnectionClosed as exc:
                raise ConnectionLost("connection closed while sending") from exc
            try:
                return await asyncio.wait_for(fut, timeout)
            except asyncio.TimeoutError:
                raise RequestTimeout(f"no reply to {msg_type} within {timeout}s") from None
        finally:
            self._pending.pop(rid, None)

    async def send(self, req: dict[str, Any]) -> None:
        """Fire and forget (for example forget). Raises ConnectionLost if the socket is gone."""
        if not self.connected:
            raise ConnectionLost("not connected")
        try:
            await self._ws.send(json.dumps(req))
        except websockets.ConnectionClosed as exc:
            raise ConnectionLost("connection closed while sending") from exc


class SessionSupervisor:
    """Keeps one session alive: fresh URL per connection, hooks after every (re)connect, backoff with jitter."""

    def __init__(self, session_factory: Callable[[], DerivSession],
                 on_connected: list[Callable[[DerivSession], Awaitable[None]]] | None = None,
                 on_disconnected: list[Callable[[], Awaitable[None]]] | None = None,
                 backoff_base: float = 1.0, backoff_cap: float = 60.0) -> None:
        self._factory = session_factory
        self._on_connected = on_connected or []
        self._on_disconnected = on_disconnected or []
        self._base, self._cap = backoff_base, backoff_cap
        self.session: DerivSession | None = None
        self.connects = 0
        self.last_error: str | None = None

    async def run(self, halt: asyncio.Event) -> None:
        attempt = 0
        while not halt.is_set():
            session = self._factory()
            try:
                await session.connect()
                self.connects += 1
                self.last_error = None
                attempt = 0
                for hook in self._on_connected:       # reconcile BEFORE the session is published to the executor
                    await hook(session)
                self.session = session
                waiter = asyncio.create_task(session.wait_closed())
                stop = asyncio.create_task(halt.wait())
                await asyncio.wait({waiter, stop}, return_when=asyncio.FIRST_COMPLETED)
                for t in (waiter, stop):
                    t.cancel()
            except Exception as exc:  # noqa: BLE001
                self.last_error = redact_text(f"{type(exc).__name__}: {exc}")
                log.warning("session problem: %s", self.last_error)
            finally:
                self.session = None
                await session.close()
                for hook in self._on_disconnected:
                    await hook()
            if halt.is_set():
                break
            delay = min(self._cap, self._base * (2 ** attempt)) * (0.5 + 0.5 * random.random())
            attempt += 1
            try:
                await asyncio.wait_for(halt.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
