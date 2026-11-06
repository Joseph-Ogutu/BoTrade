"""The executor in REAL mode, against the fake exchange (no real endpoint, no real money). Every refusal must leave nothing bought."""

import asyncio
from decimal import Decimal as D

from derivbot.execution.executor import ConnInfo
from derivbot.execution.snapshot import fetch_snapshot
from derivbot.execution.state_machine import State
from tests.fakes.fake_deriv import Fault

REAL_ID = "ROT00000002"


def go_real(env, unlocked=True, info=None):
    env.deps.configured_mode = "real"
    env.deps.real_unlocked = unlocked
    env.info["v"] = info or ConnInfo("real", False, D("1000"), "USD", REAL_ID)


def real_intent(env, key="r1", **kw):
    return env.intent(key, mode="real", account_id=REAL_ID, **kw)


async def test_a_real_intent_flows_through_every_check_and_is_stored_as_real(env):
    go_real(env)
    res = await env.executor.submit(real_intent(env))
    assert res.state is State.SETTLED and env.fake.buys_received == 1
    assert env.q("SELECT mode, account_id FROM order_intents")[0] == ("real", REAL_ID)
    assert env.q("SELECT mode FROM orders")[0][0] == "real"


async def test_without_the_guards_unlock_a_real_intent_is_rejected_by_risk_rule_two_and_nothing_is_bought(env):
    go_real(env, unlocked=False)
    res = await env.executor.submit(real_intent(env))
    assert res.state is State.RISK_REJECTED and env.fake.buys_received == 0
    failed = env.q("SELECT rule_results FROM risk_decisions ORDER BY id DESC LIMIT 1")[0][0]
    assert "mode_and_account_consistent" in [x["name"] for x in failed if not x["passed"]]


async def test_a_real_process_on_the_demo_endpoint_or_a_demo_account_buys_nothing(env):
    for info in (ConnInfo("demo", True, D("1000"), "USD", "DOT1"), ConnInfo("real", True, D("1000"), "USD", REAL_ID),
                 ConnInfo("real", None, D("1000"), "USD", REAL_ID), ConnInfo(None, None, D("1000"), "USD", None)):
        go_real(env, info=info)
        res = await env.executor.submit(real_intent(env, key=f"x{info.endpoint_mode}{info.account_is_demo}"))
        assert res.state is State.RISK_REJECTED
    assert env.fake.buys_received == 0


async def test_the_real_connection_is_re_read_just_before_the_buy_and_a_flip_stops_it(env):
    go_real(env)
    env.fake.inject(Fault("proposal", "delay", seconds=0.4))

    async def flip():
        await asyncio.sleep(0.15)
        env.info["v"] = ConnInfo("demo", True, D("1000"), "USD", "DOT1")             # the connection changed after the risk decision

    task = asyncio.create_task(flip())
    res = await env.executor.submit(real_intent(env, key="flip"))
    await task
    assert env.fake.buys_received == 0 and "real mode not confirmed before buy" in res.reason


async def test_unlocking_in_a_demo_process_changes_nothing_and_a_real_intent_there_engages_the_kill_switch(env):
    env.deps.real_unlocked = True                                                       # even unlocked, the process mode is demo
    res = await env.executor.submit(real_intent(env, key="stray"))
    assert res.state is None and "mode mismatch" in res.reason and env.kill_switch.is_engaged()
    assert env.fake.buys_received == 0 and any(lvl == "critical" for lvl, _ in env.alerts)


async def test_a_demo_intent_in_a_real_process_is_refused_the_same_way(env):
    go_real(env)
    res = await env.executor.submit(env.intent("stray2"))                               # mode demo
    assert res.state is None and "mode mismatch" in res.reason and env.kill_switch.is_engaged() and env.fake.buys_received == 0


async def test_the_real_limits_of_the_daily_rules_apply_in_real_mode_too(env):
    go_real(env)
    cfg = env.deps.risk.cfg.model_copy(update={"max_trades_per_day": 1, "max_trades_per_hour": 100, "recent_duplicate_window_s": 1})
    from derivbot.risk.engine import RiskEngine

    env.deps.risk = RiskEngine(cfg, env.deps.risk.approvals)
    assert (await env.executor.submit(real_intent(env, key="a", symbol="1HZ100V"))).state is State.SETTLED
    second = await env.executor.submit(real_intent(env, key="b", symbol="R_100"))
    assert second.state is State.RISK_REJECTED and env.fake.buys_received == 1


async def test_an_adopted_contract_is_recorded_in_the_process_mode(env):
    go_real(env)
    env.fake.inject(Fault("buy", "drop_after"))                                         # the buy is placed, its reply is lost
    res = await env.executor.submit(real_intent(env, key="lost"))
    assert res.state is State.BUY_UNKNOWN and len(env.fake.contracts) == 1
    env.deps.buy_unknown_window_s = 3600.0
    session = await env.new_session()
    snap = await fetch_snapshot(session, int(env.deps.clock().timestamp()))
    out = await env.executor.resolve_unknown(snap)
    assert out[res.intent_id] in (State.OPEN, State.SETTLED) and len(env.fake.buy_messages) == 1       # found, never bought twice
    assert {row[0] for row in env.q("SELECT mode FROM orders")} == {"real"}              # adopted in the process mode, not a hard-coded one
