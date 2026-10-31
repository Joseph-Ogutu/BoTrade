"""Reconnecting tick stream for the PUBLIC Deriv endpoint (no auth, no OTP, no App ID).

Public endpoint: wss://api.derivws.com/trading/v1/options/ws/public
(schemas/deriv/ws_public_request.schema.json: "No authentication, OTP, or query parameters are required.")

Behaviour:
- reconnects with exponential backoff and full jitter, resubscribing every symbol each time;
- a connection that is open but silent for `stale_after` seconds is treated as dead and reconnected;
- Deriv error messages are logged and cause a reconnect, never a tight loop;
- URLs are redacted before logging.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import websockets

from derivbot.common.redaction import redact_text
from derivbot.deriv.errors import DerivApiError
from derivbot.deriv.messages import Tick, TickMsg, parse_message, req_ticks
from derivbot.deriv.ratelimit import RateLimiter

log = logging.getLogger(__name__)

PUBLIC_WS_URL = "wss://api.derivws.com/trading/v1/options/ws/public"


@dataclass
class StreamStats:
    connects: int = 0
    reconnects: int = 0
    errors: int = 0
    stalls: int = 0
    ticks: int = 0


async def stream_ticks(
    url: str,
    symbols: list[str],
    halt: asyncio.Event,
    *,
    stats: StreamStats | None = None,
    rate_limiter: RateLimiter | None = None,
    stale_after: float = 30.0,
    backoff_base: float = 1.0,
    backoff_cap: float = 60.0,
    jitter: Callable[[], float] = random.random,
) -> AsyncIterator[Tick]:
    stats = stats if stats is not None else StreamStats()
    attempt = 0
    req_id = 0
    safe_url = redact_text(url)

    while not halt.is_set():
        try:
            async with websockets.connect(url, open_timeout=10, ping_interval=20, ping_timeout=20) as ws:
                stats.connects += 1
                if stats.connects > 1:
                    stats.reconnects += 1
                log.info("connected to %s, subscribing %s", safe_url, symbols)
                for symbol in symbols:
                    req_id += 1
                    if rate_limiter:
                        await rate_limiter.acquire("ticks")
                    await ws.send(json.dumps(req_ticks(symbol, req_id=req_id)))

                last_msg = asyncio.get_running_loop().time()
                while not halt.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=min(stale_after, 0.5))
                    except asyncio.TimeoutError:
                        if asyncio.get_running_loop().time() - last_msg > stale_after:
                            stats.stalls += 1
                            log.warning("no message for %.1fs, reconnecting", stale_after)
                            break
                        continue
                    last_msg = asyncio.get_running_loop().time()
                    try:
                        msg = parse_message(json.loads(raw))
                    except DerivApiError as exc:
                        stats.errors += 1
                        log.warning("server error, reconnecting: %s", redact_text(str(exc)))
                        break
                    except (ValueError, KeyError) as exc:
                        stats.errors += 1
                        log.warning("unparseable message ignored: %s", redact_text(repr(exc)))
                        continue
                    if isinstance(msg, TickMsg):
                        attempt = 0
                        stats.ticks += 1
                        yield msg.tick
        except (websockets.WebSocketException, OSError, asyncio.TimeoutError) as exc:
            stats.errors += 1
            log.warning("connection problem for %s: %s", safe_url, redact_text(f"{type(exc).__name__}: {exc}"))

        if halt.is_set():
            break
        delay = min(backoff_cap, backoff_base * (2 ** attempt)) * (0.5 + 0.5 * jitter())
        attempt += 1
        try:
            await asyncio.wait_for(halt.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass
