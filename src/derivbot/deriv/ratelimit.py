"""Client-side rate limiter for the Deriv WebSocket (HANDBOOK 2.4, docs.limits).

Limits are configuration, not constants: Deriv reserves the right to change them.
Calls are counted per group with sliding windows. A safety margin keeps the
client below the published limits. The server's own rejection should still be
handled by the caller, which can call penalize() to back a group off.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class LimitConfig:
    per_minute: int
    per_hour: int
    per_second: int | None = None

    def __post_init__(self) -> None:
        if self.per_minute < 1 or self.per_hour < 1 or (self.per_second is not None and self.per_second < 1):
            raise ValueError("limits must be positive")

    def windows(self) -> list[tuple[int, float]]:
        out = [(self.per_minute, 60.0), (self.per_hour, 3600.0)]
        if self.per_second is not None:
            out.append((self.per_second, 1.0))
        return out


# Published limits, read 2026-10-31 (UTC) at https://developers.deriv.com/docs/limits/
DEFAULT_LIMITS: dict[str, LimitConfig] = {
    "trading": LimitConfig(per_minute=360, per_hour=14_400),
    "account": LimitConfig(per_minute=100, per_hour=2_000),
    "portfolio": LimitConfig(per_minute=30, per_hour=1_500),
    # OBSERVED 2026-11-06 (UTC): on a connection with no client limit the server answered error code RateLimit, message
    # "You have reached the rate limit for proposal_open_contract.", after about 97 such requests in 57 seconds. That is far
    # below the published "trading" budget of 360 a minute, so proposal_open_contract evidently has its own, lower budget.
    # 60 a minute leaves a margin (before the 0.8 safety factor). The exact server value is UNVERIFIED.
    "contract": LimitConfig(per_minute=60, per_hour=1_800),
    "other": LimitConfig(per_minute=220, per_hour=14_400),
    # ping is outside the budgets: 10 per second per connection
    "ping": LimitConfig(per_minute=600, per_hour=36_000, per_second=10),
}

_GROUPS = {
    "proposal": "trading",
    "proposal_open_contract": "contract",
    "buy": "trading",
    "sell": "trading",
    "balance": "account",
    "statement": "account",
    "portfolio": "portfolio",
    "profit_table": "portfolio",
    "ping": "ping",
}


def group_for(msg_type: str) -> str:
    return _GROUPS.get(msg_type, "other")


class RateLimiter:
    def __init__(
        self,
        limits: dict[str, LimitConfig],
        margin: float = 0.8,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not 0 < margin <= 1:
            raise ValueError("margin must be in (0, 1]")
        self._clock = clock
        self._sleep = sleep
        self._limits = {
            g: [(max(1, int(n * margin)), w) for n, w in cfg.windows()] for g, cfg in limits.items()
        }
        self._events: dict[str, list[deque[float]]] = {
            g: [deque() for _ in wins] for g, wins in self._limits.items()
        }
        self._blocked_until: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _resolve(self, msg_type: str) -> str:
        group = group_for(msg_type)
        return group if group in self._limits else "other"

    def _wait_needed(self, group: str, now: float) -> float:
        wait = max(0.0, self._blocked_until.get(group, 0.0) - now)
        for (limit, window), events in zip(self._limits[group], self._events[group]):
            while events and events[0] <= now - window:
                events.popleft()
            if len(events) >= limit:
                wait = max(wait, events[len(events) - limit] + window - now)
        return wait

    def _record(self, group: str, now: float) -> None:
        for events in self._events[group]:
            events.append(now)

    def try_acquire(self, msg_type: str) -> bool:
        group = self._resolve(msg_type)
        now = self._clock()
        if self._wait_needed(group, now) > 0:
            return False
        self._record(group, now)
        return True

    async def acquire(self, msg_type: str) -> None:
        group = self._resolve(msg_type)
        lock = self._locks.setdefault(group, asyncio.Lock())
        async with lock:
            while True:
                wait = self._wait_needed(group, self._clock())
                if wait <= 0:
                    self._record(group, self._clock())
                    return
                await self._sleep(wait)

    def penalize(self, msg_type_or_group: str, seconds: float) -> None:
        """Block a group for `seconds`, e.g. after the server rejected a call as rate-limited.

        UNVERIFIED-ERR: the rate-limit rejection code and shape are not known; no caller detects them yet.
        """
        group = msg_type_or_group if msg_type_or_group in self._limits else self._resolve(msg_type_or_group)
        self._blocked_until[group] = max(self._blocked_until.get(group, 0.0), self._clock() + seconds)
