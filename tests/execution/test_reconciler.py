import asyncio
from decimal import Decimal as D

import pytest
from sqlalchemy import text

from derivbot.db import repo
from derivbot.deriv import messages as m
from derivbot.deriv.session import DerivSession, SessionSupervisor
from derivbot.execution.executor import Executor
from derivbot.execution.reconciler import Reconciler
from derivbot.execution.state_machine import Event, State
from tests.fakes.fake_deriv import Fault

pytestmark = pytest.mark.postgres


@pytest.fixture()
def rec(env):
    return Reconciler(env.executor, interval_s=30.0)


def runs(env):
    return env.q("SELECT trigger, status, local_open, remote_open, discrepancies FROM reconciliation_runs ORDER BY id")


def orders_snapshot(env):
    return env.q("SELECT intent_id, deriv_contract_id, state, buy_price, profit, settled_at FROM orders ORDER BY intent_id")


async def _url(u):
    return u


async def foreign_contract(env, stake="5.00"):
    other = DerivSession(lambda: _url(env.fake.url))
    await other.connect()
    prop = m.parse_message(await other.request(m.req_proposal("1HZ100V", "DIGITEVEN", D(stake), 5, "t", "USD", other.next_req_id())))
    buy = m.parse_message(await other.request(m.req_buy(prop.proposal.id, D("5.10"), other.next_req_id())))
    await other.close()
    return buy.buy.contract_id


def make_open_order(env, key, contract_id):
    with env.engine.begin() as c:
        i = repo.create_intent(c, idempotency_key=key, mode="demo", account_id="ACC", symbol="1HZ100V",
                               contract_type="DIGITEVEN", stake=D("5.00"))
        for st, ev in ((State.INTENT_CREATED, Event.RISK_PASS), (State.RISK_APPROVED, Event.PROPOSAL_REQUEST),
                       (State.PROPOSAL_REQUESTED, Event.SEND_BUY), (State.BUY_SENT, Event.BUY_CONFIRMED)):
            repo.transition(c, i, st, ev)
        repo.open_order(c, i, contract_id=contract_id, transaction_id=None, buy_price=D("5.00"), payout=D("9.75"),
                        mode="demo", account_id="ACC")
    return i


def test_interval_below_the_rate_budget_is_refused(env):
    with pytest.raises(ValueError):
        Reconciler(env.executor, interval_s=5)


async def test_clean_state_records_a_clean_run_and_sets_the_age(env, rec):
    assert rec.age_s() is None
    out = await rec.run("startup")
    assert out == [] and runs(env)[0][:4] == ("startup", "clean", 0, 0)
    assert rec.age_s() is not None and rec.age_s() < 5
    assert not env.kill_switch.is_engaged()


async def test_a_normally_settled_trade_reconciles_clean(env, rec):
    r = await env.executor.submit(env.intent())
    assert r.state is State.SETTLED
    assert await rec.run("periodic") == [] and runs(env)[-1][1] == "clean"


async def test_a_foreign_open_contract_is_reported_pauses_trading_and_changes_nothing(env, rec):
    env.fake.settle_after = 30.0
    cid = await foreign_contract(env)
    before = orders_snapshot(env)
    out = await rec.run("periodic")
    assert [d["kind"] for d in out] == ["remote_open_missing_locally"] and out[0]["ref"] == cid
    assert runs(env)[-1][1] == "discrepancy" and runs(env)[-1][3] == 1
    assert env.kill_switch.is_engaged() and env.kill_switch.log.events[-1][1] == "reconciler"
    assert any(lvl == "critical" for lvl, _ in env.alerts)
    assert rec.age_s() is None                           # a discrepancy is not a good run
    assert orders_snapshot(env) == before                # nothing was "fixed"


async def test_settled_while_untracked_is_reported_not_repaired_until_an_operator_resolves_it(env, rec):
    r = await env.executor.submit(env.intent(), wait_settlement=False)       # OPEN, settlement task running
    await env.executor.shutdown()                                            # the process "dies" before settlement is seen
    env.tracker.forget(r.contract_id)
    await asyncio.sleep(0.5)                                                 # the contract settles on the exchange
    before = orders_snapshot(env)
    out = await rec.run("reconnect")
    assert [d["kind"] for d in out] == ["settled_while_untracked"] and out[0]["detail"]["profit_from_profit_table"] == "4.75"
    assert env.kill_switch.is_engaged() and orders_snapshot(env) == before
    assert env.state_of(r.intent_id) == "OPEN"
    # explicit, audited operator action
    with pytest.raises(ValueError):
        await rec.resolve_settled_while_untracked(r.intent_id, "", "x")
    with pytest.raises(LookupError):
        await rec.resolve_settled_while_untracked("00000000-0000-0000-0000-000000000000", "joseph", "checked statement")
    profit = await rec.resolve_settled_while_untracked(r.intent_id, "joseph", "checked the statement")
    assert profit == D("4.75") and env.state_of(r.intent_id) == "SETTLED"
    audit = env.q("SELECT payload FROM audit_events WHERE ref_id = :i AND event_type = 'RECONCILE'", i=r.intent_id)
    assert any(a[0].get("operator") == "joseph" for a in audit)


async def test_a_local_open_order_the_exchange_has_never_heard_of_is_vanished(env, rec):
    make_open_order(env, "ghost", 999999)
    out = await rec.run("periodic")
    assert [d["kind"] for d in out] == ["vanished"] and env.kill_switch.is_engaged()


async def test_a_profit_that_disagrees_with_profit_table_is_reported(env, rec):
    r = await env.executor.submit(env.intent())
    with env.engine.begin() as c:
        c.execute(text("UPDATE orders SET profit = 1.00 WHERE intent_id = :i"), {"i": r.intent_id})
    out = await rec.run("periodic")
    assert [d["kind"] for d in out] == ["profit_mismatch"]
    assert out[0]["detail"] == {"local": "1.00", "remote": "4.75"}


async def test_unknown_buys_are_resolved_inside_the_run_and_the_result_is_recorded(env, rec):
    env.fake.inject(Fault("buy", "drop_after"))
    r = await env.executor.submit(env.intent())
    assert r.state is State.BUY_UNKNOWN
    s = await env.new_session()
    out = await rec.run("reconnect", s)
    assert out == [] and runs(env)[-1][1] == "clean"
    payload = runs(env)[-1][4]
    assert payload[-1]["kind"] == "resolved_unknown_buys" and r.intent_id in payload[-1]["detail"]
    for _ in range(60):
        if env.state_of(r.intent_id) == "SETTLED":
            break
        await asyncio.sleep(0.05)
    assert env.state_of(r.intent_id) == "SETTLED" and len(env.fake.buy_messages) == 1


async def test_a_buy_left_in_flight_by_a_crash_is_recovered_through_buy_unknown(env, rec):
    with env.engine.begin() as c:
        i = repo.create_intent(c, idempotency_key="crash", mode="demo", account_id="ACC", symbol="1HZ100V",
                               contract_type="DIGITEVEN", stake=D("5.00"))
        for st, ev in ((State.INTENT_CREATED, Event.RISK_PASS), (State.RISK_APPROVED, Event.PROPOSAL_REQUEST),
                       (State.PROPOSAL_REQUESTED, Event.SEND_BUY)):
            repo.transition(c, i, st, ev)
        c.execute(text("UPDATE order_events SET ts = now() - interval '5 minutes' WHERE intent_id = :i"), {"i": i})
    env.deps.buy_unknown_window_s = 0.0
    out = await rec.run("startup")
    assert out == [] and env.state_of(i) == "NOT_PLACED"
    seq = [x[0] for x in env.q("SELECT to_state FROM order_events WHERE intent_id = :i ORDER BY id", i=i)]
    assert seq[-3:] == ["BUY_SENT", "BUY_UNKNOWN", "NOT_PLACED"]
    assert env.fake.buy_messages == []                     # recovered without sending anything


async def test_a_buy_that_is_still_being_submitted_in_this_process_is_not_touched(env, rec):
    env.fake.inject(Fault("buy", "delay", seconds=0.5))
    task = asyncio.create_task(env.executor.submit(env.intent("live")))
    await asyncio.sleep(0.2)
    rec.stuck_after = 0.0
    await rec.run("startup")                               # would convert a stuck BUY_SENT, but this one is in flight
    r = await task
    assert r.state is State.SETTLED and len(env.fake.buy_messages) == 1


async def test_a_failed_snapshot_is_recorded_alerted_and_does_not_count_as_a_good_run(env, rec):
    env.fake.inject(Fault("portfolio", "error", code="FakeError", message="x"))        # UNVERIFIED-ERR: invented
    out = await rec.run("periodic")
    assert [d["kind"] for d in out] == ["snapshot_failed"] and rec.age_s() is None
    assert any(lvl == "warning" for lvl, _ in env.alerts) and not env.kill_switch.is_engaged()
    assert await rec.run("periodic") == [] and rec.age_s() is not None            # recovers on the next good run


async def test_no_session_is_a_failed_run_not_a_crash(env, rec):
    env.deps.get_session = lambda: None
    out = await rec.run("periodic")
    assert out[0]["kind"] == "snapshot_failed" and "no connected session" in out[0]["detail"]["why"]


async def test_an_acknowledged_foreign_contract_stops_re_pausing_trading(env, rec):
    env.fake.settle_after = 30.0
    cid = await foreign_contract(env)
    assert (await rec.run("periodic"))[0]["kind"] == "remote_open_missing_locally"
    with pytest.raises(ValueError):
        await rec.acknowledge_foreign(cid, "", "x")
    await rec.acknowledge_foreign(cid, "joseph", "my own manual trade")
    env.kill_switch.release("api", "reviewed, manual trade")
    assert await rec.run("periodic") == [] and not env.kill_switch.is_engaged()


async def test_periodic_loop_runs_repeatedly_and_stops_on_halt(env, rec):
    rec.interval_s = 0.1
    halt = asyncio.Event()
    task = asyncio.create_task(rec.run_periodic(halt))
    await asyncio.sleep(0.6)
    halt.set()
    await asyncio.wait_for(task, 3)
    assert len(runs(env)) >= 3 and all(r[0] == "periodic" for r in runs(env))


async def test_supervisor_reconciles_on_start_and_on_every_reconnect_before_the_session_is_published(env, rec):
    await env.session.close()
    published_before_reconcile = []

    def factory():
        s = DerivSession(lambda: _url(env.fake.url))
        s.on("proposal_open_contract", env.tracker.handle)
        return s

    sup = SessionSupervisor(factory, on_connected=[rec.on_connected], backoff_base=0.05, backoff_cap=0.1)

    async def spy(session):
        published_before_reconcile.append(sup.session)

    sup._on_connected.insert(0, spy)
    halt = asyncio.Event()
    task = asyncio.create_task(sup.run(halt))
    for _ in range(100):
        if sup.session is not None:
            break
        await asyncio.sleep(0.05)
    await env.fake.drop_all()
    for _ in range(100):
        if sup.connects >= 2 and sup.session is not None:
            break
        await asyncio.sleep(0.05)
    halt.set()
    await asyncio.wait_for(task, 5)
    assert [r[0] for r in runs(env)][:2] == ["startup", "reconnect"]
    assert all(s is None for s in published_before_reconcile)         # the executor never saw an unreconciled session


async def test_restart_resumes_settlement_tracking_for_open_orders(env, rec):
    r = await env.executor.submit(env.intent(), wait_settlement=False)
    await env.executor.shutdown()
    env.tracker.forget(r.contract_id)
    env.fake.settle_after = 0.3
    # a new process: new executor, same database
    new_ex = Executor(env.deps)
    new_rec = Reconciler(new_ex, interval_s=30.0)
    await new_rec.resume_tracking()
    for _ in range(100):
        if env.state_of(r.intent_id) == "SETTLED":
            break
        await asyncio.sleep(0.05)
    assert env.state_of(r.intent_id) == "SETTLED"
    await new_ex.shutdown()
    assert env.q("SELECT count(*) FROM orders")[0][0] == 1


async def test_the_reconciler_module_never_writes_to_orders_directly():
    import inspect

    import derivbot.execution.reconciler as mod

    src = inspect.getsource(mod)
    for needle in ("UPDATE orders", "DELETE FROM orders", "INSERT INTO orders", "settle_order(", "open_order("):
        assert needle not in src, needle
