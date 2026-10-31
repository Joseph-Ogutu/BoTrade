"""Gate 1 test functions: known answers, scipy cross-checks, and loop-based oracles written from the spec text."""

import math

import numpy as np
import pytest

from derivbot.gate1.numerics import chi2_sf, norm_sf
from derivbot.gate1.segments import segment_ids
from derivbot.gate1.stats import (
    autocorrelation_portmanteau,
    pair_independence_chi2,
    parity_runs,
    uniformity_chi2,
)

ONE_SEGMENT = lambda n: np.zeros(n, dtype=np.int64)  # noqa: E731


# ------------------------------------------------------------------ known answers and scipy references

def test_uniformity_known_counts():
    d = np.array([0] * 150 + [1] * 50 + [d for d in range(2, 10) for _ in range(100)])
    r = uniformity_chi2(d)
    assert r.statistic == pytest.approx(50.0) and r.df == 9
    assert r.p_value == pytest.approx(1.0772382022574693e-07, rel=1e-8)   # scipy.stats.chisquare


def test_uniformity_perfectly_flat_gives_p_one():
    r = uniformity_chi2(np.repeat(np.arange(10), 100))
    assert r.statistic == 0.0 and r.p_value == pytest.approx(1.0)


def test_uniformity_matches_scipy_on_seeded_digits():
    d = np.random.default_rng(1).integers(0, 10, 2000)
    r = uniformity_chi2(d)
    assert r.statistic == pytest.approx(10.99) and r.p_value == pytest.approx(0.27639500953447543, rel=1e-8)


def test_pair_independence_matches_scipy_contingency_on_seeded_digits():
    d = np.random.default_rng(1).integers(0, 10, 2000)
    r = pair_independence_chi2(d, ONE_SEGMENT(2000), min_len=10)
    assert r.df == 81 and r.n_used == 2000
    assert r.statistic == pytest.approx(85.87718026903543, rel=1e-9)         # scipy.stats.chi2_contingency
    assert r.p_value == pytest.approx(0.3343591742718454, rel=1e-8)


def test_runs_alternating_parity_hand_computed():
    d = np.array([0, 1] * 5)   # even, odd, ... n=10, n1=n2=5, runs=10, E=6, Var=2.2222
    r = parity_runs(d, ONE_SEGMENT(10), min_len=10)
    assert r.statistic == pytest.approx(2.6832815729997477)
    assert r.p_value == pytest.approx(0.007290358091535638, rel=1e-8)


def test_runs_mean_case_gives_z_zero():
    # 11 00 1 0 11 00 : 6 runs with n1=n2=5 gives exactly the expectation
    d = np.array([0, 0, 1, 1, 0, 1, 0, 0, 1, 1])
    r = parity_runs(d, ONE_SEGMENT(10), min_len=10)
    assert r.statistic == pytest.approx(0.0) and r.p_value == pytest.approx(1.0)


def test_runs_all_one_parity_is_not_computable_not_a_crash():
    r = parity_runs(np.array([0, 2, 4, 6] * 5), ONE_SEGMENT(20), min_len=10)
    assert r.valid is False and r.p_value is None


def test_autocorrelation_constant_digits_is_not_computable():
    r = autocorrelation_portmanteau(np.full(100, 3), ONE_SEGMENT(100), min_len=10)
    assert r.valid is False


def test_short_segments_are_ignored_by_the_serial_tests_but_not_by_uniformity():
    # 3 segments: lengths 40, 5, 40. The 5-tick segment is below min_len=10.
    ep = np.concatenate([np.arange(0, 40), np.arange(100, 105), np.arange(200, 240)])
    ids = segment_ids(ep, 1)
    d = np.random.default_rng(3).integers(0, 10, ep.size)
    assert uniformity_chi2(d).n_used == 85
    assert parity_runs(d, ids, 10).n_used == 80
    assert pair_independence_chi2(d, ids, 10).n_used == 80      # 40 pairs per long segment
    assert autocorrelation_portmanteau(d, ids, 10).n_used == 40 - 10 + 40 - 10   # smallest P_k is lag 10: 30 + 30


# ------------------------------------------------------------------ loop-based oracles from the spec text

def oracle_runs(digits, ids, min_len):
    segs = {}
    for d, s in zip(digits, ids):
        segs.setdefault(s, []).append(d % 2 == 0)
    runs = mean = var = 0.0
    used = 0
    for seq in segs.values():
        if len(seq) < min_len:
            continue
        n = len(seq)
        n1 = sum(seq)
        n2 = n - n1
        r = 1 + sum(1 for i in range(1, n) if seq[i] != seq[i - 1])
        runs += r
        mean += 1 + 2 * n1 * n2 / n
        var += 2 * n1 * n2 * (2 * n1 * n2 - n) / (n * n * (n - 1))
        used += n
    z = (runs - mean) / math.sqrt(var)
    return z, min(1.0, 2 * norm_sf(abs(z))), used


def oracle_portmanteau(digits, ids, min_len):
    segs = {}
    for d, s in zip(digits, ids):
        segs.setdefault(s, []).append(float(d))
    used = [v for v in segs.values() if len(v) >= min_len]
    allx = [x for v in used for x in v]
    n = len(allx)
    m = sum(allx) / n
    big_d = sum((x - m) ** 2 for x in allx)
    q = 0.0
    for k in range(1, 11):
        c = p = 0
        for v in used:
            for t in range(len(v) - k):
                c += (v[t] - m) * (v[t + k] - m)
                p += 1
        r = (c / p) / (big_d / n)
        q += p * r * r
    return q, chi2_sf(q, 10)


def oracle_pairs(digits, ids, min_len):
    segs = {}
    for d, s in zip(digits, ids):
        segs.setdefault(s, []).append(int(d))
    table = [[0] * 10 for _ in range(10)]
    pairs = 0
    for v in segs.values():
        if len(v) < min_len:
            continue
        for j in range(len(v) // 2):
            table[v[2 * j]][v[2 * j + 1]] += 1
            pairs += 1
    rows = [sum(r) for r in table]
    cols = [sum(table[i][j] for i in range(10)) for j in range(10)]
    x2 = 0.0
    for i in range(10):
        for j in range(10):
            e = rows[i] * cols[j] / pairs
            if e > 0:
                x2 += (table[i][j] - e) ** 2 / e
    df = (sum(1 for r in rows if r) - 1) * (sum(1 for c in cols if c) - 1)
    return x2, chi2_sf(x2, df), df, 2 * pairs


@pytest.mark.parametrize("seed", range(6))
def test_vectorised_tests_equal_the_loop_oracles_on_gappy_random_data(seed):
    rng = np.random.default_rng(100 + seed)
    n = 3000
    epochs = np.cumsum(1 + (rng.random(n) < 0.01) * rng.integers(1, 5, n))   # random outages
    ids = segment_ids(epochs, 1)
    digits = rng.integers(0, 10, n)
    # add some structure so the statistics are not all near their null means
    digits[1::7] = digits[0::7][: len(digits[1::7])]

    z, p, used = oracle_runs(digits, ids, 10)
    r = parity_runs(digits, ids, 10)
    assert r.statistic == pytest.approx(z, rel=1e-9, abs=1e-12) and r.p_value == pytest.approx(p, rel=1e-9)
    assert r.n_used == used

    q, pq = oracle_portmanteau(digits, ids, 10)
    r = autocorrelation_portmanteau(digits, ids, 10)
    assert r.statistic == pytest.approx(q, rel=1e-9) and r.p_value == pytest.approx(pq, rel=1e-8)

    x2, px, df, nd = oracle_pairs(digits, ids, 10)
    r = pair_independence_chi2(digits, ids, 10)
    assert r.statistic == pytest.approx(x2, rel=1e-9) and r.p_value == pytest.approx(px, rel=1e-8)
    assert r.df == df and r.n_used == nd
