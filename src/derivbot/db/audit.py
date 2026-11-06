"""Append-only audit log writer. Payloads are redacted before they reach the database (HANDBOOK 8, 11.3)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import Connection, text

from derivbot.common.redaction import redact_obj

EVENT_TYPES = (
    "INTENT", "RISK", "RISK_PROPOSAL", "BUY_SENT", "BUY_CONFIRMED", "BUY_UNKNOWN", "SETTLED", "KILL_SWITCH",
    "RECONNECT", "RECONCILE", "STATE", "CONFIG", "MODE_GUARD", "ALERT", "FORWARD_EVALUATION",
)


def append_audit(conn: Connection, actor: str, event_type: str, payload: dict[str, Any], ref_id: str | None = None,
                 req_id: int | None = None) -> int:
    """Insert one audit row and return its id. Unknown event types are refused so the vocabulary stays closed."""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown audit event type {event_type!r}")
    clean = redact_obj(payload)
    row = conn.execute(
        text("INSERT INTO audit_events (actor, event_type, ref_id, req_id, payload) "
             "VALUES (:actor, :event_type, :ref_id, :req_id, CAST(:payload AS jsonb)) RETURNING id"),
        {"actor": actor, "event_type": event_type, "ref_id": ref_id, "req_id": req_id,
         "payload": json.dumps(clean, default=str)},
    ).one()
    return int(row[0])
