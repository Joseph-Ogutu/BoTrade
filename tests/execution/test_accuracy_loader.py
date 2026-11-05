"""The audit loader and checks against trades recorded by the REAL executor (fake exchange): the bot's own records must
satisfy every local invariant, and a deliberate corruption of the database must be caught."""

import datetime as dt
from decimal import Decimal as D

from sqlalchemy import text

from derivbot.audit.__main__ import load_local
from derivbot.audit.accuracy import AuditData, ContractFacts, ExchangeTrade, run_checks
from tests.execution.conftest import EXEC_CFG


def audit_data(engine, events, local, **kw):
    ex = [ExchangeTrade(t.contract_id, t.buy_price, t.payout, t.buy_price + t.profit, int(t.opened_at.timestamp()))
          for t in local if t.state == "SETTLED"]
    facts = {t.contract_id: ContractFacts(t.contract_id, t.contract_type, D("100"), D("101") if t.profit > 0 else D("99"), None)
             for t in local if t.state == "SETTLED"}
    for t in local:
        if t.contract_type == "PUT" and t.state == "SETTLED":                      # exit/entry in the winning direction for PUT
            facts[t.contract_id] = ContractFacts(t.contract_id, "PUT", D("100"), D("99") if t.profit > 0 else D("101"), None)
    return AuditData(local, ex, facts, events, D(EXEC_CFG["risk_per_trade_pct"]), D(EXEC_CFG["max_stake_abs"]), 0, **kw)


async def test_trades_recorded_by_the_real_executor_pass_the_local_invariants(env):
    # different symbol or direction each time: an identical intent inside the window is (correctly) refused as a duplicate
    for key, kw in (("a1", {}), ("a2", {"contract_type": "PUT"}), ("a3", {"symbol": "R_100"})):
        res = await env.executor.submit(env.intent(key, **kw))
        assert res.state.value == "SETTLED", res
    local, events = load_local(env.engine, dt.datetime(2000, 1, 1, tzinfo=dt.UTC))
    assert len(local) == 3 and all(t.balance_after_buy is not None and t.balance_before_decision is not None for t in local)
    checks = {c.name: c for c in run_checks(audit_data(env.engine, events, local))}
    local_checks = ("state_transitions_are_legal", "exposed_intents_were_risk_approved", "no_duplicate_contracts", "nothing_stuck",
                    "stake_matches_sizing_rule", "profit_formula_holds")
    for name in local_checks:
        assert checks[name].passed and checks[name].checked > 0, checks[name]


async def test_a_corrupted_record_in_the_database_is_caught(env):
    await env.executor.submit(env.intent("b1"))
    with env.engine.begin() as c:
        c.execute(text("UPDATE orders SET profit = profit + 0.01"))                   # one cent of silent corruption
    local, events = load_local(env.engine, dt.datetime(2000, 1, 1, tzinfo=dt.UTC))
    failed = {c.name for c in run_checks(audit_data(env.engine, events, local)) if not c.passed}
    assert "profit_formula_holds" in failed


async def test_an_event_inserted_out_of_order_is_caught(env):
    await env.executor.submit(env.intent("c1"))
    with env.engine.begin() as c:
        c.execute(text("INSERT INTO order_events (intent_id, from_state, to_state, event, payload) "
                       "SELECT id, 'SETTLED', 'OPEN', 'SEND_BUY', '{}'::jsonb FROM order_intents"))
    local, events = load_local(env.engine, dt.datetime(2000, 1, 1, tzinfo=dt.UTC))
    assert "state_transitions_are_legal" in {c.name for c in run_checks(audit_data(env.engine, events, local)) if not c.passed}


async def test_the_loader_attaches_the_limits_in_force_when_each_trade_was_decided(env):
    await env.executor.submit(env.intent("d1"))
    local, _ = load_local(env.engine, dt.datetime(2000, 1, 1, tzinfo=dt.UTC))
    assert local[0].max_stake == D(EXEC_CFG["max_stake_abs"]) and local[0].risk_fraction == D(EXEC_CFG["risk_per_trade_pct"])
