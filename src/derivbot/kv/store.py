"""Small key-value interface over Redis (HANDBOOK 8: ephemeral state, never the source of truth for money).

Keys used: kill_switch, lock:intent:{key}, ratelimit:{bucket}, heartbeat:{component}, tick:last:{symbol}.
RedisStore is the real thing; MemoryStore has the same behaviour with an injectable clock for fast unit tests.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Protocol


class KV(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str, ttl_s: float | None = None) -> None: ...
    def set_if_absent(self, key: str, value: str, ttl_s: float | None = None) -> bool: ...
    def delete(self, key: str) -> None: ...
    def delete_if_equals(self, key: str, value: str) -> bool: ...
    def incr(self, key: str, ttl_s: float | None = None) -> int: ...
    def ttl(self, key: str) -> float | None: ...


class MemoryStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._data: dict[str, tuple[str, float | None]] = {}

    def _live(self, key: str) -> tuple[str, float | None] | None:
        item = self._data.get(key)
        if item is None:
            return None
        if item[1] is not None and item[1] <= self._clock():
            del self._data[key]
            return None
        return item

    def get(self, key: str) -> str | None:
        item = self._live(key)
        return item[0] if item else None

    def set(self, key: str, value: str, ttl_s: float | None = None) -> None:
        self._data[key] = (value, None if ttl_s is None else self._clock() + ttl_s)

    def set_if_absent(self, key: str, value: str, ttl_s: float | None = None) -> bool:
        if self._live(key) is not None:
            return False
        self.set(key, value, ttl_s)
        return True

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def delete_if_equals(self, key: str, value: str) -> bool:
        item = self._live(key)
        if item and item[0] == value:
            del self._data[key]
            return True
        return False

    def incr(self, key: str, ttl_s: float | None = None) -> int:
        item = self._live(key)
        if item is None:
            self.set(key, "1", ttl_s)
            return 1
        n = int(item[0]) + 1
        self._data[key] = (str(n), item[1])
        return n

    def ttl(self, key: str) -> float | None:
        item = self._live(key)
        if item is None or item[1] is None:
            return None
        return max(0.0, item[1] - self._clock())


_DELETE_IF_EQUALS = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end"


class RedisStore:
    def __init__(self, client, prefix: str = "derivbot:") -> None:
        self._r = client
        self._p = prefix

    def _k(self, key: str) -> str:
        return self._p + key

    def get(self, key: str) -> str | None:
        v = self._r.get(self._k(key))
        return None if v is None else (v.decode() if isinstance(v, bytes) else v)

    def set(self, key: str, value: str, ttl_s: float | None = None) -> None:
        if ttl_s is None:
            self._r.set(self._k(key), value)
        else:
            self._r.set(self._k(key), value, px=max(1, int(ttl_s * 1000)))

    def set_if_absent(self, key: str, value: str, ttl_s: float | None = None) -> bool:
        px = None if ttl_s is None else max(1, int(ttl_s * 1000))
        return bool(self._r.set(self._k(key), value, nx=True, px=px))

    def delete(self, key: str) -> None:
        self._r.delete(self._k(key))

    def delete_if_equals(self, key: str, value: str) -> bool:
        return bool(self._r.eval(_DELETE_IF_EQUALS, 1, self._k(key), value))

    def incr(self, key: str, ttl_s: float | None = None) -> int:
        pipe = self._r.pipeline()
        pipe.incr(self._k(key))
        if ttl_s is not None:
            pipe.pexpire(self._k(key), max(1, int(ttl_s * 1000)), nx=True)
        return int(pipe.execute()[0])

    def ttl(self, key: str) -> float | None:
        ms = self._r.pttl(self._k(key))
        return None if ms is None or ms < 0 else ms / 1000.0


class IntentLock:
    """lock:intent:{idempotency_key}. Owner-token release, so a late release can never free someone else's lock."""

    def __init__(self, kv: KV, ttl_s: float = 60.0) -> None:
        self._kv, self._ttl = kv, ttl_s

    def acquire(self, idempotency_key: str) -> str | None:
        token = uuid.uuid4().hex
        return token if self._kv.set_if_absent(f"lock:intent:{idempotency_key}", token, self._ttl) else None

    def release(self, idempotency_key: str, token: str) -> bool:
        return self._kv.delete_if_equals(f"lock:intent:{idempotency_key}", token)


class RateCounter:
    """ratelimit:{bucket}. Fixed-window counter shared between processes."""

    def __init__(self, kv: KV, window_s: float) -> None:
        self._kv, self._w = kv, window_s

    def hit(self, bucket: str) -> int:
        return self._kv.incr(f"ratelimit:{bucket}", self._w)


class HeartbeatKV:
    """heartbeat:{component} with a TTL; absence means the component stopped beating."""

    def __init__(self, kv: KV, ttl_s: float = 30.0, clock: Callable[[], float] = time.time) -> None:
        self._kv, self._ttl, self._clock = kv, ttl_s, clock

    def beat(self, component: str) -> None:
        self._kv.set(f"heartbeat:{component}", repr(self._clock()), self._ttl)

    def age(self, component: str) -> float | None:
        v = self._kv.get(f"heartbeat:{component}")
        return None if v is None else max(0.0, self._clock() - float(v))
