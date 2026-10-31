import numpy as np
import pytest

from derivbot.gate1 import controls as c
from derivbot.gate1.registration import load_registration
from derivbot.gate1.segments import segment_ids
from derivbot.gate1.stats import ALL_TESTS

REG = load_registration()


def test_band_matches_the_registered_numbers():
    assert c.band(1000) == (0.0293, 0.0707)
    assert c.band(200) == tuple(REG["validity_band"])


def test_ks_statistic_known_cases():
    assert c.ks_statistic(np.array([0.5])) == pytest.approx(0.5)
    uniform_grid = (np.arange(1000) + 0.5) / 1000
    assert c.ks_statistic(uniform_grid) == pytest.approx(0.0005)
    assert c.ks_statistic(np.zeros(1000) + 0.01) > 0.9


def test_epochs_with_gaps_are_increasing_and_actually_have_gaps():
    ep = c.epochs_with_gaps(20_000, 1, np.random.default_rng(1))
    assert ep.size == 20_000 and np.all(np.diff(ep) > 0)
    assert segment_ids(ep, 1).max() >= 5


def test_planted_generators_have_the_intended_property():
    n = 200_000
    rng = np.random.default_rng(3)
    assert abs((c.planted("parity_bias", n, rng) % 2 == 0).mean() - 0.53) < 0.01
    d7 = c.planted("digit7_bias", n, rng)
    assert abs((d7 == 7).mean() - 0.11) < 0.005
    par = c.planted("parity_persistence", n, rng) % 2
    assert abs((par[1:] == par[:-1]).mean() - 0.53) < 0.01
    rep = c.planted("digit_repeat", n, rng)
    assert (rep[1:] == rep[:-1]).mean() > 0.18        # 0.12 forced repeats plus chance repeats of the uniform draws


def test_seed_is_the_registered_constant_and_runs_are_reproducible():
    assert c.SEED == 20261031
    a = c.iid_digits(1000, c.rng_for(1, 5))
    b = c.iid_digits(1000, c.rng_for(1, 5))
    assert np.array_equal(a, b) and not np.array_equal(a, c.iid_digits(1000, c.rng_for(1, 6)))


def test_a_small_negative_control_runs_end_to_end():
    out = c.negative_control(REG, reps=30, n=30_000, gaps=True)
    assert set(out["per_test"]) == set(ALL_TESTS) and out["reps"] == 30
