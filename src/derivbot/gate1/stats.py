"""The four Gate 1 tests, exactly as registered in docs/kill-criterion.md (A1.5, A2.3).

Inputs are digit arrays (0..9) in time order and segment ids from derivbot.gate1.segments. Nothing here reads a
database or a file: the same functions run on simulated data (controls) and on the one real run.

- uniformity_chi2 uses ALL digits.
- parity_runs, autocorrelation_portmanteau_10 and pair_independence_chi2 use only segments with at least `min_len`
  ticks and never pair ticks from different segments.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from derivbot.gate1.numerics import chi2_sf, norm_sf

UNIFORMITY = "uniformity_chi2"
RUNS = "parity_runs"
AUTOCORR = "autocorrelation_portmanteau_10"
PAIRS = "pair_independence_chi2"
ALL_TESTS = (UNIFORMITY, RUNS, AUTOCORR, PAIRS)
LAGS = 10


@dataclass(frozen=True)
class TestResult:
    __test__ = False  # not a pytest class

    name: str
    statistic: float | None
    df: int | None
    p_value: float | None
    n_used: int
    valid: bool = True
    note: str = ""


def _not_computable(name: str, n_used: int, note: str) -> TestResult:
    return TestResult(name, None, None, None, n_used, valid=False, note=note)


def _used(digits: np.ndarray, seg_ids: np.ndarray, min_len: int):
    """Digits of segments with at least min_len ticks, with segment labels renumbered 0..k-1."""
    digits = np.asarray(digits)
    if digits.size == 0:
        return digits, np.zeros(0, dtype=np.int64)
    lens = np.bincount(seg_ids)
    keep = (lens >= min_len)[seg_ids]
    d = digits[keep]
    _, inv = np.unique(seg_ids[keep], return_inverse=True)
    return d, inv.astype(np.int64)


def uniformity_chi2(digits: np.ndarray) -> TestResult:
    n = int(np.asarray(digits).size)
    if n == 0:
        return _not_computable(UNIFORMITY, 0, "no digits")
    counts = np.bincount(np.asarray(digits), minlength=10).astype(np.float64)
    expected = n / 10.0
    x2 = float(((counts - expected) ** 2 / expected).sum())
    return TestResult(UNIFORMITY, x2, 9, chi2_sf(x2, 9), n)


def parity_runs(digits: np.ndarray, seg_ids: np.ndarray, min_len: int = 10) -> TestResult:
    d, inv = _used(digits, seg_ids, min_len)
    if d.size == 0:
        return _not_computable(RUNS, 0, "no segment long enough")
    even = (d % 2 == 0).astype(np.int64)
    k = int(inv.max()) + 1
    n = np.bincount(inv, minlength=k).astype(np.float64)
    n1 = np.bincount(inv, weights=even, minlength=k)
    n2 = n - n1
    changes = (even[1:] != even[:-1]) & (inv[1:] == inv[:-1])
    runs = 1.0 + np.bincount(inv[1:][changes], minlength=k)
    mean = 1.0 + 2.0 * n1 * n2 / n
    var = 2.0 * n1 * n2 * (2.0 * n1 * n2 - n) / (n * n * (n - 1.0))
    total_var = float(var.sum())
    if total_var <= 0.0:
        return _not_computable(RUNS, int(d.size), "zero variance (all digits of one parity)")
    z = float((runs.sum() - mean.sum()) / np.sqrt(total_var))
    return TestResult(RUNS, z, None, min(1.0, 2.0 * norm_sf(abs(z))), int(d.size))


def autocorrelation_portmanteau(digits: np.ndarray, seg_ids: np.ndarray, min_len: int = 10,
                                lags: int = LAGS) -> TestResult:
    d, inv = _used(digits, seg_ids, min_len)
    if d.size <= lags:
        return _not_computable(AUTOCORR, int(d.size), "too few digits")
    x = d.astype(np.float64)
    m = x.mean()
    xc = x - m
    big_d = float((xc * xc).sum())
    if big_d <= 0.0:
        return _not_computable(AUTOCORR, int(d.size), "zero variance")
    n_total = x.size
    q = 0.0
    min_pairs = None
    for k in range(1, lags + 1):
        same = inv[k:] == inv[:-k]
        p_k = int(same.sum())
        if p_k == 0:
            return _not_computable(AUTOCORR, int(d.size), f"no within-segment pairs at lag {k}")
        c_k = float((xc[k:] * xc[:-k])[same].sum())
        r_k = (c_k / p_k) / (big_d / n_total)
        q += p_k * r_k * r_k
        min_pairs = p_k if min_pairs is None else min(min_pairs, p_k)
    return TestResult(AUTOCORR, q, lags, chi2_sf(q, lags), int(min_pairs))


def pair_independence_chi2(digits: np.ndarray, seg_ids: np.ndarray, min_len: int = 10) -> TestResult:
    d, inv = _used(digits, seg_ids, min_len)
    if d.size < 2:
        return _not_computable(PAIRS, int(d.size), "too few digits")
    k = int(inv.max()) + 1
    lens = np.bincount(inv, minlength=k)
    starts = np.concatenate(([0], np.cumsum(lens)[:-1]))
    pos = np.arange(d.size) - np.repeat(starts, lens)
    first = np.flatnonzero((pos % 2 == 0) & (pos + 1 < np.repeat(lens, lens)))
    a, b = d[first], d[first + 1]
    m = int(a.size)
    if m == 0:
        return _not_computable(PAIRS, 0, "no pairs")
    table = np.bincount(a.astype(np.int64) * 10 + b, minlength=100).reshape(10, 10).astype(np.float64)
    rows, cols = table.sum(axis=1), table.sum(axis=0)
    expected = np.outer(rows, cols) / m
    nz = expected > 0
    x2 = float(((table - expected) ** 2 / np.where(nz, expected, 1.0))[nz].sum())
    df = int((rows > 0).sum() - 1) * int((cols > 0).sum() - 1)
    if df < 1:
        return _not_computable(PAIRS, 2 * m, "degenerate table")
    return TestResult(PAIRS, x2, df, chi2_sf(x2, df), 2 * m)
