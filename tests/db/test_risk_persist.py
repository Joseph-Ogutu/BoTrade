import json
from decimal import Decimal as D

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from derivbot.config.risk import StrategyApprovals, parse_risk_config
from derivbot.db.config_store import store_config
from derivbot.db.repo import DuplicateIntent, StaleState, create_intent, get_state, transition
from derivbot.execution.state_machine import Event, IllegalTransition, State
from derivbot.risk.engine import RiskEngine
from derivbot.risk.persist import record_decision
from tests.unit.test_config import VALID
from tests.unit.test_risk_engine import ctx

pytestmark = pytest.mark.postgres

CFG = parse_risk_config(VALID)
ENGINE = RiskEngine(CFG, StrategyApprovals(approved={"ema_trend": "1"}))


def new_intent(conn, key):
    return create_intent(conn, idempotency_key=key, mode="demo", account_id="ACC", symbol="1HZ100V",
                         contract_type="DIGITEVEN", stake=D("1.00"))


def test_create_intent_is_unique_by_key_and_logs_the_creation(conn):
    i = new_intent(conn, "persist-k1")
    assert get_state(conn, i) is State.INTENT_CREATED
    with pytest.raises(DuplicateIntent):
        new_intent(conn, "persist-k1")
    assert conn.execute(text("SELECT count(*) FROM order_events WHERE intent_id = :i"), {"i": i}).scalar_one() == 1


def test_transition_validates_updates_and_logs_atomically(conn):
    i = new_intent(conn, "persist-k2")
    assert transition(conn, i, State.INTENT_CREATED, Event.RISK_PASS) is State.RISK_APPROVED
    with pytest.raises(IllegalTransition):
        transition(conn, i, State.RISK_APPROVED, Event.BUY_CONFIRMED)
    assert get_state(conn, i) is State.RISK_APPROVED                          # the failed attempt changed nothing
    with pytest.raises(StaleState):                                           # caller believed an old state
        transition(conn, i, State.INTENT_CREATED, Event.RISK_FAIL)
    events = conn.execute(text("SELECT from_state, to_state, event FROM order_events WHERE intent_id = :i ORDER BY id"),
                          {"i": i}).all()
    assert [tuple(e) for e in events] == [(None, "INTENT_CREATED", "CREATED"), ("INTENT_CREATED", "RISK_APPROVED", "RISK_PASS")]


def test_approved_decision_is_recorded_with_all_rules_snapshot_and_config_version(conn):
    cv = store_config(conn, "risk", CFG)
    i = new_intent(conn, "persist-k3")
    decision = ENGINE.evaluate(ctx())
    assert record_decision(conn, i, decision, cv) is State.RISK_APPROVED
    row = conn.execute(text("SELECT approved, rule_results, state_snapshot, config_version_id FROM risk_decisions "
                            "WHERE intent_id = :i"), {"i": i}).one()
    assert row.approved is True and row.config_version_id == cv
    assert len(row.rule_results) == 13 and all(r["passed"] for r in row.rule_results)
    assert row.state_snapshot["balance"] == "1000"
    audit = conn.execute(text("SELECT payload FROM audit_events WHERE ref_id = :i AND event_type = 'RISK'"), {"i": i}).scalar_one()
    assert audit["approved"] is True and audit["stake"] == "5.00"


def test_rejected_decision_moves_the_intent_to_risk_rejected_and_names_the_failures(conn):
    cv = store_config(conn, "risk", CFG)
    i = new_intent(conn, "persist-k4")
    decision = ENGINE.evaluate(ctx(kill_switch_engaged=True, open_positions=1))
    assert record_decision(conn, i, decision, cv) is State.RISK_REJECTED
    row = conn.execute(text("SELECT approved, rule_results FROM risk_decisions WHERE intent_id = :i"), {"i": i}).one()
    assert row.approved is False
    assert {r["name"] for r in row.rule_results if not r["passed"]} == {"kill_switch_clear", "open_positions_within_limit"}
    assert get_state(conn, i) is State.RISK_REJECTED


def test_a_second_decision_for_the_same_intent_is_refused(conn):
    cv = store_config(conn, "risk", CFG)
    i = new_intent(conn, "persist-k5")
    record_decision(conn, i, ENGINE.evaluate(ctx()), cv)
    conn.commit()
    with pytest.raises((IntegrityError, IllegalTransition, StaleState)):
        record_decision(conn, i, ENGINE.evaluate(ctx()), cv)
    conn.rollback()
    assert conn.execute(text("SELECT count(*) FROM risk_decisions WHERE intent_id = :i"), {"i": i}).scalar_one() == 1


def test_audit_payloads_in_decisions_hold_no_secrets(conn):
    cv = store_config(conn, "risk", CFG)
    i = new_intent(conn, "persist-k6")
    record_decision(conn, i, ENGINE.evaluate(ctx()), cv)
    blob = json.dumps([dict(r._mapping) for r in conn.execute(text("SELECT payload FROM audit_events WHERE ref_id = :i"), {"i": i})], default=str)
    assert "token" not in blob.lower() and "otp" not in blob.lower()
