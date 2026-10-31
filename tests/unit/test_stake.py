from decimal import Decimal as D

from hypothesis import given, settings
from hypothesis import strategies as st

from derivbot.risk.stake import compute_stake

PCT, MAXS, MIN = D("0.01"), D("5"), D("0.35")

money = st.decimals(min_value=D("0.01"), max_value=D("100000"), places=2)
pcts = st.sampled_from([D("0.001"), D("0.005"), D("0.01"), D("0.02"), D("0.05"), D("0.1")])


def test_basic_fraction_and_rounding_down():
    r = compute_stake(D("1000"), PCT, MAXS, MIN)
    assert r.stake == D("5.00") and r.reason == "ok"
    r = compute_stake(D("123.99"), PCT, MAXS, MIN)
    assert r.stake == D("1.23")


def test_unknown_balance_or_minimum_means_no_trade():
    for bal in (None, D("0"), D("-5")):
        assert compute_stake(bal, PCT, MAXS, MIN).reason == "balance_unknown"
    assert compute_stake(D("1000"), PCT, MAXS, None).reason == "min_stake_unknown"


def test_a_stake_below_the_minimum_is_not_raised_to_it():
    r = compute_stake(D("20"), PCT, MAXS, MIN)
    assert r.stake is None and r.reason == "below_min" and r.raw == D("0.20")
    assert compute_stake(D("35"), PCT, MAXS, MIN).stake == D("0.35")


def test_owner_floor_above_the_api_minimum_applies():
    assert compute_stake(D("100"), PCT, MAXS, MIN, D("2")).reason == "below_min"
    assert compute_stake(D("300"), PCT, MAXS, MIN, D("2")).stake == D("3.00")
    assert compute_stake(D("20"), PCT, MAXS, MIN, D("0.10")).reason == "below_min"


@given(balance=money, pct=pcts)
@settings(max_examples=300, deadline=None)
def test_stake_never_exceeds_any_cap(balance, pct):
    r = compute_stake(balance, pct, MAXS, MIN)
    if r.stake is not None:
        assert r.stake <= MAXS and r.stake <= balance * pct and r.stake <= balance
        assert r.stake >= MIN and r.stake == r.stake.quantize(D("0.01"))


@given(b1=money, b2=money, pct=pcts)
@settings(max_examples=300, deadline=None)
def test_stake_is_monotone_non_decreasing_in_balance(b1, b2, pct):
    lo, hi = sorted([b1, b2])
    s_lo, s_hi = compute_stake(lo, pct, MAXS, MIN).stake, compute_stake(hi, pct, MAXS, MIN).stake
    if s_lo is not None:
        assert s_hi is not None and s_hi >= s_lo


@given(start=st.decimals(min_value=D("10"), max_value=D("50000"), places=2),
       outcomes=st.lists(st.tuples(st.booleans(), st.decimals(min_value=D("0.1"), max_value=D("1.5"), places=2)), max_size=80),
       pct=pcts)
@settings(max_examples=250, deadline=None)
def test_stake_never_increases_after_a_loss_for_any_sequence(start, outcomes, pct):
    """Simulate trading: stake from the current balance; a loss subtracts the stake, a win adds stake * r."""
    balance, prev_stake, prev_was_loss = start, None, False
    for won, r in outcomes:
        res = compute_stake(balance, pct, MAXS, MIN)
        if res.stake is None:
            prev_stake = None
            continue
        if prev_was_loss and prev_stake is not None:
            assert res.stake <= prev_stake, (balance, res.stake, prev_stake)
        prev_stake, prev_was_loss = res.stake, not won
        balance = balance + res.stake * r if won else balance - res.stake


def test_there_is_no_parameter_that_scales_a_stake_after_a_loss():
    import inspect

    from derivbot.config.risk import RiskConfig

    names = set(RiskConfig.model_fields) | set(inspect.signature(compute_stake).parameters)
    for banned in ("martingale", "multiplier", "recover", "double", "streak", "scale"):
        assert not any(banned in n for n in names)
