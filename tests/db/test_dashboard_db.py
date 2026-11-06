import datetime as dt
import json

import pytest

from derivbot.api.dashboard import build_summary
from tests.db.test_report import D0, seed

pytestmark = pytest.mark.postgres


def test_summary_matches_the_seeded_day_and_leaks_no_ids(conn):
    seed(conn)
    s = build_summary(conn, D0 + dt.timedelta(hours=12))
    assert s["today"]["settled"] == 3 and s["today"]["wins"] == 1 and s["today"]["losses"] == 2
    assert s["today"]["pnl"] == pytest.approx(-1.05) and s["today"]["win_rate"] == pytest.approx(1 / 3)
    assert s["open_exposure"] == 1
    assert s["risk_rejections_today"] == {"market_data_fresh": 1}
    assert s["last_reconciliation"]["status"] == "clean"
    assert s["recent_events"][0]["text"].startswith("engage by telegram")
    assert s["balance"]["value"] == pytest.approx(998.95) and s["balance_day_start"] == pytest.approx(1000.0)
    assert s["equity_curve"] == [1000.0, 998.95]
    assert len(s["recent_trades"]) <= 15 and all(set(t) == {"time", "symbol", "type", "stake", "state", "profit"} for t in s["recent_trades"])
    text = json.dumps(s)
    assert "'A'" not in text and "account" not in text and s["as_of"].endswith("Z")


def test_an_empty_database_gives_a_complete_empty_summary(conn):
    s = build_summary(conn, D0)
    assert s["balance"] is None and s["recent_trades"] == [] and s["today"]["settled"] == 0 and s["today"]["win_rate"] is None
    assert s["equity_curve"] == [] and s["last_reconciliation"] is None and s["recent_events"] == []


def _seed_many(conn, n):
    import uuid

    from sqlalchemy import text
    for i in range(n):
        conn.execute(text("INSERT INTO order_intents (id, idempotency_key, created_at, mode, account_id, symbol, contract_type, stake, state)"
                          " VALUES (:i, :k, :t, 'demo', 'A', 'S', 'CALL', 1, 'SETTLED')"),
                     {"i": str(uuid.uuid4()), "k": uuid.uuid4().hex, "t": D0 + dt.timedelta(minutes=i)})


def test_trades_pagination_is_complete_ordered_and_bounded(conn):
    from derivbot.api.dashboard import trades_page
    _seed_many(conn, 23)
    p1, p2, p3 = (trades_page(conn, n, 10) for n in (1, 2, 3))
    assert (p1["total"], p1["pages"], p1["page"], p1["size"]) == (23, 3, 1, 10)
    assert [len(p["items"]) for p in (p1, p2, p3)] == [10, 10, 3]
    times = [i["time"] for p in (p1, p2, p3) for i in p["items"]]
    assert times == sorted(times, reverse=True) and len(set(times)) == 23           # newest first, nothing repeated or skipped


def test_pagination_clamps_page_and_size(conn):
    from derivbot.api.dashboard import trades_page
    _seed_many(conn, 12)
    assert trades_page(conn, 99, 10)["page"] == 2 and len(trades_page(conn, 99, 10)["items"]) == 2     # beyond the end: the last page
    assert trades_page(conn, 0, 10)["page"] == 1 and trades_page(conn, -5, 10)["page"] == 1
    assert trades_page(conn, 1, 100000)["size"] == 15                                                    # an unknown size falls back
    empty = trades_page(conn, 1, 10)
    assert empty["total"] == 12


def test_pagination_of_an_empty_table(conn):
    from derivbot.api.dashboard import trades_page
    r = trades_page(conn, 1, 15)
    assert r == {"page": 1, "size": 15, "total": 0, "pages": 1, "items": []}


def test_every_number_is_for_the_current_mode_only_demo_and_real_never_mix(conn):
    import uuid

    from sqlalchemy import text

    from derivbot.api.dashboard import trades_page
    seed(conn)                                                                    # demo trades, balances and a rejection
    for mode, bal in (("real", "42.00"),):
        conn.execute(text("INSERT INTO balance_snapshots (mode, account_id, balance, currency, ts) VALUES (:m, 'R', :b, 'USD', :t)"),
                     {"m": mode, "b": bal, "t": D0 + dt.timedelta(hours=10)})
    iid = str(uuid.uuid4())
    conn.execute(text("INSERT INTO order_intents (id, idempotency_key, created_at, mode, account_id, symbol, contract_type, stake, state) "
                      "VALUES (:i, :k, :t, 'real', 'R', 'S', 'CALL', 1, 'SETTLED')"), {"i": iid, "k": uuid.uuid4().hex, "t": D0 + dt.timedelta(hours=10)})
    conn.execute(text("INSERT INTO orders (id, intent_id, state, profit, settled_at, mode, account_id) VALUES (gen_random_uuid(), :i, 'SETTLED', 9, :t, 'real', 'R')"),
                 {"i": iid, "t": D0 + dt.timedelta(hours=10)})
    now = D0 + dt.timedelta(hours=12)
    demo, real = build_summary(conn, now, "demo"), build_summary(conn, now, "real")
    assert demo["today"]["settled"] == 3 and demo["today"]["pnl"] == pytest.approx(-1.05) and demo["balance"]["value"] == pytest.approx(998.95)
    assert real["today"]["settled"] == 1 and real["today"]["pnl"] == pytest.approx(9.0) and real["balance"]["value"] == pytest.approx(42.0)
    assert real["risk_rejections_today"] == {} and real["open_exposure"] == 0 and real["equity_curve"] == [42.0]
    assert trades_page(conn, 1, 15, "real")["total"] == 1 and trades_page(conn, 1, 15, "demo")["total"] == 6
    assert trades_page(conn, 1, 15)["total"] == 6                                          # the default stays demo
