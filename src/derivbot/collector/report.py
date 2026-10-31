"""Uptime and contiguity report. Epochs only: this module never sees a quote or a digit."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from derivbot.common.timeutil import iso_from_epoch
from derivbot.gate1.segments import segment_ids, segment_lengths

DAY = 86400


@dataclass(frozen=True)
class DayRow:
    day: str
    ticks: int
    expected: int
    uptime: float
    gap_count: int
    missing: int
    longest_gap_s: int


@dataclass(frozen=True)
class CoverageSummary:
    ticks: int
    segments: int
    valid_segments: int
    ticks_in_valid_segments: int
    longest_segment: int
    contiguous_fraction: float


def day_rows(epochs: list[int], interval: int, since: int, until: int) -> list[DayRow]:
    """Per UTC day over (since, until]. `expected` counts every nominal slot in the range for that day."""
    ep = np.unique(np.asarray([e for e in epochs if since < e <= until], dtype=np.int64))
    rows: list[DayRow] = []
    day_start = (since + 1) // DAY * DAY
    while day_start <= until:
        lo, hi = max(day_start, since), min(day_start + DAY, until)  # slots in (lo, hi]
        in_day = ep[(ep > day_start - 1) & (ep < day_start + DAY)]
        expected = max(0, math.ceil((hi - lo) / interval))
        if in_day.size > 1:
            d = np.diff(in_day)
            big = d[d > interval]
            gap_count, missing, longest = int(big.size), int((big // interval - 1).sum()), int(big.max()) if big.size else 0
        else:
            gap_count = missing = longest = 0
        if expected or in_day.size:
            rows.append(DayRow(iso_from_epoch(day_start)[:10], int(in_day.size), expected,
                               min(1.0, in_day.size / expected) if expected else 0.0, gap_count, missing, longest))
        day_start += DAY
    return rows


def coverage_summary(epochs: list[int], interval: int, min_segment_len: int = 10) -> CoverageSummary:
    ep = np.unique(np.asarray(epochs, dtype=np.int64))
    if ep.size == 0:
        return CoverageSummary(0, 0, 0, 0, 0, 0.0)
    lengths = segment_lengths(segment_ids(ep, interval))
    valid = lengths[lengths >= min_segment_len]
    return CoverageSummary(
        ticks=int(ep.size), segments=int(lengths.size), valid_segments=int(valid.size),
        ticks_in_valid_segments=int(valid.sum()), longest_segment=int(lengths.max()),
        contiguous_fraction=float(valid.sum() / ep.size),
    )
