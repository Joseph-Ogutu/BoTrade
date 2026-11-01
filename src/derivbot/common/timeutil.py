"""UTC time helpers. All recorded times in this project are UTC ISO-8601 with a trailing Z.

Why this exists: the dev machine's local zone is Africa/Nairobi (UTC+3), so local date and UTC date differ for three
hours every day, and tools such as `ls -l --time-style` and `git log --date=format:` print LOCAL or commit-offset
times unless told otherwise. A label of Z on a local time is a 3 hour error. Use these helpers, never string-format a
local time.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable

_UTC_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def utc_now_iso(now: Callable[[], dt.datetime] | None = None) -> str:
    n = now() if now else dt.datetime.now(dt.UTC)
    return n.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_from_epoch(epoch: int | float) -> str:
    return dt.datetime.fromtimestamp(int(epoch), dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(text: str) -> dt.datetime:
    """Parse strictly YYYY-MM-DDTHH:MM:SSZ. Anything else (naive, offsets) is rejected."""
    if not isinstance(text, str) or not _UTC_Z.match(text):
        raise ValueError(f"not a UTC ISO-8601 timestamp with Z: {text!r}")
    return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC)


def weekday_name(date_iso: str) -> str:
    return dt.date.fromisoformat(date_iso).strftime("%A")
