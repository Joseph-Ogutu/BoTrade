import datetime as dt
import uuid
from decimal import Decimal as D

import pytest
from sqlalchemy import text

from derivbot.risk import state

pytestmark = pytest.mark.postgres

NOW = dt.datetime(2026, 11, 2, 12, 0, tzinfo=dt.UTC)


def intent(conn, st="INTENT_CREATED", created=NOW, symbol="1HZ100V", ct="DIGITEVEN", acc="ACC", key=None):
    i = str(uuid.uuid4())
    conn.execute(text("INSERT INTO order_intents (id, idempotency_key, created_at, mode, account_id, symbol, contract_type,"
                      " stake, state) VALUES (:i, :k, :c, 'demo', :a, :s, :ct, 1, :st)"),
                 {"i": i, "k": key or i, "c": created, "a": acc, "s": symbol, "ct": ct, "st": st})
    return i


def order(conn, st, profit, settled):
    i = intent(conn, st=st)
    conn.execute(text("INSERT INTO orders (id, intent_id, state, profit, settled_at, mode, account_id) "
                      "VALUES (:o, :i, :st, :p, :t, 'demo', 'ACC')"),
                 {"o": str(uuid.uuid4()), "i": i, "st": st, "p": profit, "t": settled})
    return i


def test_day_start_boundaries():
    assert state.day_start(NOW, 0) == dt.datetime(2026, 11, 2, 0, 0, tzinfo=dt.UTC)
    assert state.day_start(NOW, 13) == dt.datetime(2026, 11, 1, 13, 0, tzinfo=dt.UTC)   # reset not yet reached today
    assert state.day_start(NOW, 12) == NOW
    eat = dt.timezone(dt.timedelta(hours=3))
    assert state.day_start(NOW.astimezone(eat), 0) == dt.datetime(2026, 11, 2, 0, 0, tzinfo=dt.UTC)


def test_open_positions_counts_every_state_that_may_hold_a_contract(conn):
    base = state.open_positions(conn)
    for st in ("BUY_SENT", "BUY_UNKNOWN", "OPEN", "MANUAL_REVIEW"):
        intent(conn, st)
    for st in ("INTENT_CREATED", "RISK_REJECTED", "RISK_APPROVED", "PROPOSAL_REQUESTED", "PROPOSAL_REJECTED",
               "NOT_PLACED", "SETTLED", "SOLD"):
        intent(conn, st)
    assert state.open_positions(conn) == base + 4


def test_realized_pnl_counts_only_settled_orders_since_the_boundary(conn):
    since = NOW - dt.timedelta(hours=3)
    before = state.realized_pnl_since(conn, since)
    order(conn, "SETTLED", D("-2.00"), NOW - dt.timedelta(hours=1))
    order(conn, "SOLD", D("0.95"), NOW - dt.timedelta(minutes=5))
    order(conn, "SETTLED", D("-50"), NOW - dt.timedelta(hours=5))          # before the boundary: ignored
    order(conn, "OPEN", D("-9"), NOW)                                      # not realised: ignored
    assert state.realized_pnl_since(conn, since) == before + D("-1.05")


def test_consecutive_losses_stop_at_the_first_non_loss(conn):
    conn.execute(text("DELETE FROM orders"))
    for k, p in enumerate([D("0.95"), D("-1"), D("-1"), D("0.00"), D("-1"), D("-1"), D("-1")]):   # oldest first
        order(conn, "SETTLED", p, NOW - dt.timedelta(minutes=60 - k))
    assert state.consecutive_losses(conn) == 3
    order(conn, "SETTLED", D("1.90"), NOW)                                  # a win resets the streak
    assert state.consecutive_losses(conn) == 0


def test_trades_last_hour_counts_buys_sent_not_intents(conn):
    i = intent(conn)
    for ago, to in ((10, "BUY_SENT"), (30, "BUY_SENT"), (90, "BUY_SENT"), (5, "RISK_APPROVED")):
        conn.execute(text("INSERT INTO order_events (ts, intent_id, from_state, to_state, event) VALUES (:t, :i, 'X', :to, 'E')"),
                     {"t": NOW - dt.timedelta(minutes=ago), "i": i, "to": to})
    assert state.trades_in_last_hour(conn, NOW) == 2


def test_identical_recent_or_in_flight(conn):
    me = intent(conn, symbol="DUP", acc="A1")
    assert state.identical_recent_or_in_flight(conn, me, "DUP", "DIGITEVEN", "A1", 30, NOW) is False
    intent(conn, "OPEN", symbol="DUP", acc="A1", created=NOW - dt.timedelta(hours=9))          # old but still exposed
    assert state.identical_recent_or_in_flight(conn, me, "DUP", "DIGITEVEN", "A1", 30, NOW) is True


def test_identical_window_rules(conn):
    me = intent(conn, symbol="WIN", acc="A2")
    intent(conn, "SETTLED", symbol="WIN", acc="A2", created=NOW - dt.timedelta(seconds=10))     # recent, progressed
    assert state.identical_recent_or_in_flight(conn, me, "WIN", "DIGITEVEN", "A2", 30, NOW) is True
    assert state.identical_recent_or_in_flight(conn, me, "WIN", "DIGITEVEN", "A2", 5, NOW) is False   # outside the window
    intent(conn, "RISK_REJECTED", symbol="REJ", acc="A3", created=NOW)                           # a rejected one does not block
    me2 = intent(conn, symbol="REJ", acc="A3")
    assert state.identical_recent_or_in_flight(conn, me2, "REJ", "DIGITEVEN", "A3", 30, NOW) is False
    assert state.identical_recent_or_in_flight(conn, me, "WIN", "DIGITEVEN", "OTHER", 30, NOW) is False     # other account
    assert state.identical_recent_or_in_flight(conn, me, "WIN", "DIGITODD", "A2", 30, NOW) is False         # other contract


def test_balances(conn):
    boundary = NOW - dt.timedelta(hours=6)
    assert state.start_of_day_balance(conn, boundary) is None
    assert state.latest_balance(conn, "ACC-B") is None
    for ts, bal in ((boundary + dt.timedelta(hours=1), "1010"), (boundary - dt.timedelta(hours=1), "1000"),
                    (boundary - dt.timedelta(hours=9), "900")):
        conn.execute(text("INSERT INTO balance_snapshots (ts, mode, account_id, balance, currency) VALUES (:t, 'demo', 'ACC-B', :b, 'USD')"),
                     {"t": ts, "b": bal})
    assert state.start_of_day_balance(conn, boundary) == D("1000")          # last one before the boundary
    assert state.latest_balance(conn, "ACC-B") == D("1010")
    only_after = NOW - dt.timedelta(days=30)
    assert state.start_of_day_balance(conn, only_after) == D("900")         # nothing before: first one after
