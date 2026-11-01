"""Edge verdict and the mandatory control experiment (HANDBOOK 9.5) on SIMULATED data only."""

from decimal import Decimal as D

import pytest

from derivbot.backtest.engine import run_backtest
from derivbot.backtest.payout import PayoutModel, Quote
from derivbot.backtest.series import shuffled_returns, simulate_random_walk
from derivbot.backtest.verdict import evaluate_family, evaluate_variant
from derivbot.signals.base import Signal

PAYOUT = PayoutModel([Quote("CALL", D("1"), D("1.95")), Quote("PUT", D("1"), D("1.95"))])


class Momentum:
    """Reference trend follower used only to exercise the harness: CALL if the last two moves were both up."""
    id, version, warmup_ticks, duration_ticks = "momentum", "1", 5, 1
    params = {"k": 2}

    def signal(self, w):
        q = w.tail_quotes(3)
        if q[2] > q[1] > q[0]:
            return Signal("CALL")
        if q[2] < q[1] < q[0]:
            return Signal("PUT")
        return None


def run(series, **kw):
    return run_backtest(series, lambda s: Momentum(), PAYOUT, check_causality=False, **kw)


def test_a_planted_edge_is_found_and_random_data_is_not():
    edge = run(simulate_random_walk(120_000, seed=11, rho=0.35))
    v = evaluate_variant(edge.trades, k_variants=1, max_drawdown_limit=D("10000"))
    assert v.conditions["significant"] and v.conditions["expectancy"] and v.conditions["n_min"] and v.passed
    rnd = run(simulate_random_walk(120_000, seed=12, rho=0.0))
    v2 = evaluate_variant(rnd.trades, k_variants=1, max_drawdown_limit=D("10000"))
    assert not v2.conditions["significant"] and not v2.passed


def test_without_a_drawdown_limit_the_verdict_fails_by_default():
    edge = run(simulate_random_walk(120_000, seed=11, rho=0.35))
    v = evaluate_variant(edge.trades, k_variants=1)
    assert v.conditions["drawdown"] is False and v.passed is False


def test_too_few_trades_fail_the_sample_size_condition_even_with_a_strong_edge():
    edge = run(simulate_random_walk(6_000, seed=11, rho=0.6))
    v = evaluate_variant(edge.trades, k_variants=1, max_drawdown_limit=D("10000"))
    assert v.n < v.n_min and v.conditions["n_min"] is False and v.passed is False


def test_more_variants_tried_means_a_larger_required_sample_and_a_stricter_test():
    edge = run(simulate_random_walk(120_000, seed=11, rho=0.35))
    one = evaluate_variant(edge.trades, k_variants=1)
    many = evaluate_variant(edge.trades, k_variants=50)
    assert many.n_min > one.n_min and many.wilson_lower < one.wilson_lower
    with pytest.raises(ValueError):
        evaluate_variant(edge.trades, k_variants=0)


def test_family_evaluation_applies_holm_over_every_variant():
    edge = run(simulate_random_walk(120_000, seed=11, rho=0.35)).trades
    rnd = [run(simulate_random_walk(60_000, seed=100 + i, rho=0.0)).trades for i in range(4)]
    fam = evaluate_family({"edge": edge, **{f"noise{i}": t for i, t in enumerate(rnd)}}, max_drawdown_limit=D("10000"))
    assert fam["edge"].passed and not any(fam[f"noise{i}"].passed for i in range(4))


def test_empty_trade_list_never_passes():
    v = evaluate_variant([], k_variants=1, max_drawdown_limit=D("1"))
    assert v.passed is False and v.n == 0


def test_the_block_condition_fails_when_the_edge_exists_in_only_one_half():
    a = run(simulate_random_walk(80_000, seed=21, rho=0.5)).trades
    b = run(simulate_random_walk(80_000, seed=22, rho=0.0)).trades           # the effect is absent in the second half
    off = a[-1].exit_epoch + 10
    from dataclasses import replace
    shifted = [replace(t, seq=len(a) + i, decision_epoch=t.decision_epoch + off, entry_epoch=t.entry_epoch + off,
                       exit_epoch=t.exit_epoch + off) for i, t in enumerate(b)]
    v = evaluate_variant(a + shifted, k_variants=1, max_drawdown_limit=D("100000"))
    assert v.conditions["blocks"] is False and v.passed is False


def test_control_experiment_random_series_do_not_produce_edge_at_more_than_the_expected_rate():
    """HANDBOOK 9.5: shuffled returns and random walks. Count series where the significance condition fires."""
    fires = 0
    n = 40
    for i in range(n):
        base = simulate_random_walk(20_000, seed=500 + i)
        for series in (base, shuffled_returns(base, seed=900 + i)):
            v = evaluate_variant(run(series).trades, k_variants=1)
            fires += v.conditions["significant"]
    assert fires <= 2     # true win rate is 0.5, below break-even 0.513: a false positive is very unlikely at alpha 0.05


def test_control_experiment_shuffling_destroys_a_planted_edge():
    s = simulate_random_walk(120_000, seed=31, rho=0.35)
    real = evaluate_variant(run(s).trades, k_variants=1)
    shuf = evaluate_variant(run(shuffled_returns(s, seed=32)).trades, k_variants=1)
    assert real.p_hat > real.p_be and shuf.p_hat < 0.51 and not shuf.conditions["significant"]
