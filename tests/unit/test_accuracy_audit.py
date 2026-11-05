import datetime as dt
from dataclasses import replace
from decimal import Decimal as D

import pytest

from derivbot.audit.accuracy import (
    ALL_CHECKS,
    AuditData,
    ContractFacts,
    ExchangeTrade,
    LocalTrade,
    expected_stake,
    render,
    run_checks,
)

T0 = dt.datetime(2026, 11, 5, 12, 0, 0, tzinfo=dt.UTC)
EPOCH0 = int(T0.timestamp())


def trade(i, ctype, win, balance_before, balance_after_buy, opened_s):
    stake = D("1.00")
    payout = D("1.90")
    profit = payout - stake if win else -stake
    return LocalTrade(f"intent-{i:04d}", ctype, stake, "SETTLED", 1000 + i, stake, payout, profit, T0 + dt.timedelta(seconds=opened_s),
                      balance_after_buy, balance_before, True, T0 + dt.timedelta(seconds=opened_s - 1))


def good():
    """Three sequential trades: win, loss, win, with a self-consistent balance chain and exchange records."""
    b0 = D("1000.00")
    t1 = trade(1, "CALL", True, b0, b0 - D("1.00"), 0)                         # balance after buy 1 = 999.00
    b1 = t1.balance_after_buy + t1.buy_price + t1.profit                       # 999 + 1.00 + 0.90 = 1000.90
    t2 = trade(2, "PUT", False, b1, b1 - D("1.00"), 300)                       # 999.90
    b2 = t2.balance_after_buy + t2.buy_price + t2.profit                       # 999.90 + 1 - 1 = 999.90
    t3 = trade(3, "CALL", True, b2, b2 - D("1.00"), 600)
    local = [t1, t2, t3]
    exch = [ExchangeTrade(t.contract_id, t.buy_price, t.payout, t.buy_price + t.profit, EPOCH0 + int((t.opened_at - T0).total_seconds()))
            for t in local]
    facts = {t1.contract_id: ContractFacts(t1.contract_id, "CALL", D("100.00"), D("100.50"), "won"),
             t2.contract_id: ContractFacts(t2.contract_id, "PUT", D("100.00"), D("100.20"), "lost"),
             t3.contract_id: ContractFacts(t3.contract_id, "CALL", D("100.00"), D("101.00"), "won")}
    path = [(None, "x", "INTENT_CREATED"), ("INTENT_CREATED", "RISK_PASS", "RISK_APPROVED"),
            ("RISK_APPROVED", "PROPOSAL_REQUEST", "PROPOSAL_REQUESTED"), ("PROPOSAL_REQUESTED", "SEND_BUY", "BUY_SENT"),
            ("BUY_SENT", "BUY_CONFIRMED", "OPEN"), ("OPEN", "EXPIRY", "SETTLED")]
    events = {t.intent_id: list(path) for t in local}
    return AuditData(local, exch, facts, events, D("0.01"), D("1.00"), EPOCH0 - 5, now=T0 + dt.timedelta(hours=1))


def results(d):
    return {c.name: c for c in run_checks(d)}


def failed(d):
    return sorted(n for n, c in results(d).items() if not c.passed)


def test_clean_data_passes_every_check_and_every_check_looked_at_something():
    r = results(good())
    assert all(c.passed for c in r.values()), [c for c in r.values() if not c.passed]
    assert len(r) == len(ALL_CHECKS) == 12
    assert all(c.checked > 0 for c in r.values())                      # a check that examines nothing proves nothing


def test_a_settled_trade_missing_at_the_exchange_is_caught():
    d = good()
    d.exchange = d.exchange[:-1]
    assert "exchange_has_every_settled_trade" in failed(d)


def test_an_exchange_trade_the_bot_never_recorded_is_caught():
    d = good()
    d.exchange.append(ExchangeTrade(99999, D("1"), D("1.9"), D("0"), EPOCH0 + 100))
    assert failed(d) == ["no_unknown_trades_on_the_account"]


def test_a_trade_before_the_window_is_not_counted_as_foreign():
    d = good()
    d.exchange.append(ExchangeTrade(99999, D("1"), D("1.9"), D("0"), EPOCH0 - 3600))
    assert failed(d) == []


@pytest.mark.parametrize("field,value", [("buy_price", D("1.01")), ("payout", D("1.89")), ("profit", D("0.91"))])
def test_any_one_cent_difference_in_money_is_caught(field, value):
    d = good()
    d.local[0] = replace(d.local[0], **{field: value})
    assert "money_fields_match_exactly" in failed(d)


def test_a_profit_that_is_neither_a_win_nor_a_loss_is_caught():
    d = good()
    d.local[0] = replace(d.local[0], profit=D("0.50"))
    d.exchange[0] = replace(d.exchange[0], sell_price=D("1.50"))          # the exchange agrees, so the money fields still match
    f = failed(d)
    assert "profit_formula_holds" in f and "money_fields_match_exactly" not in f


def test_a_recorded_result_that_contradicts_the_contract_rule_is_caught():
    d = good()
    d.facts[1001] = ContractFacts(1001, "CALL", D("100.00"), D("99.50"), "lost")      # exit below entry: a CALL cannot win
    assert failed(d) == ["outcome_matches_contract_rule"]


def test_a_tie_loses_for_both_directions():
    d = good()
    d.facts[1001] = ContractFacts(1001, "CALL", D("100.00"), D("100.00"), "lost")
    assert "outcome_matches_contract_rule" in failed(d)                  # local says win, a tie is a loss
    d = good()
    d.local[1] = replace(d.local[1], profit=D("-1.00"))
    d.facts[1002] = ContractFacts(1002, "PUT", D("100.00"), D("100.00"), "lost")
    assert "outcome_matches_contract_rule" not in failed(d)


def test_a_broken_balance_chain_is_caught_by_one_cent():
    d = good()
    d.local[2] = replace(d.local[2], balance_after_buy=d.local[2].balance_after_buy + D("0.01"))
    assert failed(d) == ["balance_chain_is_consistent"]


def test_the_balance_chain_makes_no_claim_across_an_unsettled_contract():
    d = good()
    d.local[0] = replace(d.local[0], profit=None)
    assert "balance_chain_is_consistent" not in failed(d)


def test_a_wrong_stake_is_caught_both_too_high_and_too_low():
    for wrong in (D("1.01"), D("0.99")):
        d = good()
        d.local[1] = replace(d.local[1], stake=wrong)
        assert failed(d) == ["stake_matches_sizing_rule"]


def test_stake_rule_values():
    assert expected_stake(D("1000"), D("0.01"), D("1.00")) == D("1.00")          # capped
    assert expected_stake(D("10"), D("0.01"), D("1.00")) == D("0.10")
    assert expected_stake(D("10.99"), D("0.01"), D("1.00")) == D("0.10")         # rounded DOWN, never up
    assert expected_stake(D("3.999"), D("0.01"), D("1.00")) == D("0.03")


def test_an_illegal_state_transition_is_caught():
    d = good()
    d.events["intent-0001"][4] = ("BUY_SENT", "BUY_CONFIRMED", "SETTLED")        # skips OPEN
    assert failed(d) == ["state_transitions_are_legal"]


def test_a_buy_unknown_that_goes_back_to_buy_sent_is_caught():
    d = good()
    d.events["intent-0001"] = d.events["intent-0001"][:4] + [("BUY_SENT", "BUY_NO_REPLY", "BUY_UNKNOWN"), ("BUY_UNKNOWN", "SEND_BUY", "BUY_SENT")]
    assert "state_transitions_are_legal" in failed(d)


def test_a_broken_chain_of_states_is_caught():
    d = good()
    d.events["intent-0002"][3] = ("OPEN", "SEND_BUY", "BUY_SENT")                # does not start where the previous one ended
    assert "state_transitions_are_legal" in failed(d)


def test_an_exposed_intent_without_an_approved_risk_decision_is_caught():
    d = good()
    d.local[2] = replace(d.local[2], risk_approved=None)
    assert "exposed_intents_were_risk_approved" in failed(d)
    d = good()
    d.local[2] = replace(d.local[2], risk_approved=False)
    assert "exposed_intents_were_risk_approved" in failed(d)


def test_one_contract_on_two_orders_is_caught():
    d = good()
    d.local[1] = replace(d.local[1], contract_id=1001)
    assert "no_duplicate_contracts" in failed(d)


def test_clock_skew_beyond_the_executor_tolerance_is_caught_and_reported():
    d = good()
    d.exchange[1] = replace(d.exchange[1], purchase_time=d.exchange[1].purchase_time - 30)
    r = results(d)["clock_skew_within_tolerance"]
    assert not r.passed and "+30.00s" in r.detail


def test_an_intent_stuck_in_an_uncertain_state_is_caught():
    d = good()
    d.local.append(LocalTrade("intent-0099", "CALL", D("1.00"), "BUY_UNKNOWN", None, risk_approved=True))
    assert "nothing_stuck" in failed(d)


def test_render_lists_every_check_and_the_verdict():
    text = render(run_checks(good()), 3)
    assert text.count("  PASS  ") == 12 and "ALL PASSED" in text
    d = good()
    d.exchange = d.exchange[:-1]
    assert "FAIL  exchange_has_every_settled_trade" in render(run_checks(d), 3) and "FAILED" in render(run_checks(d), 3)


def test_each_trade_is_judged_by_the_limits_in_force_when_it_was_decided():
    """Found on the live account: the cap was raised from 1.00 to 25.00 and the audit wrongly failed the older trades."""
    d = good()                                                    # the audit's default (today's) cap is 1.00 here
    d.max_stake = D("25.00")                                      # today's cap is now much higher
    assert "stake_matches_sizing_rule" in failed(d)               # judged by today's cap, the old 1.00 stakes look wrong
    d.local = [replace(t, max_stake=D("1.00"), risk_fraction=D("0.01")) for t in d.local]
    assert "stake_matches_sizing_rule" not in failed(d)           # judged by the cap in force then, they are right


def test_a_stake_that_breaks_the_limit_in_force_at_the_time_is_still_caught():
    d = good()
    d.local = [replace(t, max_stake=D("1.00"), risk_fraction=D("0.01")) for t in d.local]
    d.local[1] = replace(d.local[1], stake=D("2.00"))             # above the cap that was in force
    assert "stake_matches_sizing_rule" in failed(d)
