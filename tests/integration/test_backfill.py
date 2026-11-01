from decimal import Decimal

import websockets

from derivbot.collector.backfill import backfill_symbol, coverage
from derivbot.collector.store import SqliteTickStore
from tests.fakes.fake_deriv import FakeDeriv

H0 = 1_793_486_000 - 3000  # 50 minutes of holdout inside the fake's 12 hour window


async def session(fake):
    ws = await websockets.connect(fake.url)
    return ws


async def test_backfill_pages_back_to_h0_and_stores_only_eligible_ticks(tmp_path):
    async with FakeDeriv() as fake:
        async with websockets.connect(fake.url) as ws:
            res = await backfill_symbol(ws, "1HZ100V", h0=H0, store=SqliteTickStore(tmp_path / "b.sqlite"))
    assert res.pages >= 3                                 # 3000 s at 1000 per call
    assert res.min_epoch == H0 + 1 and res.max_epoch == 1_793_486_000
    assert res.stored == 3000 and res.fetched >= res.stored
    assert res.problems == []
    s = SqliteTickStore(tmp_path / "b.sqlite")
    assert s.count_by_source("1HZ100V") == {"backfill": 3000}
    assert min(s.epochs("1HZ100V")) > H0                  # strictly after the holdout start
    s.close()


async def test_two_second_symbol(tmp_path):
    async with FakeDeriv() as fake:
        async with websockets.connect(fake.url) as ws:
            res = await backfill_symbol(ws, "R_100", h0=H0, store=SqliteTickStore(tmp_path / "b.sqlite"))
    assert res.stored == 1500


async def test_silent_fallback_to_latest_window_is_detected(tmp_path):
    """H0 is older than the server's retention: the server answers with the latest ticks, not the requested ones."""
    async with FakeDeriv() as fake:
        fake.history_retention_s = 1500                   # only 25 minutes retained; H0 is 50 minutes back
        async with websockets.connect(fake.url) as ws:
            res = await backfill_symbol(ws, "1HZ100V", h0=H0, store=SqliteTickStore(tmp_path / "b.sqlite"))
    assert any("later than requested end" in p or "overlaps" in p for p in res.problems)
    assert res.reached_h0 is False
    assert res.min_epoch is not None and res.min_epoch > H0   # coverage is reported as incomplete, never filled with wrong data


async def test_holes_are_reported_as_gaps_not_hidden(tmp_path):
    async with FakeDeriv() as fake:
        for e in range(1_793_485_500, 1_793_485_520):
            fake.history_skip.add(("1HZ100V", e))
        async with websockets.connect(fake.url) as ws:
            store = SqliteTickStore(tmp_path / "b.sqlite")
            res = await backfill_symbol(ws, "1HZ100V", h0=H0, store=store)
        cov = coverage(store.epochs("1HZ100V"), h0=H0, last_epoch=res.max_epoch, interval=1)
    assert res.stored == 3000 - 20
    assert cov.gap_count == 1 and cov.missing == 20 and cov.longest_gap_s == 21
    assert 0.99 < cov.ratio < 1.0
    store.close()


async def test_rerun_is_idempotent(tmp_path):
    async with FakeDeriv() as fake:
        store = SqliteTickStore(tmp_path / "b.sqlite")
        async with websockets.connect(fake.url) as ws:
            a = await backfill_symbol(ws, "1HZ100V", h0=H0, store=store)
            b = await backfill_symbol(ws, "1HZ100V", h0=H0, store=store)
    assert a.stored == 3000 and b.stored == 0
    store.close()


def test_quotes_are_stored_with_exact_pip_decimals(tmp_path):
    from derivbot.collector.backfill import quote_from_price
    assert quote_from_price(1310.7, 2) == Decimal("1310.70")
    assert quote_from_price(561.6, 2) == Decimal("561.60")
    assert str(quote_from_price(100.0, 3)) == "100.000"


def test_coverage_never_exceeds_one_on_an_even_lattice():
    from derivbot.collector.backfill import coverage

    # R_100 style: ticks on even epochs, holdout start on an odd epoch (the real case on 2026-10-31)
    h0 = 1_000_001
    epochs = list(range(1_000_002, 1_000_402, 2))
    cov = coverage(epochs, h0=h0, last_epoch=epochs[-1], interval=2)
    assert cov.rows == 200 and cov.expected == 200 and cov.ratio == 1.0
