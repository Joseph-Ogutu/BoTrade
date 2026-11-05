from decimal import Decimal as D

from derivbot.config.risk import StrategyApprovals
from derivbot.execution.executor import State
from derivbot.execution.soak import SOAK_ID, SOAK_VERSION, SoakTrader
from derivbot.risk.engine import RiskEngine


async def test_one_soak_trade_goes_through_the_real_executor_and_settles(env):
    env.deps.risk = RiskEngine(env.deps.risk.cfg, StrategyApprovals(approved={SOAK_ID: SOAK_VERSION}))
    env.executor.d.risk = env.deps.risk
    t = SoakTrader(env.executor.submit, "ACC", env.kill_switch.is_engaged, lambda: True, interval_s=30.0)
    assert await t.run_once() is True
    row = env.q("SELECT state, mode, stake FROM order_intents")
    assert len(row) == 1 and row[0][0] == "SETTLED" and row[0][1] == "demo" and row[0][2] <= D("5")
    assert env.fake.buys_received == 1 and "SETTLED" in (t.last_result or "")
    assert env.q("SELECT count(*) FROM risk_decisions WHERE approved")[0][0] == 1       # it passed the risk engine like any trade


async def test_without_the_in_memory_approval_the_soak_is_refused_by_risk_and_nothing_is_bought(env):
    t = SoakTrader(env.executor.submit, "ACC", env.kill_switch.is_engaged, lambda: True, interval_s=30.0)
    await t.run_once()
    assert env.fake.buys_received == 0
    assert env.q("SELECT state FROM order_intents")[0][0] == "RISK_REJECTED"
    assert State.RISK_REJECTED
