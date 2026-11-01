import numpy as np

from derivbot.gate1 import pipeline as pl
from derivbot.gate1.registration import load_registration
from derivbot.gate1.segments import segment_ids
from derivbot.gate1.stats import ALL_TESTS, PAIRS, RUNS, UNIFORMITY, TestResult

REG = load_registration()


def series_iid(symbol, n, seed, interval=1):
    rng = np.random.default_rng(seed)
    return pl.Series(symbol, np.arange(n, dtype=np.int64) * interval + 1_000, rng.integers(0, 10, n).astype(np.int8))


def test_run_tests_applies_minimum_samples():
    s = series_iid("1HZ100V", 5_000, 1)
    res = pl.run_tests(s, 1, REG)
    assert all(not r.valid and "below minimum" in r.note for r in res.values())
    s = series_iid("1HZ100V", 60_000, 1)
    assert all(r.valid for r in pl.run_tests(s, 1, REG).values())


def test_shuffle_within_segments_preserves_each_segments_multiset():
    ep = np.concatenate([np.arange(0, 50), np.arange(100, 130), np.arange(300, 320)]).astype(np.int64)
    ids = segment_ids(ep, 1)
    d = np.random.default_rng(1).integers(0, 10, ep.size).astype(np.int8)
    out = pl.shuffle_within_segments(d, ids, np.random.default_rng(2))
    for seg in range(3):
        assert sorted(d[ids == seg]) == sorted(out[ids == seg])
    assert not np.array_equal(d, out)


def test_halves_split_by_tick_count_in_time_order():
    s = series_iid("1HZ100V", 1001, 1)
    a, b = pl.split_halves(s)
    assert a.epochs.size == 500 and b.epochs.size == 501 and a.epochs[-1] < b.epochs[0]


def test_planted_serial_structure_is_found_in_the_right_test():
    n = 120_000
    rng = np.random.default_rng(5)
    repeat = rng.random(n) < 0.15
    d = rng.integers(0, 10, n).astype(np.int8)
    for i in range(1, n):
        if repeat[i]:
            d[i] = d[i - 1]
    res = pl.run_tests(pl.Series("1HZ100V", np.arange(n, dtype=np.int64), d), 1, REG)
    assert pl.rejects(res[PAIRS], 0.05) and pl.rejects(res["autocorrelation_portmanteau_10"], 0.05)


def test_planted_bias_is_found_by_uniformity_only():
    n = 120_000
    rng = np.random.default_rng(6)
    d = np.where(rng.random(n) < 0.12, 7, rng.integers(0, 10, n)).astype(np.int8)
    res = pl.run_tests(pl.Series("R_100", np.arange(n, dtype=np.int64) * 2, d), 2, REG)
    assert pl.rejects(res[UNIFORMITY], 0.05)


# ---------------------------------------------------------------- the decision rule (A1.7 as amended)

def result(p, valid=True):
    return TestResult("t", 1.0, 1, p, 100_000, valid=valid)


def analysis(symbol, p_by_test, half_p=None, sufficient=True, validity_ok=True):
    res = {t: result(p_by_test.get(t, 0.5)) for t in ALL_TESTS}
    hp = half_p or {}
    h1 = {t: result(hp.get(t, (0.5, 0.5))[0]) for t in ALL_TESTS}
    h2 = {t: result(hp.get(t, (0.5, 0.5))[1]) for t in ALL_TESTS}
    ok = {t: validity_ok for t in ALL_TESTS}
    return pl.SymbolAnalysis(symbol, pl.CONFIRMATORY, 150_000, 3, 3, sufficient, res, (h1, h2),
                             {t: 0.05 for t in ALL_TESTS}, ok)


def two(a_p=None, b_p=None, **kw):
    return [analysis("1HZ100V", a_p or {}, **kw), analysis("R_100", b_p or {})]


def test_no_rejection_means_no_structure_detected():
    d = pl.decide(two(), REG)
    assert d.outcome == pl.NO_STRUCTURE


def test_holm_threshold_with_eight_tests():
    # smallest p must be <= 0.05 / 8 = 0.00625 to be rejected
    assert pl.decide(two({UNIFORMITY: 0.0062}, half_p={UNIFORMITY: (0.01, 0.02)}), REG).outcome == pl.STRUCTURE_FOUND
    assert pl.decide(two({UNIFORMITY: 0.0063}), REG).outcome == pl.NO_STRUCTURE


def test_rejection_without_replication_is_inconclusive():
    d = pl.decide(two({RUNS: 1e-6}, half_p={RUNS: (0.01, 0.30)}), REG)
    assert d.outcome == pl.INCONCLUSIVE and not d.replicated


def test_replication_needs_both_halves_below_0_05():
    ok = pl.decide(two({RUNS: 1e-6}, half_p={RUNS: (0.049, 0.049)}), REG)
    assert ok.outcome == pl.STRUCTURE_FOUND and ok.replicated == [("1HZ100V", RUNS)]
    no = pl.decide(two({RUNS: 1e-6}, half_p={RUNS: (0.049, 0.051)}), REG)
    assert no.outcome == pl.INCONCLUSIVE


def test_insufficient_data_is_cannot_conclude_even_if_the_other_symbol_rejects():
    a = analysis("1HZ100V", {UNIFORMITY: 1e-9}, half_p={UNIFORMITY: (0.001, 0.001)})
    b = analysis("R_100", {}, sufficient=False)
    d = pl.decide([a, b], REG)
    assert d.outcome == pl.CANNOT_CONCLUDE and "below the minimum" in d.reason


def test_invalid_run_is_cannot_conclude_not_a_finding():
    d = pl.decide(two({UNIFORMITY: 1e-9}, half_p={UNIFORMITY: (0.001, 0.001)}, validity_ok=False), REG)
    assert d.outcome == pl.CANNOT_CONCLUDE and "INVALID" in d.reason


def test_missing_confirmatory_symbol_is_cannot_conclude():
    assert pl.decide([analysis("1HZ100V", {})], REG).outcome == pl.CANNOT_CONCLUDE


def test_untestable_cells_count_as_not_rejected_and_keep_family_size():
    a = analysis("1HZ100V", {})
    a.results[RUNS] = result(None, valid=False)
    d = pl.decide([a, analysis("R_100", {})], REG)
    assert len(d.table) == 8 and d.outcome == pl.NO_STRUCTURE
    assert [r["p"] for r in d.table if r["test"] == RUNS][0] == 1.0
