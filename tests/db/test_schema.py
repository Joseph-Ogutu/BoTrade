import json
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from derivbot.db.audit import append_audit

pytestmark = pytest.mark.postgres

EXPECTED = {"ticks", "candles", "config_versions", "signals", "order_intents", "risk_decisions", "orders",
            "order_events", "audit_events", "balance_snapshots", "reconciliation_runs", "kill_switch_events",
            "backtest_runs", "backtest_trades"}


def tables(conn):
    rows = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'"))
    return {r[0] for r in rows}


def test_migration_creates_every_handbook_table(conn):
    assert EXPECTED <= tables(conn)


def test_audit_insert_works_and_redacts_secrets_before_storage(conn):
    rid = append_audit(conn, "executor", "BUY_SENT", {"url": "wss://x/ws/demo?otp=SECRETOTP12345", "token": "abc123",
                                                       "stake": "1.00"}, ref_id="i-1", req_id=7)
    conn.commit()
    payload = conn.execute(text("SELECT payload FROM audit_events WHERE id = :i"), {"i": rid}).scalar_one()
    flat = json.dumps(payload)
    assert "SECRETOTP12345" not in flat and "abc123" not in flat and payload["stake"] == "1.00"


def test_unknown_audit_event_type_is_refused_in_python(conn):
    with pytest.raises(ValueError):
        append_audit(conn, "x", "NOT_A_TYPE", {})


def _count(conn):
    return conn.execute(text("SELECT count(*) FROM audit_events")).scalar_one()


def test_audit_update_is_rejected_by_the_trigger(conn):
    rid = append_audit(conn, "t", "STATE", {"a": 1})
    conn.commit()
    with pytest.raises(DBAPIError, match="append-only"):
        conn.execute(text("UPDATE audit_events SET actor = 'evil' WHERE id = :i"), {"i": rid})
    conn.rollback()
    assert conn.execute(text("SELECT actor FROM audit_events WHERE id = :i"), {"i": rid}).scalar_one() == "t"


def test_audit_delete_is_rejected_by_the_trigger(conn):
    rid = append_audit(conn, "t", "STATE", {"a": 1})
    conn.commit()
    before = _count(conn)
    with pytest.raises(DBAPIError, match="append-only"):
        conn.execute(text("DELETE FROM audit_events WHERE id = :i"), {"i": rid})
    conn.rollback()
    assert _count(conn) == before


def test_audit_truncate_is_rejected_by_the_trigger(conn):
    append_audit(conn, "t", "STATE", {"a": 1})
    conn.commit()
    before = _count(conn)
    with pytest.raises(DBAPIError, match="append-only"):
        conn.execute(text("TRUNCATE audit_events"))
    conn.rollback()
    assert _count(conn) == before


def test_audit_update_of_every_column_and_bulk_delete_are_rejected(conn):
    append_audit(conn, "t", "STATE", {"a": 1})
    conn.commit()
    for stmt in ("UPDATE audit_events SET payload = '{}'::jsonb", "DELETE FROM audit_events"):
        with pytest.raises(DBAPIError, match="append-only"):
            conn.execute(text(stmt))
        conn.rollback()


def _intent(conn, **kw):
    row = {"id": str(uuid.uuid4()), "key": uuid.uuid4().hex, "mode": "demo", "stake": "1.00", "state": "INTENT_CREATED"}
    row.update(kw)
    conn.execute(text(
        "INSERT INTO order_intents (id, idempotency_key, mode, account_id, symbol, contract_type, stake, state) "
        "VALUES (:id, :key, :mode, 'ACC', '1HZ100V', 'DIGITEVEN', :stake, :state)"), row)
    return row


def test_real_mode_is_rejected_by_a_database_constraint(conn):
    _intent(conn)  # demo is fine
    conn.commit()
    with pytest.raises(IntegrityError, match="order_intents_demo_only"):
        _intent(conn, mode="real")
    conn.rollback()


def test_unknown_state_and_nonpositive_stake_are_rejected(conn):
    with pytest.raises(IntegrityError):
        _intent(conn, state="WHATEVER")
    conn.rollback()
    with pytest.raises(IntegrityError):
        _intent(conn, stake="0")
    conn.rollback()


def test_idempotency_key_is_unique(conn):
    row = _intent(conn, key="dup-key")
    conn.commit()
    with pytest.raises(IntegrityError):
        _intent(conn, key="dup-key")
    conn.rollback()
    assert row["key"] == "dup-key"


def test_one_risk_decision_per_intent_and_config_hash_unique(conn):
    cv = conn.execute(text("INSERT INTO config_versions (kind, content, content_hash) "
                           "VALUES ('risk', '{}'::jsonb, :h) RETURNING id"), {"h": uuid.uuid4().hex}).scalar_one()
    intent = _intent(conn)
    conn.execute(text("INSERT INTO risk_decisions (intent_id, approved, rule_results, state_snapshot, config_version_id)"
                      " VALUES (:i, true, '[]'::jsonb, '{}'::jsonb, :c)"), {"i": intent["id"], "c": cv})
    conn.commit()
    with pytest.raises(IntegrityError):
        conn.execute(text("INSERT INTO risk_decisions (intent_id, approved, rule_results, state_snapshot, "
                          "config_version_id) VALUES (:i, false, '[]'::jsonb, '{}'::jsonb, :c)"),
                     {"i": intent["id"], "c": cv})
    conn.rollback()
    with pytest.raises(IntegrityError):
        conn.execute(text("INSERT INTO config_versions (kind, content, content_hash) VALUES ('risk','{}'::jsonb,"
                          " (SELECT content_hash FROM config_versions LIMIT 1))"))
    conn.rollback()


def test_downgrade_to_base_then_upgrade_again(pg_url):
    from alembic import command
    from alembic.config import Config
    from pathlib import Path

    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.downgrade(cfg, "base")
    engine = create_engine(pg_url)
    with engine.connect() as c:
        assert not (EXPECTED & tables(c))
    command.upgrade(cfg, "head")
    with engine.connect() as c:
        assert EXPECTED <= tables(c)
    engine.dispose()
