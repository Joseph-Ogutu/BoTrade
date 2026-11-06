import random

from derivbot.forward.report import decide, stats_for
from derivbot.gate1.registration import load_registration
from derivbot.stats.edge import holm_reject

FT = load_registration()["forward_test"]
N = FT["n_eval"]


def sample(rate, n=N, seed=1, first_half_rate=None):
    rng = random.Random(seed)
    half = n // 2
    return [rng.random() < (first_half_rate if (first_half_rate is not None and i < half) else rate) for i in range(n)]


def verdict(won, holm=None):
    s = stats_for(won, FT)
    return decide(s, s["p_value"] <= FT["alpha_family"] / FT["family_size_k"] if holm is None else holm, FT), s


def test_a_fair_coin_at_break_even_is_not_a_pass():
    ok, s = verdict(sample(FT["p_break_even"]))
    assert not ok and s["n"] == N


def test_a_clearly_better_than_break_even_variant_passes_every_condition():
    ok, s = verdict(sample(0.57))
    assert ok and s["wilson_lower"] > FT["p_break_even"] and s["expectancy"] > 0 and all(b > 0 for b in s["block_expectancy"])


def test_a_small_edge_that_the_conservative_bound_cannot_confirm_fails():
    ok, s = verdict(sample(0.535))
    assert not ok and s["wilson_lower"] <= FT["p_break_even"]


def test_an_edge_that_exists_in_only_one_half_of_the_time_fails_the_regime_condition():
    ok, s = verdict(sample(0.52, first_half_rate=0.66))                    # strong first half, losing second half
    assert not ok and min(s["block_expectancy"]) < 0


def test_expectancy_must_survive_the_lower_payout_sensitivity():
    won = sample(0.545)
    s = stats_for(won, FT)
    assert s["expectancy"] > 0 and s["expectancy_r_minus"] < s["expectancy"]


def test_fewer_than_n_eval_contracts_can_never_pass():
    s = stats_for(sample(0.9, n=N - 1), FT)
    assert not decide(s, True, FT)


def test_holm_is_applied_over_a_family_padded_to_fourteen_with_ones():
    p = 0.05 / 14 / 2
    padded = [1.0] * 14
    padded[3] = p
    assert holm_reject(padded, 0.05)[3] is True                           # clears 0.05/14
    padded[3] = 0.05 / 14 * 1.5
    assert holm_reject(padded, 0.05)[3] is False                          # just above the strictest threshold: rejected by nothing
