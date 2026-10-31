"""Contiguous segments of a tick series (kill-criterion Amendment 2, A2.2).

Two ticks are contiguous iff their epochs differ by at most `max_gap` (= the nominal interval for confirmatory
symbols, so zero missing ticks). Input epochs must be strictly increasing: sorting or deduplicating silently here
could hide a data problem, so unsorted input is an error.
"""

from __future__ import annotations

import numpy as np


def segment_ids(epochs: np.ndarray, max_gap: int) -> np.ndarray:
    epochs = np.asarray(epochs, dtype=np.int64)
    if epochs.size == 0:
        return np.zeros(0, dtype=np.int64)
    if np.any(np.diff(epochs) <= 0):
        raise ValueError("epochs must be strictly increasing (sort and deduplicate first)")
    breaks = np.diff(epochs) > max_gap
    return np.concatenate(([0], np.cumsum(breaks))).astype(np.int64)


def segment_lengths(ids: np.ndarray) -> np.ndarray:
    if ids.size == 0:
        return np.zeros(0, dtype=np.int64)
    return np.bincount(ids)


def split_segments(values: np.ndarray, ids: np.ndarray) -> list[np.ndarray]:
    if ids.size == 0:
        return []
    cuts = np.flatnonzero(np.diff(ids)) + 1
    return np.split(np.asarray(values), cuts)
