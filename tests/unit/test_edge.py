import math

import pytest

from derivbot.stats.edge import (
    binomial_sf,
    breakeven_p,
    expectancy,
    holm_reject,
    required_n,
    wilson_lower,
)


def test_breakeven_matches_handbook_example():
    assert breakeven_p(0.85) == pytest.approx(1 / 1.85)
    assert breakeven_p(0.85) == pytest.approx(0.5405, abs=1e-4)


def test_breakeven_rejects_nonpositive_r():
    with pytest.raises(ValueError):
        breakeven_p(0.0)


def test_expectancy_is_zero_at_breakeven():
    r = 0.9
    assert expectancy(breakeven_p(r), r) == pytest.approx(0.0, abs=1e-12)


def test_wilson_lower_known_value():
    # n=100, x=50, 95% two-sided (z=1.96) lower bound is about 0.4038
    assert wilson_lower(50, 100, confidence=0.95, one_sided=False) == pytest.approx(0.4038, abs=5e-4)


def test_wilson_one_sided_is_tighter_than_two_sided():
    one = wilson_lower(55, 100, confidence=0.95, one_sided=True)
    two = wilson_lower(55, 100, confidence=0.95, one_sided=False)
    assert one > two


def test_wilson_edge_cases():
    assert wilson_lower(0, 0) == 0.0
    assert 0.0 <= wilson_lower(0, 50) < 0.05
    assert wilson_lower(50, 50) < 1.0
    with pytest.raises(ValueError):
        wilson_lower(11, 10)


def test_binomial_sf_exact_small_case():
    # P(X >= 8) for Binomial(10, 0.5) = 56 / 1024
    assert binomial_sf(8, 10, 0.5) == pytest.approx(56 / 1024, rel=1e-9)


def test_binomial_sf_bounds():
    assert binomial_sf(0, 10, 0.3) == 1.0
    assert binomial_sf(11, 10, 0.3) == 0.0
    assert binomial_sf(1500, 3000, 0.5) == pytest.approx(0.5, abs=0.02)


def test_required_n_near_handbook_illustration():
    n = required_n(breakeven_p(0.85), delta=0.02, alpha=0.05, power=0.80)
    # HANDBOOK 4.3 gives about 3,800 with a simplified formula
    assert 3500 <= n <= 4300


def test_required_n_grows_with_stricter_alpha_and_smaller_delta():
    p = breakeven_p(0.9)
    base = required_n(p, 0.02, 0.05, 0.8)
    assert required_n(p, 0.02, 0.05 / 20, 0.8) > base
    assert required_n(p, 0.01, 0.05, 0.8) > base


def test_holm_reject_textbook_case():
    # sorted p: 0.01 <= 0.05/3 reject; 0.03 > 0.05/2 stop
    assert holm_reject([0.01, 0.04, 0.03], alpha=0.05) == [True, False, False]


def test_holm_rejects_all_when_all_tiny_and_none_when_all_large():
    assert holm_reject([0.001, 0.002, 0.003], 0.05) == [True, True, True]
    assert holm_reject([0.4, 0.5], 0.05) == [False, False]
    assert holm_reject([], 0.05) == []


def test_holm_preserves_input_order():
    assert holm_reject([0.5, 0.001], 0.05) == [False, True]


def test_required_n_is_integer():
    assert isinstance(required_n(0.54, 0.02, 0.05, 0.8), int)
    assert not math.isnan(required_n(0.54, 0.02, 0.05, 0.8))
