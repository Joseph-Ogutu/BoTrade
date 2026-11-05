from decimal import Decimal as D

import pytest
from sqlalchemy import create_engine, text

from derivbot.execution.balance_history import BalanceHistory
from derivbot.execution.executor import ConnInfo

pytestmark = pytest.mark.postgres


def rows(engine):
    with engine.connect() as c:
        return c.execute(text("SELECT mode, account_id, balance, currency FROM balance_snapshots ORDER BY id")).all()


def test_only_a_proven_demo_balance_is_recorded(pg_url):
    engine = create_engine(pg_url)
    box = {"i": ConnInfo(None, None, None)}
    h = BalanceHistory(engine, lambda: box["i"], "DEMO-ACC")
    for info in (ConnInfo(None, None, None),                                  # nothing known yet
                 ConnInfo("demo", None, D("10"), "USD"),                      # demo not proven
                 ConnInfo("real", False, D("10"), "USD"),                     # a real endpoint: never stored
                 ConnInfo("demo", False, D("10"), "USD"),
                 ConnInfo("demo", True, None, "USD"),
                 ConnInfo("demo", True, D("10"), None)):
        box["i"] = info
        assert h.record_once() is False
    assert rows(engine) == []
    box["i"] = ConnInfo("demo", True, D("10009.88"), "USD", "DEMO-ACC")
    assert h.record_once() is True
    assert rows(engine) == [("demo", "DEMO-ACC", D("10009.88"), "USD")]
    engine.dispose()


def test_a_database_failure_is_skipped_not_raised():
    engine = create_engine("postgresql+psycopg://nobody:x@127.0.0.1:1/none")
    h = BalanceHistory(engine, lambda: ConnInfo("demo", True, D("1"), "USD"), "A")
    assert h.record_once() is False
