import asyncio
import logging

from derivbot.collector.collector import run_collector
from derivbot.collector.store import SqliteTickStore
from derivbot.common.logs import quiet_transport_loggers
from derivbot.common.redaction import RedactingFilter
from derivbot.deriv.public_stream import StreamStats, stream_ticks
from tests.fakes.fake_deriv import FakeDeriv

FAST = dict(backoff_base=0.01, backoff_cap=0.05)


async def take(url, symbols, n, stats=None, timeout=5.0, **kw):
    halt = asyncio.Event()
    got = []

    async def run():
        async for t in stream_ticks(url, symbols, halt, stats=stats, **FAST, **kw):
            got.append(t)
            if len(got) >= n:
                halt.set()
                return

    await asyncio.wait_for(run(), timeout)
    return got


async def test_yields_parsed_ticks_for_each_symbol():
    async with FakeDeriv() as fake:
        got = await take(fake.url, ["1HZ100V", "R_100"], 6)
        assert {t.symbol for t in got} == {"1HZ100V", "R_100"}
        assert all(t.pip_size == 2 for t in got)


async def test_reconnects_and_resubscribes_after_stream_drop():
    async with FakeDeriv() as fake:
        fake.drop_ticks_after = 3
        stats = StreamStats()
        got = await take(fake.url, ["1HZ100V"], 10, stats=stats)
        assert len(got) == 10
        assert fake.connections >= 2
        assert stats.reconnects >= 1


async def test_retries_after_handshake_rejection():
    async with FakeDeriv() as fake:
        # UNVERIFIED-ERR: handshake rejection as HTTP 401 is an assumption (docs describe 401 for REST).
        fake.reject_handshake_status = 401
        stats = StreamStats()

        async def unblock():
            await asyncio.sleep(0.15)
            fake.reject_handshake_status = None

        task = asyncio.create_task(unblock())
        got = await take(fake.url, ["1HZ100V"], 3, stats=stats)
        await task
        assert len(got) == 3 and stats.errors >= 1


async def test_silent_stall_triggers_reconnect():
    async with FakeDeriv(tick_interval=30.0) as fake:  # connected but never sends a tick
        halt = asyncio.Event()
        stats = StreamStats()

        async def run():
            async for _ in stream_ticks(fake.url, ["1HZ100V"], halt, stats=stats, stale_after=0.2, **FAST):
                pass

        task = asyncio.create_task(run())
        await asyncio.sleep(1.0)
        halt.set()
        await asyncio.wait_for(task, 3)
        assert fake.connections >= 2 and stats.stalls >= 1


async def test_halt_stops_stream_promptly():
    async with FakeDeriv(tick_interval=30.0) as fake:
        halt = asyncio.Event()

        async def run():
            async for _ in stream_ticks(fake.url, ["1HZ100V"], halt, **FAST):
                pass

        task = asyncio.create_task(run())
        await asyncio.sleep(0.2)
        halt.set()
        await asyncio.wait_for(task, 3)


async def test_url_query_secret_never_reaches_logs(caplog):
    # Root at DEBUG on purpose: the websockets library logs the request URL (with ?otp=)
    # at DEBUG, so the process must cap transport loggers and redact handlers.
    caplog.set_level(logging.DEBUG)
    quiet_transport_loggers()
    caplog.handler.addFilter(RedactingFilter())
    async with FakeDeriv() as fake:
        fake.reject_handshake_status = 401  # force an error log that mentions the URL
        url = fake.url + "?otp=FAKESECRETOTP123456"

        async def unblock():
            await asyncio.sleep(0.1)
            fake.reject_handshake_status = None

        task = asyncio.create_task(unblock())
        await take(url, ["1HZ100V"], 2)
        await task
    assert "FAKESECRETOTP123456" not in caplog.text
    assert "FAKESECRETOTP123456" not in " ".join(str(r.args) for r in caplog.records)


async def test_collector_persists_and_dedupes_and_flushes_on_halt(tmp_path):
    async with FakeDeriv() as fake:
        store = SqliteTickStore(tmp_path / "t.sqlite")
        halt = asyncio.Event()
        stats = await asyncio.wait_for(
            run_collector(fake.url, ["1HZ100V"], store, halt, max_ticks=25, flush_every=10,
                          flush_interval=0.5, **FAST),
            10,
        )
        assert stats.ticks_received >= 25
        assert store.count("1HZ100V") == stats.ticks_stored >= 25
        epochs = store.epochs("1HZ100V")
        assert epochs == sorted(set(epochs))
        store.close()


async def test_collector_survives_drop_and_keeps_data(tmp_path):
    async with FakeDeriv() as fake:
        fake.drop_ticks_after = 5
        store = SqliteTickStore(tmp_path / "t.sqlite")
        halt = asyncio.Event()
        stats = await asyncio.wait_for(
            run_collector(fake.url, ["1HZ100V"], store, halt, max_ticks=20, flush_every=5,
                          flush_interval=0.2, **FAST),
            10,
        )
        assert stats.stream.reconnects >= 1
        assert store.count("1HZ100V") >= 20
        store.close()


async def test_closed_market_symbol_does_not_cause_reconnects_and_resubscribes_later():
    async with FakeDeriv() as fake:
        fake.closed_symbols = {"frxEURUSD"}
        stats = StreamStats()
        halt = asyncio.Event()
        got = []

        async def run():
            async for t in stream_ticks(fake.url, ["1HZ100V", "frxEURUSD"], halt, stats=stats,
                                        resubscribe_closed_after=0.3, **FAST):
                got.append(t.symbol)

        task = asyncio.create_task(run())
        await asyncio.sleep(0.5)
        assert fake.connections == 1, "a closed market must not cost a reconnect"
        assert "1HZ100V" in got and "frxEURUSD" not in got
        assert "frxEURUSD" in stats.closed_symbols
        fake.closed_symbols = set()  # market opens
        await asyncio.sleep(0.8)
        halt.set()
        await asyncio.wait_for(task, 3)
        assert "frxEURUSD" in got
        assert fake.connections == 1
        assert "frxEURUSD" not in stats.closed_symbols


async def test_other_subscription_errors_still_reconnect():
    async with FakeDeriv() as fake:
        from tests.fakes.fake_deriv import Fault

        # UNVERIFIED-ERR: "SomethingElse" is a placeholder for any unrecognised subscription error code.
        fake.inject(Fault("ticks", "error", code="SomethingElse"))
        stats = StreamStats()
        got = await take(fake.url, ["1HZ100V"], 3, stats=stats)
        assert len(got) == 3 and stats.reconnects >= 1


async def test_streamed_quotes_are_stored_with_exactly_pip_size_decimals(tmp_path):
    """The fake sends JSON numbers like 100.1 for pip_size 2; the stored text must read 100.10 (digit 0, not 1)."""
    async with FakeDeriv() as fake:
        store = SqliteTickStore(tmp_path / "t.sqlite")
        await asyncio.wait_for(
            run_collector(fake.url, ["1HZ100V"], store, asyncio.Event(), max_ticks=60, flush_every=20,
                          flush_interval=0.2, **FAST), 10)
        quotes = [r[0] for r in store._conn.execute("SELECT quote FROM ticks")]
        pips = {r[0] for r in store._conn.execute("SELECT pip_size FROM ticks")}
        store.close()
    assert quotes and pips == {2}
    assert all(len(q.split(".")[1]) == 2 for q in quotes)
    assert any(q.endswith("0") for q in quotes)  # the case the bug got wrong
