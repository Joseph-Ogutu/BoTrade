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


async def test_the_loop_retries_quickly_until_the_first_point_then_settles_to_the_interval(pg_url):
    import asyncio

    engine = create_engine(pg_url)
    box = {"i": ConnInfo(None, None, None)}
    h = BalanceHistory(engine, lambda: box["i"], "A", interval_s=30.0)
    h.retry_s = 0.05
    halt = asyncio.Event()
    task = asyncio.create_task(h.run(halt))
    base = len(rows(engine))                                         # the database is shared within this module
    await asyncio.sleep(0.2)
    assert len(rows(engine)) == base                                 # not proven yet: still nothing, but it keeps looking
    box["i"] = ConnInfo("demo", True, D("5"), "USD")
    for _ in range(40):
        if len(rows(engine)) > base:
            break
        await asyncio.sleep(0.05)
    assert len(rows(engine)) == base + 1
    await asyncio.sleep(0.3)
    assert len(rows(engine)) == base + 1                             # now on the long interval, not retrying
    halt.set()
    await asyncio.wait_for(task, 2)
    engine.dispose()


def test_real_mode_records_only_a_proven_real_account_and_demo_mode_only_a_proven_demo_one(pg_url):
    engine = create_engine(pg_url)
    real_ok = ConnInfo("real", False, D("77.70"), "USD", "ROT00000002")
    demo_ok = ConnInfo("demo", True, D("55.50"), "USD", "DOT00000001")
    before = len(rows(engine))
    cases = [("real", real_ok, True), ("real", demo_ok, False), ("real", ConnInfo("real", None, D("1"), "USD"), False),
             ("real", ConnInfo("real", True, D("1"), "USD"), False), ("demo", real_ok, False), ("demo", demo_ok, True),
             ("paper", real_ok, False)]
    for mode, info, expect in cases:
        h = BalanceHistory(engine, lambda info=info: info, "ACC", mode=mode)
        assert h.record_once() is expect, (mode, info)
    written = rows(engine)[before:]
    assert [(r[0], r[2]) for r in written] == [("real", D("77.70")), ("demo", D("55.50"))]                 # mode column follows the proof
    engine.dispose()
