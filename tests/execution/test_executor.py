"""Executor failure-injection suite (HANDBOOK 7 and 14). Every test ends by checking the global invariants."""

import asyncio
from decimal import Decimal as D

import pytest

from derivbot.execution.executor import ConnInfo
from derivbot.execution.snapshot import fetch_snapshot
from derivbot.execution.state_machine import State
from derivbot.risk.engine import RiskEngine as _RE
from tests.fakes.fake_deriv import Fault

pytestmark = pytest.mark.postgres


def check_invariants(env, foreign_buys=0):
    """Never more buy messages than intents that reached BUY_SENT (plus buys by other clients that the test made
    on purpose), and no proposal id bought twice."""
    sent = env.q("SELECT count(*) FROM order_events WHERE to_state = 'BUY_SENT'")[0][0]
    assert len(env.fake.buy_messages) - foreign_buys <= sent
    assert len(env.fake.buy_messages) == len(set(env.fake.buy_messages))
    # every buy that reached the server belongs to an intent with an approved risk decision
    unapproved = env.q("SELECT count(*) FROM order_intents i LEFT JOIN risk_decisions r ON r.intent_id = i.id "
                       "WHERE i.state IN ('BUY_SENT','OPEN','BUY_UNKNOWN','SETTLED','SOLD','MANUAL_REVIEW') "
                       "AND (r.approved IS DISTINCT FROM true)")[0][0]
    assert unapproved == 0


async def wait_for_state(env, intent_id, state, timeout=5.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if env.state_of(intent_id) == state.value:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"intent stayed {env.state_of(intent_id)}, wanted {state.value}")


async def reconcile_after_window(env):
    """Reconnect, then resolve BUY_UNKNOWN as if the window has passed."""
    env.deps.buy_unknown_window_s = 0.0
    s = await env.new_session()
    snap = await fetch_snapshot(s, int(env.deps.clock().timestamp()))
    return await env.executor.resolve_unknown(snap)


# ------------------------------------------------------------------ the normal path

async def test_happy_path_ends_settled_with_a_full_audit_trail(env):
    r = await env.executor.submit(env.intent())
    assert r.state is State.SETTLED and r.profit == D("4.75") and r.contract_id is not None
    assert len(env.fake.buy_messages) == 1 and env.fake.buys_received == 1
    events = [x[0] for x in env.q("SELECT to_state FROM order_events WHERE intent_id = :i ORDER BY id", i=r.intent_id)]
    assert events == ["INTENT_CREATED", "RISK_APPROVED", "PROPOSAL_REQUESTED", "BUY_SENT", "OPEN", "SETTLED"]
    order = env.q("SELECT buy_price, profit, state, deriv_contract_id FROM orders WHERE intent_id = :i", i=r.intent_id)[0]
    assert order.buy_price == D("5.00") and order.profit == D("4.75") and order.state == "SETTLED"
    audit = {x[0] for x in env.q("SELECT event_type FROM audit_events WHERE ref_id = :i", i=r.intent_id)}
    assert {"RISK", "RISK_PROPOSAL", "BUY_SENT", "BUY_CONFIRMED", "SETTLED"} <= audit
    rd = env.q("SELECT approved, jsonb_array_length(rule_results) FROM risk_decisions WHERE intent_id = :i", i=r.intent_id)[0]
    assert tuple(rd) == (True, 14)
    check_invariants(env)


async def test_a_loss_is_recorded_as_a_negative_profit(env):
    env.fake.win = False
    r = await env.executor.submit(env.intent())
    assert r.state is State.SETTLED and r.profit == D("-5.00")
    check_invariants(env)


# ------------------------------------------------------------------ rejected before any money moves

async def test_risk_rejection_sends_nothing_to_deriv(env):
    env.kill_switch.engage("api", "test")
    r = await env.executor.submit(env.intent())
    assert r.state is State.RISK_REJECTED
    assert not any("proposal" in m or "buy" in m for m in env.fake.received)
    check_invariants(env)


async def test_unconfirmed_demo_account_is_rejected_by_risk(env):
    env.info["v"] = ConnInfo("demo", None, D("1000"), "USD", "?")
    r = await env.executor.submit(env.intent())
    assert r.state is State.RISK_REJECTED and "mode_and_account_consistent" in r.reason
    assert env.fake.buy_messages == []


async def test_a_real_mode_intent_cannot_even_be_stored_and_engages_the_kill_switch(env):
    r = await env.executor.submit(env.intent(mode="real"))
    assert r.state is None and env.kill_switch.is_engaged()
    assert env.fake.buy_messages == [] and any(lvl == "critical" for lvl, _ in env.alerts)


async def test_duplicate_idempotency_key_is_one_intent_one_buy(env):
    a = await env.executor.submit(env.intent("same"))
    b = await env.executor.submit(env.intent("same"))
    assert a.state is State.SETTLED and b.intent_id is None and "duplicate" in b.reason
    assert env.q("SELECT count(*) FROM order_intents")[0][0] == 1 and len(env.fake.buy_messages) == 1


async def test_concurrent_submits_of_one_key_buy_once(env):
    results = await asyncio.gather(*(env.executor.submit(env.intent("race")) for _ in range(4)))
    assert sum(1 for r in results if r.state is State.SETTLED) == 1
    assert len(env.fake.buy_messages) == 1 and env.q("SELECT count(*) FROM order_intents")[0][0] == 1
    check_invariants(env)


async def test_proposal_error_rejects_without_buying(env):
    env.fake.inject(Fault("proposal", "error", code="FakeError", message="nope"))      # UNVERIFIED-ERR: invented
    r = await env.executor.submit(env.intent())
    assert r.state is State.PROPOSAL_REJECTED and env.fake.buy_messages == []
    check_invariants(env)


async def test_proposal_timeout_rejects_without_buying(env):
    env.fake.inject(Fault("proposal", "delay", seconds=2.0))
    r = await env.executor.submit(env.intent())
    assert r.state is State.PROPOSAL_REJECTED and "proposal failed" in r.reason and env.fake.buy_messages == []


async def test_a_price_that_moved_beyond_the_cap_is_not_bought(env):
    env.fake.proposal_markup = 0.10
    r = await env.executor.submit(env.intent())
    assert r.state is State.PROPOSAL_REJECTED and "rule 8" in r.reason and env.fake.buy_messages == []
    rule = env.q("SELECT payload FROM audit_events WHERE ref_id = :i AND event_type = 'RISK_PROPOSAL'", i=r.intent_id)[0][0]
    assert rule["approved"] is False


async def test_an_unacceptable_payout_is_not_bought(env):
    env.fake.r = 0.05
    r = await env.executor.submit(env.intent())
    assert r.state is State.PROPOSAL_REJECTED and env.fake.buy_messages == []


async def test_kill_switch_engaged_between_proposal_and_buy_stops_the_buy(env):
    env.fake.inject(Fault("proposal", "delay", seconds=0.3))
    task = asyncio.create_task(env.executor.submit(env.intent()))
    await asyncio.sleep(0.1)
    env.kill_switch.engage("telegram", "kill")
    r = await task
    assert r.state is State.PROPOSAL_REJECTED and "kill switch" in r.reason and env.fake.buy_messages == []


# ------------------------------------------------------------------ the dangerous cases: a buy of unknown outcome

async def test_drop_before_processing_nothing_placed_resolves_to_not_placed_and_is_never_retried(env):
    env.fake.inject(Fault("buy", "drop_before"))
    r = await env.executor.submit(env.intent())
    assert r.state is State.BUY_UNKNOWN and env.fake.contracts == {}
    # inside the window the order stays unresolved: absence is not believed too early
    env.deps.buy_unknown_window_s = 3600.0
    s = await env.new_session()
    early = await env.executor.resolve_unknown(await fetch_snapshot(s, int(env.deps.clock().timestamp())))
    assert early == {} and env.state_of(r.intent_id) == "BUY_UNKNOWN"
    resolved = await reconcile_after_window(env)
    assert resolved[r.intent_id] is State.NOT_PLACED
    assert len(env.fake.buy_messages) == 1             # one buy message ever, never replayed
    check_invariants(env)


async def test_drop_after_processing_reply_lost_is_found_and_settled_without_a_second_buy(env):
    """The case the old bot could not handle: the exchange placed the contract, the reply never arrived."""
    env.fake.inject(Fault("buy", "drop_after"))
    r = await env.executor.submit(env.intent())
    assert r.state is State.BUY_UNKNOWN and len(env.fake.contracts) == 1
    assert env.q("SELECT count(*) FROM orders")[0][0] == 0           # nothing recorded yet: honest about not knowing
    env.deps.buy_unknown_window_s = 3600.0                           # found contracts are adopted regardless of the window
    s = await env.new_session()
    snap = await fetch_snapshot(s, int(env.deps.clock().timestamp()))
    out = await env.executor.resolve_unknown(snap)
    assert out[r.intent_id] in (State.OPEN, State.SETTLED)
    await wait_for_state(env, r.intent_id, State.SETTLED)
    assert len(env.fake.contracts) == 1 and len(env.fake.buy_messages) == 1
    assert env.q("SELECT count(*) FROM orders")[0][0] == 1
    check_invariants(env)


async def test_reply_swallowed_with_the_connection_open_is_resolved_immediately(env):
    env.fake.inject(Fault("buy", "swallow"))
    r = await env.executor.submit(env.intent(), wait_settlement=True)
    assert len(env.fake.contracts) == 1 and len(env.fake.buy_messages) == 1
    assert r.state in (State.OPEN, State.SETTLED)
    await wait_for_state(env, r.intent_id, State.SETTLED)
    check_invariants(env)


async def test_an_error_reply_to_buy_is_not_assumed_final_and_waits_out_the_window(env):
    env.fake.inject(Fault("buy", "error", code="FakeError", message="boom"))      # UNVERIFIED-ERR: invented
    r = await env.executor.submit(env.intent())
    assert r.state is State.BUY_UNKNOWN and "error" in r.reason
    resolved = await reconcile_after_window(env)
    assert resolved[r.intent_id] is State.NOT_PLACED and len(env.fake.buy_messages) == 1


async def test_contract_that_settled_before_we_looked_is_found_in_profit_table(env):
    """Portfolio lists only OPEN contracts. A fast contract can be gone from it; profit_table must be consulted."""
    env.fake.inject(Fault("buy", "drop_after"))
    r = await env.executor.submit(env.intent())
    await asyncio.sleep(0.4)                                          # fake settles in 0.15 s
    s = await env.new_session()
    snap = await fetch_snapshot(s, int(env.deps.clock().timestamp()))
    assert snap.portfolio == () and len(snap.settled) == 1
    env.deps.buy_unknown_window_s = 0.0
    out = await env.executor.resolve_unknown(snap)
    assert out[r.intent_id] is State.SETTLED
    order = env.q("SELECT profit, state FROM orders WHERE intent_id = :i", i=r.intent_id)[0]
    assert order.profit == D("4.75") and order.state == "SETTLED"
    src = [x[0].get("source") for x in env.q("SELECT payload FROM audit_events WHERE ref_id = :i AND event_type = 'SETTLED'", i=r.intent_id)]
    assert "profit_table" in src
    check_invariants(env)


async def test_a_foreign_contract_that_also_matches_makes_the_unknown_buy_ambiguous(env):
    """Another client (a manual trade) holds an identical contract. Which one is ours cannot be known: stop and ask."""
    from derivbot.deriv import messages as m
    from derivbot.deriv.session import DerivSession

    other = DerivSession(lambda: _url(env.fake.url))
    await other.connect()
    prop = m.parse_message(await other.request(m.req_proposal("1HZ100V", "DIGITEVEN", D("5.00"), 5, "t", "USD",
                                                                other.next_req_id())))
    m.parse_message(await other.request(m.req_buy(prop.proposal.id, D("5.10"), other.next_req_id())))
    await other.close()
    assert len(env.fake.contracts) == 1
    env.fake.inject(Fault("buy", "drop_after"))
    r = await env.executor.submit(env.intent("ambiguous"))
    assert r.state is State.BUY_UNKNOWN and len(env.fake.contracts) == 2
    s = await env.new_session()
    out = await env.executor.resolve_unknown(await fetch_snapshot(s, int(env.deps.clock().timestamp())))
    assert out == {r.intent_id: State.MANUAL_REVIEW}
    assert env.kill_switch.is_engaged() and any(lvl == "critical" for lvl, _ in env.alerts)
    assert env.q("SELECT count(*) FROM orders")[0][0] == 0           # nothing attributed to the wrong contract
    check_invariants(env, foreign_buys=1)


async def test_two_unknown_intents_that_could_own_the_same_contract_both_go_to_manual_review(env):
    from derivbot.db import repo
    from derivbot.deriv import messages as m
    from derivbot.execution.snapshot import ReconcileSnapshot
    from derivbot.execution.state_machine import Event

    now = int(env.deps.clock().timestamp())
    ids = []
    with env.engine.begin() as c:
        for k in ("c-1", "c-2"):
            i = repo.create_intent(c, idempotency_key=k, mode="demo", account_id="ACC", symbol="1HZ100V",
                                   contract_type="DIGITEVEN", stake=D("5.00"))
            for st, ev in ((State.INTENT_CREATED, Event.RISK_PASS), (State.RISK_APPROVED, Event.PROPOSAL_REQUEST),
                           (State.PROPOSAL_REQUESTED, Event.SEND_BUY), (State.BUY_SENT, Event.BUY_NO_REPLY)):
                repo.transition(c, i, st, ev)
            ids.append(i)
    contract = m.PortfolioContract(contract_id=777, buy_price=D("5.00"), payout=D("9.75"), contract_type="DIGITEVEN",
                                   underlying_symbol="1HZ100V", purchase_time=now)
    out = await env.executor.resolve_unknown(ReconcileSnapshot((contract,), (), now))
    assert out == {ids[0]: State.MANUAL_REVIEW, ids[1]: State.MANUAL_REVIEW}
    assert env.kill_switch.is_engaged() and env.q("SELECT count(*) FROM orders")[0][0] == 0


async def test_a_matching_contract_already_attributed_to_another_order_is_not_reused(env):
    r = await env.executor.submit(env.intent("first"))               # settles normally, owns its contract id
    assert r.state is State.SETTLED
    from derivbot.db import repo
    from derivbot.deriv import messages as m
    from derivbot.execution.snapshot import ReconcileSnapshot
    from derivbot.execution.state_machine import Event

    now = int(env.deps.clock().timestamp())
    with env.engine.begin() as c:
        i = repo.create_intent(c, idempotency_key="second", mode="demo", account_id="ACC", symbol="1HZ100V",
                               contract_type="DIGITEVEN", stake=D("5.00"))
        for st, ev in ((State.INTENT_CREATED, Event.RISK_PASS), (State.RISK_APPROVED, Event.PROPOSAL_REQUEST),
                       (State.PROPOSAL_REQUESTED, Event.SEND_BUY), (State.BUY_SENT, Event.BUY_NO_REPLY)):
            repo.transition(c, i, st, ev)
    owned = m.ProfitTransaction(contract_id=r.contract_id, buy_price=D("5.00"), sell_price=D("9.75"), payout=D("9.75"),
                                contract_type="DIGITEVEN", underlying_symbol="1HZ100V", purchase_time=now)
    env.deps.buy_unknown_window_s = 3600.0
    out = await env.executor.resolve_unknown(ReconcileSnapshot((), (owned,), now))
    assert out == {} and env.state_of(i) == "BUY_UNKNOWN"             # not adopted; waits for the window


async def test_unresolved_buy_unknown_blocks_further_trading_through_the_open_positions_rule(env):
    from derivbot.config.risk import StrategyApprovals, parse_risk_config
    from derivbot.risk.engine import RiskEngine
    from tests.execution.conftest import EXEC_CFG

    env.deps.risk = RiskEngine(parse_risk_config({**EXEC_CFG, "max_open_positions": 1}), StrategyApprovals(approved={"ema_trend": "1"}))
    env.fake.inject(Fault("buy", "drop_before"))
    a = await env.executor.submit(env.intent("first"))
    await env.new_session()
    b = await env.executor.submit(env.intent("second"))
    assert a.state is State.BUY_UNKNOWN and b.state is State.RISK_REJECTED
    assert "open_positions_within_limit" in b.reason and len(env.fake.buy_messages) == 1


# ------------------------------------------------------------------ messages that must not be trusted blindly

async def test_a_duplicated_buy_reply_records_one_order(env):
    env.fake.inject(Fault("buy", "duplicate"))
    r = await env.executor.submit(env.intent())
    assert r.state is State.SETTLED
    assert env.q("SELECT count(*) FROM orders")[0][0] == 1 and len(env.fake.buy_messages) == 1
    check_invariants(env)


async def test_a_settled_message_without_profit_is_not_taken_as_a_result(env):
    env.fake.settle_profit_missing = True                # UNVERIFIED: whether Deriv ever sends a settled message like this
    r = await env.executor.submit(env.intent(), settlement_timeout=0.6)
    assert r.state is State.OPEN and "not seen" in r.reason
    assert env.q("SELECT profit FROM orders WHERE intent_id = :i", i=r.intent_id)[0][0] is None


async def test_disconnect_during_settlement_reconnects_resubscribes_and_settles_once(env):
    from derivbot.deriv.session import DerivSession, SessionSupervisor

    env.fake.settle_after = 1.0
    await env.session.close()
    sessions = []

    def factory():
        s = DerivSession(lambda: _url(env.fake.url))
        s.on("proposal_open_contract", env.tracker.handle)
        sessions.append(s)
        return s

    async def hook(session):
        env.holder["session"] = session
        await env.tracker.resubscribe_all(session)

    sup = SessionSupervisor(factory, on_connected=[hook], backoff_base=0.05, backoff_cap=0.1)
    halt = asyncio.Event()
    run = asyncio.create_task(sup.run(halt))
    for _ in range(50):
        if sup.session is not None:
            break
        await asyncio.sleep(0.05)
    assert sup.session is not None
    env.deps.get_session = lambda: sup.session
    submit = asyncio.create_task(env.executor.submit(env.intent("settle-drop"), wait_settlement=True, settlement_timeout=10))
    for _ in range(100):
        if env.fake.contracts:
            break
        await asyncio.sleep(0.02)
    await asyncio.sleep(0.2)
    await env.fake.drop_all()                            # the network drops while the contract is open
    r = await asyncio.wait_for(submit, 10)
    assert r.state is State.SETTLED and r.profit == D("4.75")
    assert sup.connects >= 2 and env.q("SELECT count(*) FROM orders")[0][0] == 1
    assert len(env.fake.buy_messages) == 1
    halt.set()
    await asyncio.wait_for(run, 5)
    check_invariants(env)


async def _url(u):
    return u


async def test_an_unexpected_exception_engages_the_kill_switch_and_alerts(env, monkeypatch):
    from derivbot.db import repo

    def boom(*a, **k):
        raise RuntimeError("database write failed")

    monkeypatch.setattr(repo, "open_order", boom)
    r = await env.executor.submit(env.intent())
    assert env.kill_switch.is_engaged() and r.state is None and "error" in r.reason
    assert any(lvl == "critical" for lvl, _ in env.alerts)
    assert len(env.fake.buy_messages) == 1                # the buy was sent once; the contract exists and will be reconciled


# ---------------------------------------------------------------- owner day rules through the real executor (2026-11-06)


def _owner_day(env, cap=5, stop=3):
    cfg = env.deps.risk.cfg.model_copy(update={"max_trades_per_day": cap, "max_losses_per_day": stop, "max_trades_per_hour": 100,
                                               "recent_duplicate_window_s": 1})
    env.deps.risk = _RE(cfg, env.deps.risk.approvals)
    env.executor.d.risk = env.deps.risk


async def test_the_sixth_bet_of_the_day_is_refused_and_nothing_is_bought(env):
    _owner_day(env, cap=5, stop=99)
    kinds = [("1HZ100V", "CALL"), ("1HZ100V", "PUT"), ("R_100", "CALL"), ("R_100", "PUT"), ("1HZ100V", "DIGITEVEN")]
    for k, (sym, typ) in enumerate(kinds):
        res = await env.executor.submit(env.intent(f"d{k}", symbol=sym, contract_type=typ))
        assert res.state.value == "SETTLED", res
    sixth = await env.executor.submit(env.intent("d5", symbol="R_100", contract_type="DIGITEVEN"))
    assert sixth.state.value == "RISK_REJECTED" and env.fake.buys_received == 5
    failed = env.q("SELECT rule_results FROM risk_decisions ORDER BY id DESC LIMIT 1")[0][0]
    assert [x["name"] for x in failed if not x["passed"]] == ["trades_per_day_within_limit"]


async def test_three_losses_in_the_day_stop_trading_even_with_bets_left(env):
    _owner_day(env, cap=5, stop=3)
    with env.engine.begin() as c:                                        # three losses already settled today
        from sqlalchemy import text as _t
        for k in range(3):
            iid = f"00000000-0000-0000-0000-00000000000{k}"
            c.execute(_t("INSERT INTO order_intents (id, idempotency_key, mode, account_id, symbol, contract_type, stake, state) "
                         "VALUES (:i, :k, 'demo', 'ACC', 'S', 'CALL', 1, 'SETTLED')"), {"i": iid, "k": f"seed{k}"})
            c.execute(_t("INSERT INTO orders (id, intent_id, state, profit, settled_at, mode, account_id) "
                         "VALUES (gen_random_uuid(), :i, 'SETTLED', -1, now(), 'demo', 'ACC')"), {"i": iid})
    res = await env.executor.submit(env.intent("after-losses", symbol="R_100", contract_type="PUT"))
    assert res.state.value == "RISK_REJECTED" and env.fake.buys_received == 0
    failed = env.q("SELECT rule_results FROM risk_decisions ORDER BY id DESC LIMIT 1")[0][0]
    assert "losses_per_day_within_limit" in [x["name"] for x in failed if not x["passed"]]


async def test_the_day_counters_reset_at_the_daily_boundary(env):
    import datetime as dt
    _owner_day(env, cap=1, stop=99)
    base = dt.datetime.now(dt.UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    env.executor.d.clock = lambda: base
    assert (await env.executor.submit(env.intent("n1", symbol="R_100", contract_type="CALL"))).state.value == "SETTLED"
    assert (await env.executor.submit(env.intent("n2", symbol="R_100", contract_type="PUT"))).state.value == "RISK_REJECTED"
    env.executor.d.clock = lambda: base + dt.timedelta(days=1)         # the next UTC day: the cap is fresh
    again = await env.executor.submit(env.intent("n3", symbol="1HZ100V", contract_type="PUT"))
    assert again.state.value == "SETTLED", again


async def test_three_wins_in_a_row_stop_trading_for_the_rest_of_the_day(env):
    cfg = env.deps.risk.cfg.model_copy(update={"max_consecutive_wins_per_day": 3, "max_trades_per_hour": 100, "recent_duplicate_window_s": 1})
    env.deps.risk = _RE(cfg, env.deps.risk.approvals)
    env.executor.d.risk = env.deps.risk
    for k, (sym, typ) in enumerate([("1HZ100V", "CALL"), ("1HZ100V", "PUT"), ("R_100", "CALL")]):
        res = await env.executor.submit(env.intent(f"w{k}", symbol=sym, contract_type=typ))
        assert res.state.value == "SETTLED" and res.profit > 0, res                 # the fake exchange settles these as wins
    fourth = await env.executor.submit(env.intent("w3", symbol="R_100", contract_type="PUT"))
    assert fourth.state.value == "RISK_REJECTED" and env.fake.buys_received == 3
    failed = env.q("SELECT rule_results FROM risk_decisions ORDER BY id DESC LIMIT 1")[0][0]
    assert [x["name"] for x in failed if not x["passed"]] == ["consecutive_wins_today_within_limit"]
