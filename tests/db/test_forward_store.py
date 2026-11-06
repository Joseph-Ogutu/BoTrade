from decimal import Decimal as D

import pytest
from sqlalchemy import create_engine, text

from derivbot.forward.shadow import Outcome
from derivbot.forward.store import ALERT_QUEUE, LedgerStore, counts

pytestmark = pytest.mark.postgres


def outcome(i, variant="ema_trend:1HZ100V", won=True):
    return Outcome(variant, "ema_trend", "1", "1HZ100V", "CALL", 1000 + i * 10, 1001 + i * 10, 1006 + i * 10, D("100.10"), D("100.20"), won)


def test_rows_are_written_in_batches_and_counted(pg_url):
    engine = create_engine(pg_url)
    st = LedgerStore(engine)
    for i in range(5):
        st.push_outcome(outcome(i))
    st.push_discard("ema_trend:1HZ100V", 99, "gap")
    assert st.pending() == 6 and st.flush() == 6 and st.pending() == 0
    with engine.connect() as c:
        assert counts(c) == {"ema_trend:1HZ100V": {"completed": 5, "discarded": 1}}
        assert c.execute(text("SELECT entry_quote, exit_quote FROM shadow_outcomes LIMIT 1")).one() == (D("100.10"), D("100.20"))
    engine.dispose()


def test_a_retry_after_a_failed_write_loses_nothing_and_duplicates_nothing(pg_url):
    bad = create_engine("postgresql+psycopg://nobody:x@127.0.0.1:1/none")
    st = LedgerStore(bad)
    for i in range(3):
        st.push_outcome(outcome(i, variant="retry:1HZ100V"))
    assert st.flush() == 0 and st.pending() == 3                          # the database is unreachable: rows stay queued
    st.engine = create_engine(pg_url)                                      # the database comes back
    assert st.flush() == 3
    st.push_outcome(outcome(0, variant="retry:1HZ100V"))                   # the same (variant, signal epoch) again
    st.flush()
    with st.engine.connect() as c:
        assert counts(c)["retry:1HZ100V"]["completed"] == 3                # ON CONFLICT DO NOTHING: no duplicate
    st.engine.dispose()
    bad.dispose()


def test_a_long_outage_raises_one_critical_alert_not_one_per_attempt():
    alerts = []
    st = LedgerStore(create_engine("postgresql+psycopg://nobody:x@127.0.0.1:1/none"), lambda lvl, txt: alerts.append((lvl, txt)))
    for i in range(ALERT_QUEUE + 5):
        st.push_outcome(outcome(i, variant="down:1HZ100V"))
    for _ in range(3):
        st.flush()
    assert len(alerts) == 1 and alerts[0][0] == "critical"
