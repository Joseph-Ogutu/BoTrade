"""Backfill of holdout ticks via ticks_history, paging backwards, with explicit checks against silent wrong answers.

Facts this code is built on (OBSERVED on the public endpoint, docs/api-notes.md):
- count is clamped to 1000 per call, with no error;
- an `end` older than the retention window (between 12 and 24 hours) does NOT error: the server returns the LATEST
  ticks instead. Such a page would silently put mislabelled ticks into the store, so every page is validated and a
  page with any tick later than the requested `end` is rejected and ends the backfill for that symbol.

Only quotes and epochs are read. No digit statistic is computed here (kill-criterion Amendment 1, A1.4).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from decimal import Decimal

from derivbot.collector.gaps import find_gaps
from derivbot.collector.store import SqliteTickStore
from derivbot.deriv.messages import Tick

HISTORY_LIMIT = 1000


class BackfillError(Exception):
    pass


def decimals_from_pip(pip_size: float) -> int:
    """history returns pip_size as a decimal count (2.0); active_symbols returns it as a step (0.01). Accept both."""
    if pip_size <= 0:
        raise ValueError("pip_size must be positive")
    return int(round(pip_size)) if pip_size >= 1 else int(round(-math.log10(pip_size)))


def quote_from_price(price: float, decimals: int) -> Decimal:
    return Decimal(str(price)).quantize(Decimal(1).scaleb(-decimals))


def validate_page(times: list[int], end: str | int, prev_min: int | None, limit: int = HISTORY_LIMIT) -> None:
    if not times:
        raise BackfillError("empty page")
    if len(times) > limit:
        raise BackfillError(f"page larger than the {limit} tick limit")
    if any(b <= a for a, b in zip(times, times[1:])):
        raise BackfillError("page not strictly increasing")
    if end != "latest" and times[-1] > int(end):
        raise BackfillError(
            f"page contains ticks later than requested end ({times[-1]} > {end}): "
            "the server probably returned the latest window instead of the requested one")
    if prev_min is not None and times[-1] >= prev_min:
        raise BackfillError("page overlaps the previous page")


@dataclass
class BackfillResult:
    symbol: str
    pages: int = 0
    fetched: int = 0
    stored: int = 0
    min_epoch: int | None = None
    max_epoch: int | None = None
    reached_h0: bool = False
    dropped_not_after_h0: int = 0
    problems: list[str] = field(default_factory=list)
    page_log: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class Coverage:
    expected: int
    rows: int
    ratio: float
    gap_count: int
    missing: int
    longest_gap_s: int


def coverage(epochs: list[int], h0: int, last_epoch: int | None, interval: int) -> Coverage:
    eligible = sorted({e for e in epochs if e > h0 and (last_epoch is None or e <= last_epoch)})
    if not eligible:
        return Coverage(0, 0, 0.0, 0, 0, 0)
    last = last_epoch if last_epoch is not None else eligible[-1]
    expected = max(1, math.ceil((last - h0) / interval))  # nominal slots in (h0, last]
    gaps = find_gaps([h0] + eligible, interval)
    return Coverage(
        expected=expected, rows=len(eligible), ratio=len(eligible) / expected, gap_count=len(gaps),
        missing=sum(g.missing for g in gaps),
        longest_gap_s=max((g.end_epoch - g.start_epoch for g in gaps), default=0),
    )


async def _ask(ws, req: dict, rate_limiter=None) -> dict:
    if rate_limiter:
        await rate_limiter.acquire(next(k for k in ("ticks_history", "time") if k in req))
    await ws.send(json.dumps(req))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("req_id") == req["req_id"]:
            return msg


async def backfill_symbol(ws, symbol: str, h0: int, store: SqliteTickStore, rate_limiter=None,
                          max_pages: int = 300, req_id_start: int = 1000) -> BackfillResult:
    """Fetch ticks with h0 < epoch <= now. Stores source=backfill. Never stores a page that fails validation."""
    res = BackfillResult(symbol)
    rid = req_id_start
    rid += 1
    now = (await _ask(ws, {"time": 1, "req_id": rid}, rate_limiter)).get("time")
    end: str | int = "latest"
    prev_min: int | None = None
    while res.pages < max_pages:
        rid += 1
        msg = await _ask(ws, {"ticks_history": symbol, "count": HISTORY_LIMIT, "end": end, "style": "ticks",
                              "req_id": rid}, rate_limiter)
        if "error" in msg:
            res.problems.append(f"server error {msg['error'].get('code')}: stopping")  # UNVERIFIED-ERR: shape only
            break
        hist = msg.get("history") or {}
        times = [int(t) for t in hist.get("times", [])]
        prices = hist.get("prices", [])
        try:
            validate_page(times, end, prev_min)
            if len(prices) != len(times):
                raise BackfillError("prices and times differ in length")
            if end == "latest" and now is not None and int(now) - times[-1] > 30:
                raise BackfillError(f"latest page is stale: newest tick {int(now) - times[-1]} s older than server time")
        except BackfillError as exc:
            res.problems.append(str(exc))
            res.page_log.append({"end": end, "n": len(times), "min": times[0] if times else None,
                                 "max": times[-1] if times else None, "accepted": False, "problem": str(exc)})
            break
        res.pages += 1
        res.fetched += len(times)
        res.page_log.append({"end": end, "n": len(times), "min": times[0], "max": times[-1], "accepted": True})
        dec = decimals_from_pip(float(msg.get("pip_size", 2)))
        eligible = [Tick(symbol=symbol, epoch=t, quote=quote_from_price(p, dec), pip_size=dec)
                    for t, p in zip(times, prices) if t > h0]
        res.dropped_not_after_h0 += sum(1 for t in times if t <= h0)
        res.stored += store.insert_many(eligible, source="backfill")
        if eligible:
            res.min_epoch = min(res.min_epoch, eligible[0].epoch) if res.min_epoch else eligible[0].epoch
            res.max_epoch = max(res.max_epoch or 0, eligible[-1].epoch)
        if times[0] <= h0:
            res.reached_h0 = True
            break
        prev_min, end = times[0], times[0] - 1
    else:
        res.problems.append(f"stopped after {max_pages} pages without reaching the holdout start")
    return res
