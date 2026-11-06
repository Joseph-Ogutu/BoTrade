from decimal import Decimal as D

import pytest

from derivbot.config.risk import StrategyApprovals, parse_risk_config
from derivbot.risk.engine import IntentRequest, RiskContext, RiskEngine
from tests.unit.test_config import VALID

CFG = parse_risk_config(VALID)
APPROVALS = StrategyApprovals(approved={"ema_trend": "1"})
ENGINE = RiskEngine(CFG, APPROVALS)
INTENT = IntentRequest("k1", "1HZ100V", "DIGITEVEN", "ema_trend", "1", "demo", "ACC")


def ctx(**kw):
    base = dict(intent=INTENT, configured_mode="demo", endpoint_mode="demo", account_is_demo=True,
                kill_switch_engaged=False, connection_healthy=True, last_reconcile_age_s=5.0, last_tick_age_s=1.0,
                balance=D("1000"), api_min_stake=D("0.35"), open_positions=0, realized_pnl_today=D("0"),
                start_of_day_balance=D("1000"), consecutive_losses=0, trades_last_hour=0,
                idempotency_key_used=False, identical_recent_or_in_flight=False)
    base.update(kw)
    return RiskContext(**base)


def test_a_clean_context_is_approved_with_a_stake():
    d = ENGINE.evaluate(ctx())
    assert d.approved and d.stake == D("5.00") and d.failed == []
    assert len(d.results) == 13


def test_every_rule_is_recorded_even_when_several_fail():
    d = ENGINE.evaluate(ctx(kill_switch_engaged=True, last_tick_age_s=None, open_positions=1, consecutive_losses=9))
    assert not d.approved and d.stake is None
    assert set(d.failed) == {"kill_switch_clear", "market_data_fresh", "open_positions_within_limit",
                             "consecutive_losses_within_limit"}
    assert len(d.rule_results_json()) == 13


@pytest.mark.parametrize("override,failing", [
    ({"kill_switch_engaged": True}, "kill_switch_clear"),
    ({"endpoint_mode": "real"}, "mode_and_account_consistent"),
    ({"account_is_demo": None}, "mode_and_account_consistent"),
    ({"connection_healthy": False}, "connection_and_reconciliation_healthy"),
    ({"last_reconcile_age_s": None}, "connection_and_reconciliation_healthy"),
    ({"last_tick_age_s": 99.0}, "market_data_fresh"),
    ({"open_positions": 1}, "open_positions_within_limit"),
    ({"realized_pnl_today": D("-20")}, "daily_loss_within_limit"),
    ({"consecutive_losses": 4}, "consecutive_losses_within_limit"),
    ({"trades_last_hour": 30}, "trade_rate_within_limit"),
    ({"balance": None}, "stake_within_bounds"),
    ({"api_min_stake": None}, "stake_within_bounds"),
    ({"balance": D("10")}, "stake_within_bounds"),
    ({"idempotency_key_used": True}, "duplicate_protection"),
    ({"identical_recent_or_in_flight": True}, "duplicate_protection"),
])
def test_each_single_failure_rejects_and_names_the_rule(override, failing):
    d = ENGINE.evaluate(ctx(**override))
    assert not d.approved and failing in d.failed


def test_unapproved_strategy_or_wrong_version_is_rejected():
    for sid, ver in (("unknown", "1"), ("ema_trend", "2")):
        i = IntentRequest("k", "1HZ100V", "DIGITEVEN", sid, ver, "demo", "ACC")
        assert "strategy_approved" in ENGINE.evaluate(ctx(intent=i)).failed
    nothing = RiskEngine(CFG, StrategyApprovals())
    assert not nothing.evaluate(ctx()).approved


def test_real_mode_intent_is_rejected():
    i = IntentRequest("k", "1HZ100V", "DIGITEVEN", "ema_trend", "1", "real", "ACC")
    d = ENGINE.evaluate(ctx(intent=i, configured_mode="real"))
    assert "mode_and_account_consistent" in d.failed and not d.approved


def test_snapshot_contains_the_state_judged():
    d = ENGINE.evaluate(ctx())
    assert d.snapshot["balance"] == "1000" and d.snapshot["intent"]["symbol"] == "1HZ100V"
    assert d.snapshot["kill_switch_engaged"] is False


def test_proposal_check_rule_8():
    ok, res = ENGINE.check_proposal(D("5.00"), D("5.00"), D("9.75"))
    assert ok and [x.name for x in res] == ["proposal_price_acceptable", "proposal_payout_acceptable"]
    bad_price, _ = ENGINE.check_proposal(D("5.00"), D("5.20"), D("10.14"))
    assert not bad_price
    bad_payout, res = ENGINE.check_proposal(D("5.00"), D("5.00"), D("5.45"))
    assert not bad_payout and not res[1].passed


OWNER_DAY = parse_risk_config({**VALID, "max_trades_per_day": 5, "max_losses_per_day": 3})


def test_the_owner_day_rules_are_off_unless_set_and_on_when_set():
    assert ENGINE.evaluate(ctx(trades_today=99, losses_today=99)).approved                       # VALID sets neither: no cap, no stop
    eng = RiskEngine(OWNER_DAY, APPROVALS)
    assert eng.evaluate(ctx(trades_today=4, losses_today=2)).approved
    assert eng.evaluate(ctx(trades_today=5)).failed == ["trades_per_day_within_limit"]
    assert eng.evaluate(ctx(losses_today=3)).failed == ["losses_per_day_within_limit"]


def test_both_day_rules_are_reported_together_when_both_trip():
    d = RiskEngine(OWNER_DAY, APPROVALS).evaluate(ctx(trades_today=5, losses_today=3))
    assert sorted(d.failed) == ["losses_per_day_within_limit", "trades_per_day_within_limit"] and d.stake is None
