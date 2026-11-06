import random

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from derivbot.forward.report import render, run_report
from derivbot.gate1.registration import load_registration

pytestmark = pytest.mark.postgres

FT = {**load_registration()["forward_test"], "n_eval": 400}


def seed(conn, variant, rate, n, seed_=1, start=1000):
    rng = random.Random(seed_)
    for i in range(n):
        conn.execute(text("INSERT INTO shadow_outcomes (variant, strategy_id, strategy_version, symbol, side, signal_epoch, entry_epoch, exit_epoch, "
                          "entry_quote, exit_quote, won) VALUES (:v, 's', '1', 'S', 'CALL', :a, :b, :c, 1, 2, :w)"),
                     {"v": variant, "a": start + i * 10, "b": start + i * 10 + 1, "c": start + i * 10 + 6, "w": rng.random() < rate})


def test_the_default_report_shows_counts_only_even_when_far_more_data_exists_than_required(conn):
    seed(conn, "ema_trend:1HZ100V", 0.9, 900)
    rep = run_report(conn, FT)
    assert rep["evaluations"] == []
    row = next(v for v in rep["variants"] if v["variant"] == "ema_trend:1HZ100V")
    assert row["completed"] == 900 and row["ready"] is True and set(row) == {"variant", "completed", "discarded", "ready", "evaluated"}
    text_out = render(rep)
    assert "wins" not in text_out and "p=" not in text_out and "READY" in text_out          # nothing about outcomes leaks


def test_a_variant_below_the_threshold_is_never_evaluated_even_on_request(conn):
    seed(conn, "ema_trend:1HZ100V", 0.9, 399)
    rep = run_report(conn, FT, evaluate=True)
    assert rep["evaluations"] == []
    assert conn.execute(text("SELECT count(*) FROM audit_events WHERE event_type = 'FORWARD_EVALUATION'")).scalar() == 0


def test_a_variant_is_evaluated_exactly_once_on_its_first_n_eval_contracts_and_recorded(conn):
    seed(conn, "ema_trend:1HZ100V", 0.55, 400)
    seed(conn, "ema_trend:1HZ100V", 0.99, 200, seed_=9, start=900_000)        # later contracts must not change the evaluation
    first = run_report(conn, FT, evaluate=True)
    ev = [e for e in first["evaluations"] if e["variant"] == "ema_trend:1HZ100V"]
    assert len(ev) == 1 and ev[0]["n"] == 400 and ev[0]["win_rate"] < 0.7
    again = run_report(conn, FT, evaluate=True)
    assert [e for e in again["evaluations"] if e["variant"] == "ema_trend:1HZ100V"] == []         # refused the second time
    assert conn.execute(text("SELECT count(*) FROM audit_events WHERE event_type = 'FORWARD_EVALUATION' AND ref_id = 'ema_trend:1HZ100V'")).scalar() == 1
    assert next(v for v in again["variants"] if v["variant"] == "ema_trend:1HZ100V")["evaluated"] is True


def test_a_control_that_rejects_voids_the_whole_evaluation(conn):
    seed(conn, "control_random:1HZ100V", 0.75, 400)
    seed(conn, "ema_trend:1HZ100V", 0.5, 400, seed_=3)
    rep = run_report(conn, FT, evaluate=True)
    assert rep["void"] is True and "VOID" in render(rep)


def test_a_fair_control_does_not_void_anything(conn):
    seed(conn, "control_random:1HZ100V", 0.5, 400)
    assert run_report(conn, FT, evaluate=True)["void"] is False


def _insert(conn, **kw):
    row = dict(v="v", sid="s", ver="1", sym="S", side="CALL", a=10, b=11, c=16, eq=1, xq=2, w=True)
    row.update(kw)
    conn.execute(text("INSERT INTO shadow_outcomes (variant, strategy_id, strategy_version, symbol, side, signal_epoch, entry_epoch, exit_epoch, "
                      "entry_quote, exit_quote, won) VALUES (:v, :sid, :ver, :sym, :side, :a, :b, :c, :eq, :xq, :w)"), row)


def test_the_ledger_is_append_only_for_outcomes_and_discards(conn):
    seed(conn, "v", 0.5, 3)
    conn.execute(text("INSERT INTO shadow_discards (variant, signal_epoch, reason) VALUES ('v', 1, 'gap')"))
    for sql in ("UPDATE shadow_outcomes SET won = NOT won", "DELETE FROM shadow_outcomes", "TRUNCATE shadow_outcomes",
                "UPDATE shadow_discards SET reason = 'restart'", "DELETE FROM shadow_discards", "TRUNCATE shadow_discards"):
        with pytest.raises(DBAPIError, match="append-only"), conn.begin_nested():
            conn.execute(text(sql))
    assert conn.execute(text("SELECT count(*) FROM shadow_outcomes")).scalar() == 3


def test_the_ledger_enforces_its_own_shape(conn):
    _insert(conn)
    for bad in ({"a": 10}, {"side": "DIGITEVEN"}, {"b": 10}, {"c": 11}, {"a": 20, "b": 15}):
        with pytest.raises(DBAPIError), conn.begin_nested():                 # duplicate (variant, signal_epoch), wrong side, bad ordering
            _insert(conn, **bad)
    with pytest.raises(DBAPIError), conn.begin_nested():
        conn.execute(text("INSERT INTO shadow_discards (variant, signal_epoch, reason) VALUES ('v', 1, 'because')"))
