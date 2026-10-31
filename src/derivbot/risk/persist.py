"""Write a risk decision (rules + state snapshot + config version) and move the intent, in ONE transaction."""

from __future__ import annotations

import json

from sqlalchemy import Connection, text

from derivbot.db.audit import append_audit
from derivbot.db.repo import transition
from derivbot.execution.state_machine import Event, State
from derivbot.risk.engine import RiskDecision


def record_decision(conn: Connection, intent_id: str, decision: RiskDecision, config_version_id: int) -> State:
    conn.execute(
        text("INSERT INTO risk_decisions (intent_id, approved, rule_results, state_snapshot, config_version_id) "
             "VALUES (:i, :a, CAST(:r AS jsonb), CAST(:s AS jsonb), :c)"),
        {"i": intent_id, "a": decision.approved, "r": json.dumps(decision.rule_results_json()),
         "s": json.dumps(decision.snapshot, default=str), "c": config_version_id},
    )
    append_audit(conn, "risk", "RISK", {"approved": decision.approved, "failed": decision.failed,
                                        "stake": str(decision.stake) if decision.stake is not None else None},
                 ref_id=intent_id)
    return transition(conn, intent_id, State.INTENT_CREATED, Event.RISK_PASS if decision.approved else Event.RISK_FAIL,
                      {"failed_rules": decision.failed})
