import datetime as dt
import uuid
from decimal import Decimal as D

import pytest
from sqlalchemy import create_engine, text

from derivbot.kv.store import MemoryStore
from derivbot.notify.notifier import Notifier
from derivbot.notify.report import DailyReporter, build_daily_report, format_report
from derivbot.notify.transport import FakeTransport

pytestmark = pytest.mark.postgres

D0 = dt.datetime(2026, 11, 2, 0, 0, tzinfo=dt.timezone.utc)
D1 = D0 + dt.timedelta(days=1)


def seed(conn):
    cv = conn.execute(text("INSERT INTO config_versions (kind, content, content_hash) VALUES ('risk','{}'::jsonb,:h) RETURNING id"),
                      {"h": uuid.uuid4().hex}).scalar_one()
    def intent(state, ts):
        i = str(uuid.uuid4())
        conn.execute(text("INSERT INTO order_intents (id, idempotency_key, created_at, mode, account_id, symbol, contract_type, stake, state)"
                          " VALUES (:i, :k, :t, 'demo', 'A', 'S', 'CALL', 1, :s)"), {"i": i, "k": uuid.uuid4().hex, "t": ts, "s": state})
        return i
    for profit, hour in ((D("0.95"), 3), (D("-1"), 4), (D("-1"), 5)):
        i = intent("SETTLED", D0 + dt.timedelta(hours=hour))
        conn.execute(text("INSERT INTO orders (id, intent_id, state, profit, settled_at, mode, account_id) VALUES (:o,:i,'SETTLED',:p,:t,'demo','A')"),
                     {"o": str(uuid.uuid4()), "i": i, "p": profit, "t": D0 + dt.timedelta(hours=hour)})
    rej = intent("RISK_REJECTED", D0 + dt.timedelta(hours=6))
    conn.execute(text("INSERT INTO risk_decisions (intent_id, decided_at, approved, rule_results, state_snapshot, config_version_id)"
                      " VALUES (:i, :t, false, CAST(:r AS jsonb), '{}'::jsonb, :c)"),
                 {"i": rej, "t": D0 + dt.timedelta(hours=6), "c": cv,
                  "r": '[{"name":"market_data_fresh","passed":false},{"name":"kill_switch_clear","passed":true}]'})
    intent("OPEN", D0 + dt.timedelta(hours=7))
    intent("SETTLED", D0 - dt.timedelta(hours=2))                    # yesterday: not in this report
    conn.execute(text("INSERT INTO kill_switch_events (ts, action, source, reason) VALUES (:t,'engage','telegram','kill')"), {"t": D0 + dt.timedelta(hours=8)})
    conn.execute(text("INSERT INTO reconciliation_runs (ts, trigger, local_open, remote_open, status) VALUES (:t,'periodic',0,0,'clean')"), {"t": D0 + dt.timedelta(hours=9)})
    for h, b in ((1, "1000"), (20, "998.95")):
        conn.execute(text("INSERT INTO balance_snapshots (ts, mode, account_id, balance, currency) VALUES (:t,'demo','A',:b,'USD')"), {"t": D0 + dt.timedelta(hours=h), "b": b})


def test_report_numbers_and_text(conn):
    seed(conn)
    r = build_daily_report(conn, D0, D1)
    assert r.settled == 3 and r.wins == 1 and r.losses == 2 and r.realized_pnl == D("-1.05")
    assert r.risk_rejections == {"market_data_fresh": 1}
    assert r.reconciliations == {"clean": 1} and r.kill_switch_events == [("engage", "telegram", "kill")]
    assert r.balance_first == D("1000") and r.balance_last == D("998.95")
    assert r.intents_by_state["SETTLED"] == 3 and r.intents_by_state["OPEN"] == 1
    text_ = format_report(r)
    assert "realised P&L -1.05" in text_ and "market_data_fresh=1" in text_ and "1000 -> 998.95" in text_ and "demo" in text_


async def test_reporter_sends_once_per_day_after_the_hour_and_not_twice_after_a_restart(conn, pg_url):
    seed(conn)
    conn.commit()
    now = {"t": dt.datetime(2026, 11, 3, 0, 5, tzinfo=dt.timezone.utc)}
    transport, kv = FakeTransport(), MemoryStore()
    rep = DailyReporter(Notifier(transport, [1], min_level="warning"), create_engine(pg_url), kv, hour_utc=0, clock=lambda: now["t"])
    assert await rep.tick() is True and "Daily report 2026-11-02" in transport.sent[0][1]
    assert await rep.tick() is False                                  # already sent for this day
    restarted = DailyReporter(Notifier(transport, [1], min_level="warning"), create_engine(pg_url), kv, hour_utc=0, clock=lambda: now["t"])
    assert await restarted.tick() is False and len(transport.sent) == 1
    now["t"] = dt.datetime(2026, 11, 4, 0, 1, tzinfo=dt.timezone.utc)
    assert await rep.tick() is True and len(transport.sent) == 2


async def test_reporter_waits_for_its_hour(conn, pg_url):
    rep = DailyReporter(Notifier(FakeTransport(), [1]), create_engine(pg_url), MemoryStore(), hour_utc=6,
                        clock=lambda: dt.datetime(2026, 11, 3, 5, 59, tzinfo=dt.timezone.utc))
    assert await rep.tick() is False
