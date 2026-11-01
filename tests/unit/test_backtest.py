import json
from decimal import Decimal as D
from pathlib import Path

import numpy as np
import pytest

from derivbot.backtest import metrics as mt
from derivbot.backtest.engine import LookaheadDetected, causality_check, run_backtest
from derivbot.backtest.payout import PayoutModel, Quote
from derivbot.backtest.series import TickSeries, shuffled_returns, simulate_digits, simulate_random_walk
from derivbot.signals.base import LookaheadError, Signal, TickWindow

ROOT = Path(__file__).resolve().parents[2]
PAYOUT = PayoutModel.from_quotes([Quote("DIGITEVEN", D("1"), D("1.95")), Quote("DIGITODD", D("1"), D("1.95")),
                                  Quote("DIGITOVER", D("1"), D("2.43")), Quote("CALL", D("1"), D("1.95")),
                                  Quote("PUT", D("1"), D("1.95"))])


class Const:
    """Reference strategy: emit one fixed signal at every decision tick."""
    id, version, warmup_ticks = "const", "1", 0

    def __init__(self, contract="DIGITEVEN", duration=2, barrier=None):
        self.contract, self.duration_ticks, self.barrier = contract, duration, barrier

    @property
    def params(self):
        return {"contract": self.contract}

    def signal(self, w):
        return Signal(self.contract, self.barrier)


def fac(**kw):
    return lambda series: Const(**kw)


def series_of(digits, quotes=None):
    n = len(digits)
    q = np.array(quotes if quotes is not None else [1000.0 + i for i in range(n)], dtype=float)
    return TickSeries("T", np.arange(n, dtype=np.int64) + 100, q, np.array(digits, dtype=np.int8), 2)


# ------------------------------------------------------------------ series and digits

def test_digit_is_the_last_digit_at_pip_size_including_a_trailing_zero():
    s = TickSeries.from_quotes("T", np.array([1, 2, 3]), np.array([1310.70, 1310.75, 5.0]), 2)
    assert s.digits.tolist() == [0, 5, 0]


def test_series_rejects_unsorted_or_mismatched_input():
    with pytest.raises(ValueError):
        TickSeries("T", np.array([2, 1]), np.array([1.0, 1.0]), np.array([0, 0], dtype=np.int8), 2)
    with pytest.raises(ValueError):
        TickSeries("T", np.array([1, 2]), np.array([1.0]), np.array([0, 0], dtype=np.int8), 2)


def test_shuffled_returns_keeps_the_increments_but_not_their_order():
    s = simulate_random_walk(2000, seed=1)
    sh = shuffled_returns(s, seed=2)
    assert len(sh) == len(s)
    assert not np.array_equal(sh.quotes, s.quotes)
    assert np.allclose(np.sort(np.diff(sh.quotes)), np.sort(np.diff(s.quotes)), atol=0.011)   # rounding to cents


# ------------------------------------------------------------------ payout model from stored quotes

def test_payout_model_from_the_stored_evidence_uses_the_worst_ratio_by_default():
    pm = PayoutModel.from_evidence_batch(ROOT / "docs/evidence/public_probe_batch.json")
    assert pm.r("DIGITEVEN") == D("0.66") / D("0.35") - 1 or abs(pm.r("DIGITEVEN") - D("0.8857142857")) < D("1e-6")
    near = PayoutModel.from_evidence_batch(ROOT / "docs/evidence/public_probe_batch.json", mode="nearest")
    assert near.r("DIGITEVEN", D("1")) == D("0.95")
    assert pm.r("DIGITEVEN") < near.r("DIGITEVEN", D("1"))


def test_unknown_contract_has_no_default_payout():
    with pytest.raises(KeyError):
        PAYOUT.r("DIGITMATCH")


def test_shift_lowers_the_ratio_and_the_model_serialises():
    assert PAYOUT.shifted(D("0.05")).r("CALL") == D("0.90")
    assert json.dumps(PAYOUT.to_json())


def test_invalid_quotes_are_refused():
    for bad in ([], [Quote("CALL", D("0"), D("1"))]):
        with pytest.raises(ValueError):
            PayoutModel(bad)


# ------------------------------------------------------------------ exact accounting on hand-built series

def test_win_pays_r_times_stake_and_loss_costs_the_stake():
    # decision at t=0, entry t=1, exit t=3 (duration 2). digit at 3 is 4 (even) -> win
    res = run_backtest(series_of([1, 1, 1, 4, 1, 1, 1, 1]), fac(), PAYOUT, check_causality=False)
    assert res.trades[0].win and res.trades[0].profit == D("0.95") and res.trades[0].r == D("0.95")
    res = run_backtest(series_of([2, 2, 2, 7, 1, 1, 1, 1]), fac(), PAYOUT, check_causality=False)
    assert not res.trades[0].win and res.trades[0].profit == D("-1")


def test_timing_entry_strictly_after_decision_and_exit_after_entry_and_no_overlap():
    s = simulate_digits(400, seed=3)
    res = run_backtest(s, fac(duration=3), PAYOUT, check_causality=False)
    assert len(res.trades) > 50
    for t in res.trades:
        assert t.decision_epoch < t.entry_epoch < t.exit_epoch
    for a, b in zip(res.trades, res.trades[1:]):
        assert b.entry_epoch > a.exit_epoch                     # a new contract only after the previous one settled


def test_trade_count_follows_from_latency_and_duration():
    s = series_of([0] * 100)
    res = run_backtest(s, fac(duration=4), PAYOUT, latency_ticks=1, check_causality=False)
    assert len(res.trades) == 19                                # each trade consumes latency + duration = 5 ticks
    res2 = run_backtest(s, fac(duration=4), PAYOUT, latency_ticks=3, check_causality=False)
    assert len(res2.trades) == 14                               # 7 ticks each


def test_latency_below_one_is_refused():
    with pytest.raises(ValueError):
        run_backtest(series_of([0] * 20), fac(), PAYOUT, latency_ticks=0)


def test_the_last_ticks_that_cannot_settle_are_not_traded():
    res = run_backtest(series_of([0] * 6), fac(duration=2), PAYOUT, check_causality=False)
    assert all(t.exit_epoch <= 105 for t in res.trades)


def test_all_contract_types_settle_as_defined():
    d = [0, 0, 0, 6, 0, 0, 0, 0]
    cases = {("DIGITEVEN", None): True, ("DIGITODD", None): False, ("DIGITOVER", 5): True, ("DIGITUNDER", 5): False,
             ("DIGITMATCH", 6): True, ("DIGITDIFF", 6): False}
    pm = PayoutModel([Quote(c, D("1"), D("1.9")) for c, _ in cases])
    for (contract, barrier), expected in cases.items():
        res = run_backtest(series_of(d), fac(contract=contract, barrier=barrier), pm, check_causality=False)
        assert res.trades[0].win is expected, contract


def test_call_put_and_a_tie_loses():
    q = [100, 100, 100, 101, 100, 100, 100, 100.0]
    up = run_backtest(series_of([0] * 8, q), fac(contract="CALL"), PAYOUT, check_causality=False)
    assert up.trades[0].win                                     # exit 101 > entry 100
    flat = run_backtest(series_of([0] * 8, [100.0] * 8), fac(contract="CALL"), PAYOUT, check_causality=False)
    assert not flat.trades[0].win                               # tie loses (UNVERIFIED tie rule)
    put = run_backtest(series_of([0] * 8, [100, 100, 100, 99, 100, 100, 100, 100.0]), fac(contract="PUT"), PAYOUT, check_causality=False)
    assert put.trades[0].win


def test_signal_rejects_unsupported_contracts():
    with pytest.raises(ValueError):
        Signal("MULTUP")


# ------------------------------------------------------------------ look-ahead canary

class Cheater:
    """The canary: built with the full data, it reads the NEXT tick's digit and bets on it. A perfect (and impossible)
    strategy that any look-ahead guard must catch."""
    id, version, warmup_ticks, duration_ticks = "cheat", "1", 0, 1
    params = {}

    def __init__(self, series):
        self.digits = series.digits

    def signal(self, w):
        t = w.index - 1
        nxt = int(self.digits[t + 2])        # tick t+2 is the exit tick for latency 1 and duration 1
        return Signal("DIGITEVEN" if nxt % 2 == 0 else "DIGITODD")


def test_the_lookahead_canary_is_caught_before_any_result_is_produced():
    s = simulate_digits(600, seed=4)
    with pytest.raises(LookaheadDetected):
        run_backtest(s, Cheater, PAYOUT)


def test_without_the_guard_the_canary_looks_like_a_miracle_which_is_why_the_guard_exists():
    s = simulate_digits(600, seed=4)

    class Safe(Cheater):
        def signal(self, w):
            t = w.index - 1
            nxt = int(self.digits[min(t + 2, len(self.digits) - 1)])
            return Signal("DIGITEVEN" if nxt % 2 == 0 else "DIGITODD")

    res = run_backtest(s, Safe, PAYOUT, check_causality=False)
    m = mt.compute(res.trades)
    assert m.win_rate > 0.95                                    # impossible for a real strategy on iid digits


def test_the_window_has_no_way_to_ask_for_a_future_tick():
    s = simulate_digits(50, seed=5)
    w = TickWindow(s.quotes, s.digits, s.epochs, 10)
    assert w.index == 11 and len(w.tail_quotes(10**6)) == 11 and len(w.tail_digits(10**6)) == 11
    with pytest.raises(LookaheadError):
        w.peek(1)
    with pytest.raises(ValueError):
        w.tail_quotes(5)[0] = 1.0                               # read-only view


def test_a_causal_strategy_passes_the_prefix_invariance_check():
    causality_check(lambda s: Const(), simulate_digits(500, seed=6), probes=60)


# ------------------------------------------------------------------ metrics

def trades_from(results):
    out = []
    from derivbot.backtest.engine import Trade
    for i, w in enumerate(results):
        out.append(Trade(i, 100 + 3 * i, 101 + 3 * i, 102 + 3 * i + (86400 * i), "DIGITEVEN", None, D(1), D("0.95"), w,
                         D("0.95") if w else D(-1)))
    return out


def test_metrics_on_a_known_sequence():
    tr = trades_from([True, True, False, False, False, True])
    m = mt.compute(tr)
    assert m.n == 6 and m.wins == 3 and m.win_rate == 0.5
    assert m.total_profit == pytest.approx(3 * 0.95 - 3)
    # cumulative: 0.95, 1.90 (peak), 0.90, -0.10, -1.10 (trough), -0.15 -> drawdown 3.00, 4 trades below the peak
    assert m.longest_loss_streak == 3 and m.max_drawdown == pytest.approx(3.0) and m.max_drawdown_trades == 4
    assert m.profit_factor == pytest.approx(2.85 / 3) and m.risk_reward == pytest.approx(0.95)
    assert m.breakeven_p == pytest.approx(1 / 1.95)
    assert m.expectancy_per_unit == pytest.approx((2.85 - 3) / 6)


def test_drawdown_and_streak_helpers():
    assert mt.max_drawdown([D(1), D(1), D(-3), D(1), D(1), D(1)]) == (D(3), 3)
    assert mt.max_drawdown([D(1), D(1)]) == (D(0), 0)
    assert mt.longest_streak([True, False, False, True, False]) == 2


def test_blocks_and_payout_sensitivity():
    tr = trades_from([True] * 10 + [False] * 10)
    b = mt.by_block(tr, 2)
    assert b[0].expectancy_per_unit > 0 > b[1].expectancy_per_unit
    assert mt.expectancy_at_shifted_r(tr, 0.05) < mt.compute(tr).expectancy_per_unit
    assert mt.by_block(tr, 40) == [] and mt.compute([]).n == 0
    with pytest.raises(ValueError):
        mt.by_block(tr, 0)
