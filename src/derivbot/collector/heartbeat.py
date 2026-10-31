"""Heartbeat file, heartbeat log line and the health decision used by the Docker healthcheck."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path


class Heartbeat:
    def __init__(self, symbols: list[str], path: str | Path | None, clock: Callable[[], float] = time.time) -> None:
        self._symbols = list(symbols)
        self._path = Path(path) if path else None
        self._clock = clock
        self._last: dict[str, float] = {}
        self.ticks_total = 0
        self.started_at = clock()

    def record(self, symbol: str) -> None:
        self._last[symbol] = self._clock()
        self.ticks_total += 1

    def last_tick(self, symbol: str) -> float | None:
        return self._last.get(symbol)

    def snapshot(self) -> dict:
        return {
            "written_at": self._clock(),
            "started_at": self.started_at,
            "ticks_total": self.ticks_total,
            "last_tick": dict(self._last),
        }

    def write(self) -> None:
        """Atomic write (temp file + rename) so the healthcheck never reads a half-written file."""
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.snapshot()))
        os.replace(tmp, self._path)

    def log_line(self, connects: int, reconnects: int, errors: int) -> str:
        now = self._clock()
        ages = " ".join(
            f"age_{s}={now - self._last[s]:.1f}" if s in self._last else f"age_{s}=never" for s in self._symbols
        )
        return (f"HEARTBEAT ticks_total={self.ticks_total} connects={connects} "
                f"reconnects={reconnects} errors={errors} {ages}")


def check_health(path: str | Path, required_symbols: list[str], max_age_s: float,
                 now: float | None = None) -> tuple[bool, str]:
    """Healthy only if the heartbeat file is fresh AND every required symbol ticked within max_age_s.

    Closed-market symbols are deliberately not in required_symbols, so a weekend does not fail the check.
    """
    now = time.time() if now is None else now
    try:
        data = json.loads(Path(path).read_text())
        written_at = float(data["written_at"])
        last = data["last_tick"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return False, f"heartbeat unreadable: {type(exc).__name__}"
    if now - written_at > max_age_s:
        return False, f"heartbeat file stale by {now - written_at:.0f}s (process not running?)"
    for symbol in required_symbols:
        ts = last.get(symbol)
        if ts is None:
            return False, f"{symbol}: no tick received yet"
        if now - float(ts) > max_age_s:
            return False, f"{symbol}: last tick {now - float(ts):.0f}s ago (limit {max_age_s:.0f}s)"
    return True, "ok"
