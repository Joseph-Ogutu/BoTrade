"""Gap detection for tick series (HANDBOOK 6.1: collector detects gaps)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Gap:
    start_epoch: int  # last tick before the gap
    end_epoch: int  # first tick after the gap
    missing: int  # estimated number of missing ticks


def find_gaps(epochs: list[int], expected_interval: int, tolerance_factor: float = 2.0) -> list[Gap]:
    """Return gaps where consecutive ticks are further apart than expected_interval * tolerance_factor.

    expected_interval is 1 for 1-second indices and 2 for 2-second indices.
    Input may be unsorted and contain duplicates.
    """
    ordered = sorted(set(epochs))
    gaps: list[Gap] = []
    for prev, cur in zip(ordered, ordered[1:]):
        delta = cur - prev
        if delta > expected_interval * tolerance_factor:
            gaps.append(Gap(prev, cur, missing=delta // expected_interval - 1))
    return gaps
