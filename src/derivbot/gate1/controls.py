"""Harness controls on SIMULATED data only (kill-criterion A1.8 as amended by A3).

The seed is fixed here and is not to be changed after seeing results: a control that fails is a harness fault to be
fixed, not a seed to be re-rolled. Nothing in this module reads a database or any real tick.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from derivbot.gate1 import pipeline as pl
from derivbot.gate1 import stats
from derivbot.stats.edge import holm_reject

SEED = 20261031
ALPHA = 0.05


def rng_for(section: int, rep: int = 0) -> np.random.Generator:
    return np.random.default_rng([SEED, section, rep])


# ------------------------------------------------------------------ simulators

def iid_digits(n: int, rng: np.random.Generator) -> np.ndarray:
    return rng.integers(0, 10, n).astype(np.int8)


def epochs_with_gaps(n: int, interval: int, rng: np.random.Generator, drop_prob: float = 0.002,
                     outages: int = 5, outage_len: int = 300) -> np.ndarray:
    """n epochs on a grid of `interval` seconds with random single-tick losses and a few longer outages."""
    grid = int(n / (1.0 - drop_prob)) + outages * outage_len + 1000
    keep = rng.random(grid) >= drop_prob
    for start in rng.integers(0, grid - outage_len, outages):
        keep[start:start + outage_len] = False
    ep = (np.flatnonzero(keep) * interval + 1_000).astype(np.int64)
    if ep.size < n:
        raise RuntimeError("simulator produced too few ticks")
    return ep[:n]


def epochs_plain(n: int, interval: int) -> np.ndarray:
    return (np.arange(n, dtype=np.int64) * interval + 1_000)


def planted(kind: str, n: int, rng: np.random.Generator) -> np.ndarray:
    if kind == "parity_bias":            # P(even) = 0.53, digits uniform within parity
        even = rng.random(n) < 0.53
        return (2 * rng.integers(0, 5, n) + np.where(even, 0, 1)).astype(np.int8)
    if kind == "digit7_bias":            # digit 7 with probability 0.11, the other nine equal
        p = np.full(10, 0.89 / 9)
        p[7] = 0.11
        return rng.choice(10, size=n, p=p).astype(np.int8)
    if kind == "parity_persistence":     # next parity equals the previous with probability 0.53
        same = rng.random(n) < 0.53
        flips = np.where(same, 0, 1)
        parity = (np.cumsum(flips) + int(rng.integers(0, 2))) % 2   # 0 = even
        return (2 * rng.integers(0, 5, n) + parity).astype(np.int8)
    if kind == "digit_repeat":           # with probability 0.12 repeat the previous digit, otherwise draw uniformly
        d = iid_digits(n, rng)
        rep = rng.random(n) < 0.12
        for i in np.flatnonzero(rep):
            if i > 0:
                d[i] = d[i - 1]          # sequential so that a run of repeats carries forward
        return d
    raise ValueError(kind)


PLANTED_EXPECTS = {
    "parity_bias": [stats.UNIFORMITY],
    "digit7_bias": [stats.UNIFORMITY],
    "parity_persistence": [stats.RUNS],
    "digit_repeat": [stats.AUTOCORR, stats.PAIRS],
}


# ------------------------------------------------------------------ helpers

def band(n: int) -> tuple[float, float]:
    se = math.sqrt(ALPHA * (1 - ALPHA) / n)
    return round(ALPHA - 3 * se, 4), round(ALPHA + 3 * se, 4)


def ks_statistic(p: np.ndarray) -> float:
    x = np.sort(p)
    n = x.size
    i = np.arange(1, n + 1)
    return float(max((i / n - x).max(), (x - (i - 1) / n).max()))


def ks_critical(n: int, level: float = 0.01) -> float:
    c = {0.05: 1.358, 0.01: 1.628}[level]
    return c / math.sqrt(n)


@dataclass
class ControlReport:
    negative: dict[str, Any] = field(default_factory=dict)
    positive: dict[str, Any] = field(default_factory=dict)
    validity: dict[str, Any] = field(default_factory=dict)
    seed: int = SEED

    @property
    def passed(self) -> bool:
        return (all(v["pass"] for v in self.negative.values()) and all(v["pass"] for v in self.positive.values())
                and all(v["pass"] for v in self.validity.values()))


# ------------------------------------------------------------------ negative control

def negative_control(reg: dict, reps: int = 1000, n: int = 50_000, gaps: bool = False, progress=None) -> dict[str, Any]:
    section = 2 if gaps else 1
    lo, hi = band(reps)
    p_a = {t: np.empty(reps) for t in reg["tests"]}
    p_b = {t: np.empty(reps) for t in reg["tests"]}
    any_holm = 0
    for rep in range(reps):
        rng = rng_for(section, rep)
        fam = []
        for sym_interval, store in ((1, p_a), (2, p_b)):
            ep = epochs_with_gaps(n, sym_interval, rng) if gaps else epochs_plain(n, sym_interval)
            series = pl.Series("sim", ep, iid_digits(n, rng))
            res = pl.run_tests(series, sym_interval, reg)
            for t in reg["tests"]:
                if not res[t].valid:
                    raise RuntimeError(f"control series too short for {t}: {res[t].note}")
                store[t][rep] = res[t].p_value
                fam.append(res[t].p_value)
        any_holm += any(holm_reject(fam, reg["alpha_family"]))
        if progress and (rep + 1) % 200 == 0:
            progress(f"negative gaps={gaps} {rep + 1}/{reps}")
    out: dict[str, Any] = {"reps": reps, "length": n, "gaps": gaps, "band": [lo, hi], "per_test": {}}
    ok = True
    crit = ks_critical(reps)
    for t in reg["tests"]:
        fpr_a, fpr_b = float((p_a[t] < ALPHA).mean()), float((p_b[t] < ALPHA).mean())
        ks = ks_statistic(p_a[t])
        t_ok = lo <= fpr_a <= hi and ks <= crit
        ok &= t_ok
        out["per_test"][t] = {"fpr_symbol_a": fpr_a, "fpr_symbol_b_reported_only": fpr_b, "ks_d": round(ks, 5),
                              "ks_critical_0.01": round(crit, 5), "pass": t_ok}
    share = any_holm / reps
    out["family_any_holm_rejection"] = share
    out["family_limit"] = hi
    out["pass"] = bool(ok and share <= hi)
    return out


# ------------------------------------------------------------------ positive control

def positive_control(reg: dict, reps: int = 200, n: int = 100_000, progress=None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, kind in enumerate(PLANTED_EXPECTS):
        hits = {t: 0 for t in PLANTED_EXPECTS[kind]}
        for rep in range(reps):
            rng = rng_for(10 + k, rep)
            series = pl.Series("sim", epochs_plain(n, 1), planted(kind, n, rng))
            res = pl.run_tests(series, 1, reg)
            for t in hits:
                hits[t] += pl.rejects(res[t], ALPHA)
        rates = {t: hits[t] / reps for t in hits}
        out[kind] = {"reps": reps, "length": n, "detection_rate": rates,
                     "required": 0.95, "pass": all(r >= 0.95 for r in rates.values())}
        if progress:
            progress(f"positive {kind}: {rates}")
    return out


# ------------------------------------------------------------------ run-validity machinery (A3.2)

def validity_machinery(reg: dict, n: int = 100_000, gaps: bool = False) -> dict[str, Any]:
    rng = rng_for(30 + int(gaps))
    ep = epochs_with_gaps(n, 1, rng) if gaps else epochs_plain(n, 1)
    series = pl.Series("sim", ep, iid_digits(n, rng))
    rates = pl.validity_rates(series, 1, reg, rng)
    lo, hi = reg["validity_band"]
    per = {t: {"rate": rates[t], "pass": rates[t] is not None and lo <= rates[t] <= hi} for t in reg["tests"]}
    return {"length": n, "gaps": gaps, "band": [lo, hi], "per_test": per, "pass": all(v["pass"] for v in per.values())}


def run_all(reg: dict, progress=None) -> ControlReport:
    rep = ControlReport()
    rep.negative["no_gaps"] = negative_control(reg, gaps=False, progress=progress)
    rep.negative["random_gaps"] = negative_control(reg, gaps=True, progress=progress)
    rep.positive = positive_control(reg, progress=progress)
    rep.validity["no_gaps"] = validity_machinery(reg, gaps=False)
    rep.validity["random_gaps"] = validity_machinery(reg, gaps=True)
    return rep
