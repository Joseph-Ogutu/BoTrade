"""Every branch of every pre-trade rule. Run with branch coverage; CI requires 100 percent on risk/rules.py and stake.py."""

from decimal import Decimal as D

from derivbot.risk import rules as r
from derivbot.risk.stake import StakeResult


def test_kill_switch():
    assert r.kill_switch_clear(False).passed and not r.kill_switch_clear(True).passed


def test_mode_and_account_every_branch():
    ok = r.mode_and_account_consistent("demo", "demo", "demo", True)
    assert ok.passed
    bad = r.mode_and_account_consistent("real", "demo", "demo", True)
    assert not bad.passed and "differ" in bad.detail
    assert not r.mode_and_account_consistent("demo", "real", "demo", True).passed
    assert not r.mode_and_account_consistent("live", "live", "demo", True).passed             # an unknown mode never passes
    assert not r.mode_and_account_consistent("demo", "demo", "real", True).passed
    assert not r.mode_and_account_consistent("demo", "demo", None, True).passed
    assert not r.mode_and_account_consistent("demo", "demo", "demo", False).passed
    unknown = r.mode_and_account_consistent("demo", "demo", "demo", None)
    assert not unknown.passed and "unknown" in unknown.observed


def test_connection_and_reconciliation_every_branch():
    assert r.connection_and_reconciliation_healthy(True, 10.0, 120).passed
    assert r.connection_and_reconciliation_healthy(True, 120.0, 120).passed          # boundary is allowed
    assert not r.connection_and_reconciliation_healthy(False, 10.0, 120).passed
    never = r.connection_and_reconciliation_healthy(True, None, 120)
    assert not never.passed and never.detail == "never reconciled"
    old = r.connection_and_reconciliation_healthy(True, 120.1, 120)
    assert not old.passed and old.detail == "reconciliation too old"


def test_market_data_fresh_every_branch():
    assert r.market_data_fresh(3.0, 10).passed and r.market_data_fresh(10.0, 10).passed
    assert not r.market_data_fresh(10.5, 10).passed
    none = r.market_data_fresh(None, 10)
    assert not none.passed and none.detail == "no market data"


def test_open_positions_and_counters_boundaries():
    assert r.open_positions_within_limit(0, 1).passed and not r.open_positions_within_limit(1, 1).passed
    assert r.consecutive_losses_within_limit(3, 4).passed and not r.consecutive_losses_within_limit(4, 4).passed
    assert r.trade_rate_within_limit(29, 30).passed and not r.trade_rate_within_limit(30, 30).passed


def test_daily_loss_every_branch():
    assert r.daily_loss_within_limit(D("-19.99"), None, D("20"), None).passed
    a = r.daily_loss_within_limit(D("-20"), None, D("20"), None)
    assert not a.passed and "absolute" in a.detail
    assert r.daily_loss_within_limit(D("50"), None, D("20"), None).passed
    assert r.daily_loss_within_limit(D("0"), None, D("20"), None).passed
    assert r.daily_loss_within_limit(D("-4.99"), D("100"), None, D("0.05")).passed
    p = r.daily_loss_within_limit(D("-5"), D("100"), None, D("0.05"))
    assert not p.passed and "percentage" in p.detail
    for start in (None, D("0"), D("-1")):
        u = r.daily_loss_within_limit(D("-1"), start, None, D("0.05"))
        assert not u.passed and "unknown" in u.detail
    assert not r.daily_loss_within_limit(D("-25"), D("1000"), D("20"), D("0.05")).passed
    assert not r.daily_loss_within_limit(D("-6"), D("100"), D("20"), D("0.05")).passed
    assert r.daily_loss_within_limit(D("-1"), D("100"), D("20"), D("0.05")).passed
    assert r.daily_loss_within_limit(D("-1"), None, None, None).limit == "none configured"


def test_stake_rule_both_branches():
    ok = r.stake_within_bounds(StakeResult(D("1.00"), "ok", D("1.00"), D("0.35")))
    assert ok.passed and ok.detail == ""
    bad = r.stake_within_bounds(StakeResult(None, "below_min", D("0.20"), D("0.35")))
    assert not bad.passed and bad.detail == "below_min"
    unknown = r.stake_within_bounds(StakeResult(None, "balance_unknown"))
    assert not unknown.passed and "unknown" in unknown.limit


def test_duplicate_protection_every_branch():
    assert r.duplicate_protection(False, False).passed
    assert "already used" in r.duplicate_protection(True, False).detail
    assert "in flight" in r.duplicate_protection(False, True).detail
    assert not r.duplicate_protection(True, True).passed


def test_proposal_rules_every_branch():
    assert r.proposal_price_acceptable(D("1"), D("1.02"), D("0.02")).passed
    assert not r.proposal_price_acceptable(D("1"), D("1.0201"), D("0.02")).passed
    assert r.proposal_payout_acceptable(D("1"), D("1.95"), D("0.80")).passed
    assert not r.proposal_payout_acceptable(D("1"), D("1.09"), D("0.80")).passed
    zero = r.proposal_payout_acceptable(D("0"), D("1"), D("0.80"))
    assert not zero.passed and "non-positive" in zero.detail


def test_strategy_rule():
    assert r.strategy_approved(True, "ema", "1").passed
    n = r.strategy_approved(False, "ema", "2")
    assert not n.passed and n.observed == "ema@2"


def test_rule_result_serialises():
    d = r.kill_switch_clear(True).as_dict()
    assert d == {"name": "kill_switch_clear", "passed": False, "observed": "engaged", "limit": "clear", "detail": ""}


def test_daily_trade_cap_every_branch():
    assert r.trades_per_day_within_limit(4, 5).passed and not r.trades_per_day_within_limit(5, 5).passed
    assert not r.trades_per_day_within_limit(9, 5).passed
    none = r.trades_per_day_within_limit(100, None)
    assert none.passed and none.limit == "no daily cap set"


def test_daily_win_streak_stop_every_branch():
    assert r.consecutive_wins_today_within_limit(2, 3).passed and not r.consecutive_wins_today_within_limit(3, 3).passed
    assert not r.consecutive_wins_today_within_limit(9, 3).passed
    none = r.consecutive_wins_today_within_limit(100, None)
    assert none.passed and none.limit == "no win stop set"


def test_daily_loss_count_stop_every_branch():
    assert r.losses_per_day_within_limit(2, 3).passed and not r.losses_per_day_within_limit(3, 3).passed
    assert not r.losses_per_day_within_limit(7, 3).passed
    none = r.losses_per_day_within_limit(100, None)
    assert none.passed and none.limit == "no daily stop set"


def test_real_mode_needs_the_guard_the_real_endpoint_and_an_account_confirmed_real():
    ok = r.mode_and_account_consistent("real", "real", "real", False, real_unlocked=True)
    assert ok.passed and ok.limit == "real, unlocked"
    locked = r.mode_and_account_consistent("real", "real", "real", False)                          # the guard has not resolved to real
    assert not locked.passed and "guard" in locked.detail
    assert "endpoint" in r.mode_and_account_consistent("real", "real", "demo", False, real_unlocked=True).detail
    assert not r.mode_and_account_consistent("real", "real", None, False, real_unlocked=True).passed
    assert "not confirmed to be the real" in r.mode_and_account_consistent("real", "real", "real", None, real_unlocked=True).detail
    assert not r.mode_and_account_consistent("real", "real", "real", True, real_unlocked=True).passed   # a demo account on a real process
    assert r.mode_and_account_consistent("demo", "demo", "demo", True, real_unlocked=True).passed is not False        # unlocking changes nothing for demo
    assert not r.mode_and_account_consistent("real", "demo", "real", False, real_unlocked=True).passed  # intent mode differs
