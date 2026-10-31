"""Gate 1 pipeline: segmentation, the four tests, minimum samples, replication halves, validity checks and the
decision rule of kill-criterion A1.7 as amended (A2, A3). All parameters come from the registration in the document.

Pure functions of arrays and a random generator. Loading real ticks is a separate, permit-gated step (loader.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from derivbot.gate1 import stats
from derivbot.gate1.segments import segment_ids
from derivbot.gate1.stats import ALL_TESTS, TestResult
from derivbot.stats.edge import holm_reject, wilson_lower

CONFIRMATORY, EXPLORATORY = "confirmatory", "exploratory"
STRUCTURE_FOUND = "STRUCTURE_FOUND"
INCONCLUSIVE = "INCONCLUSIVE"
NO_STRUCTURE = "NO_STRUCTURE_DETECTED"
CANNOT_CONCLUDE = "CANNOT_CONCLUDE"


@dataclass(frozen=True)
class Series:
    symbol: str
    epochs: np.ndarray   # strictly increasing int64
    digits: np.ndarray   # int8, 0..9, same length


@dataclass
class SymbolAnalysis:
    symbol: str
    tier: str
    n_ticks: int
    n_segments: int
    n_valid_segments: int
    sufficient: bool
    results: dict[str, TestResult]
    halves: tuple[dict[str, TestResult], dict[str, TestResult]]
    validity_rates: dict[str, float | None]
    validity_ok: dict[str, bool]
    parity: dict[str, Any] = field(default_factory=dict)


def max_gap_for(symbol: str, reg: dict, tier: str) -> int:
    return reg["contiguous_max_gap_s"][symbol] if tier == CONFIRMATORY else reg["exploratory_max_gap_s"]


def run_tests(series: Series, max_gap: int, reg: dict) -> dict[str, TestResult]:
    ids = segment_ids(series.epochs, max_gap)
    min_len = reg["min_segment_len"]
    raw = {
        stats.UNIFORMITY: stats.uniformity_chi2(series.digits),
        stats.RUNS: stats.parity_runs(series.digits, ids, min_len),
        stats.AUTOCORR: stats.autocorrelation_portmanteau(series.digits, ids, min_len),
        stats.PAIRS: stats.pair_independence_chi2(series.digits, ids, min_len),
    }
    out = {}
    for name, r in raw.items():
        need = reg["min_valid_digits"][name]
        if r.valid and r.n_used < need:
            r = TestResult(name, None, None, None, r.n_used, valid=False, note=f"below minimum {need}")
        out[name] = r
    return out


def rejects(r: TestResult, alpha: float) -> bool:
    return bool(r.valid and r.p_value is not None and r.p_value < alpha)


def shuffle_within_segments(digits: np.ndarray, ids: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Permute digits inside each segment. Segments are contiguous, so sorting by (segment, random key) does it."""
    order = np.lexsort((rng.random(digits.size), ids))
    return digits[order]


def split_halves(series: Series) -> tuple[Series, Series]:
    mid = series.epochs.size // 2
    return (Series(series.symbol, series.epochs[:mid], series.digits[:mid]),
            Series(series.symbol, series.epochs[mid:], series.digits[mid:]))


def validity_rates(series: Series, max_gap: int, reg: dict, rng: np.random.Generator) -> dict[str, float | None]:
    """A3.2: serial tests on shuffled copies of the real series; uniformity on simulated iid-uniform series of the same
    length. Rate = share of copies rejecting at unadjusted alpha. None if the test could not run at all."""
    copies, alpha = reg["validity_copies"], reg["alpha_family"]
    ids = segment_ids(series.epochs, max_gap)
    counts = {name: 0 for name in ALL_TESTS}
    ran = {name: 0 for name in ALL_TESTS}
    for _ in range(copies):
        shuffled = Series(series.symbol, series.epochs, shuffle_within_segments(series.digits, ids, rng))
        res = run_tests(shuffled, max_gap, reg)
        for name in reg["shuffle_checked_tests"]:
            if res[name].valid:
                ran[name] += 1
                counts[name] += rejects(res[name], alpha)
        sim = rng.integers(0, 10, series.digits.size, dtype=np.int8)
        u = stats.uniformity_chi2(sim)
        ran[stats.UNIFORMITY] += 1
        counts[stats.UNIFORMITY] += rejects(u, alpha)
    return {name: (counts[name] / ran[name] if ran[name] else None) for name in ALL_TESTS}


def analyze_symbol(series: Series, reg: dict, rng: np.random.Generator, tier: str) -> SymbolAnalysis:
    max_gap = max_gap_for(series.symbol, reg, tier)
    ids = segment_ids(series.epochs, max_gap)
    lens = np.bincount(ids) if ids.size else np.zeros(0, dtype=np.int64)
    need = reg["min_ticks_confirmatory"] if tier == CONFIRMATORY else reg["min_ticks_exploratory"]
    sufficient = series.epochs.size >= need
    results = run_tests(series, max_gap, reg)
    h1, h2 = split_halves(series)
    halves = (run_tests(h1, max_gap, reg), run_tests(h2, max_gap, reg))
    lo, hi = reg["validity_band"]
    if sufficient:
        rates = validity_rates(series, max_gap, reg, rng)
    else:
        rates = {name: None for name in ALL_TESTS}
    ok = {name: (rates[name] is not None and lo <= rates[name] <= hi) for name in ALL_TESTS}
    even = int((series.digits % 2 == 0).sum())
    n = int(series.digits.size)
    parity = {"n": n, "p_even": even / n if n else None,
              "wilson95_lower_one_sided": wilson_lower(even, n, 0.95) if n else None}
    return SymbolAnalysis(series.symbol, tier, int(series.epochs.size), int(lens.size),
                          int((lens >= reg["min_segment_len"]).sum()), sufficient, results, halves, rates, ok, parity)


@dataclass
class Gate1Decision:
    outcome: str
    reason: str
    table: list[dict[str, Any]]
    replicated: list[tuple[str, str]]


def _family(analyses: list[SymbolAnalysis], reg: dict, size: int):
    cells = [(a.symbol, t) for a in analyses for t in reg["tests"]]
    p = []
    for a in analyses:
        for t in reg["tests"]:
            r = a.results[t]
            p.append(r.p_value if (r.valid and r.p_value is not None and a.sufficient) else 1.0)
    # pad with p = 1.0 for symbols that were not analysed at all so the family size is the registered one
    while len(p) < size:
        p.append(1.0)
    return cells, p


def decide(confirmatory: list[SymbolAnalysis], reg: dict) -> Gate1Decision:
    alpha, rep_alpha = reg["alpha_family"], reg["replication_alpha"]
    cells, p = _family(confirmatory, reg, reg["confirmatory_family_size"])
    rej = holm_reject(p, alpha)
    table = [{"symbol": s, "test": t, "p": p[i], "holm_rejected": rej[i], "tier": CONFIRMATORY}
             for i, (s, t) in enumerate(cells)]
    expected_symbols = set(reg["confirmatory_symbols"])
    if {a.symbol for a in confirmatory} != expected_symbols:
        return Gate1Decision(CANNOT_CONCLUDE, "a confirmatory symbol is missing", table, [])
    short = [a.symbol for a in confirmatory if not a.sufficient]
    if short:
        return Gate1Decision(CANNOT_CONCLUDE, f"below the minimum number of eligible ticks: {short}", table, [])
    bad = [(a.symbol, t) for a in confirmatory for t in reg["tests"] if not a.validity_ok[t]]
    if bad:
        return Gate1Decision(CANNOT_CONCLUDE, f"run INVALID: validity checks outside the band for {bad}", table, [])
    hits = [(s, t) for (s, t), r in zip(cells, rej) if r]
    if not hits:
        return Gate1Decision(NO_STRUCTURE, "no confirmatory test rejects under Holm", table, [])
    by_sym = {a.symbol: a for a in confirmatory}
    replicated = []
    for s, t in hits:
        h1, h2 = by_sym[s].halves
        if rejects(h1[t], rep_alpha) and rejects(h2[t], rep_alpha):
            replicated.append((s, t))
    if replicated:
        return Gate1Decision(STRUCTURE_FOUND, "Holm rejection replicated in both halves", table, replicated)
    return Gate1Decision(INCONCLUSIVE, "Holm rejection did not replicate in both halves", table, [])
