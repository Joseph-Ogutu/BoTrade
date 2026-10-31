"""Intent and order persistence. Every state change goes through the state machine and is written to order_events in
the same transaction as the state update, using an optimistic check so two writers cannot both move one intent."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, text

from derivbot.common.redaction import redact_obj
from derivbot.execution.state_machine import Event, State, next_state


class DuplicateIntent(Exception):
    pass


class StaleState(Exception):
    """The stored state is not the one the caller believed: someone else moved the intent."""


def create_intent(conn: Connection, *, idempotency_key: str, mode: str, account_id: str, symbol: str,
                  contract_type: str, stake: Decimal, duration: int | None = None, duration_unit: str | None = None,
                  signal_id: str | None = None) -> str:
    intent_id = str(uuid.uuid4())
    row = conn.execute(
        text("INSERT INTO order_intents (id, idempotency_key, signal_id, mode, account_id, symbol, contract_type, stake,"
             " duration, duration_unit, state) VALUES (:id, :k, :sig, :mode, :acc, :sym, :ct, :stake, :dur, :unit,"
             " 'INTENT_CREATED') ON CONFLICT (idempotency_key) DO NOTHING RETURNING id"),
        {"id": intent_id, "k": idempotency_key, "sig": signal_id, "mode": mode, "acc": account_id, "sym": symbol,
         "ct": contract_type, "stake": stake, "dur": duration, "unit": duration_unit},
    ).first()
    if row is None:
        raise DuplicateIntent(idempotency_key)
    conn.execute(text("INSERT INTO order_events (intent_id, from_state, to_state, event, payload) "
                      "VALUES (:i, NULL, 'INTENT_CREATED', 'CREATED', '{}'::jsonb)"), {"i": intent_id})
    return intent_id


def get_state(conn: Connection, intent_id: str) -> State:
    return State(conn.execute(text("SELECT state FROM order_intents WHERE id = :i"), {"i": intent_id}).scalar_one())


def transition(conn: Connection, intent_id: str, expected: State, event: Event,
               payload: dict[str, Any] | None = None) -> State:
    """Apply `event` to the stored state. Raises IllegalTransition for a bad event and StaleState on a lost race."""
    new = next_state(expected, event)
    updated = conn.execute(
        text("UPDATE order_intents SET state = :new WHERE id = :i AND state = :old"),
        {"new": new.value, "old": expected.value, "i": intent_id},
    ).rowcount
    if updated != 1:
        raise StaleState(f"intent {intent_id} is not in state {expected.value}")
    conn.execute(
        text("INSERT INTO order_events (intent_id, from_state, to_state, event, payload) "
             "VALUES (:i, :f, :t, :e, CAST(:p AS jsonb))"),
        {"i": intent_id, "f": expected.value, "t": new.value, "e": event.value,
         "p": json.dumps(redact_obj(payload or {}), default=str)},
    )
    conn.execute(text("UPDATE orders SET state = :new WHERE intent_id = :i"), {"new": new.value, "i": intent_id})
    return new
