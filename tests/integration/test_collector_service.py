import asyncio
import json

from derivbot.collector.config import CollectorConfig
from derivbot.collector.service import run_service
from tests.fakes.fake_deriv import FakeDeriv


def cfg_for(url, tmp_path, **kw):
    """Build a valid config, then point it at the fake server (ws://127.0.0.1 is rightly refused by validation)."""
    base = {
        "symbols": [{"symbol": "1HZ100V", "expected_interval": 1, "always_open": True}],
        "db_path": str(tmp_path / "ticks.sqlite"),
        "heartbeat_path": str(tmp_path / "heartbeat.json"),
        "heartbeat_interval_s": 0.1,
        "flush_interval_s": 0.1,
        "stale_after_s": 5,
        "backoff_base_s": 0.01,
        "backoff_cap_s": 0.05,
        "health_max_age_s": 5,
        **kw,
    }
    return CollectorConfig.model_validate(base).model_copy(update={"url": url})


async def test_service_writes_heartbeat_and_ticks_then_stops(tmp_path, caplog):
    caplog.set_level("INFO")
    async with FakeDeriv() as fake:
        cfg = cfg_for(fake.url, tmp_path)
        halt = asyncio.Event()
        task = asyncio.create_task(run_service(cfg, halt))
        await asyncio.sleep(1.0)
        halt.set()
        code = await asyncio.wait_for(task, 5)
    assert code == 0
    hb = json.loads((tmp_path / "heartbeat.json").read_text())
    assert hb["ticks_total"] > 5 and "1HZ100V" in hb["last_tick"]
    assert any(r.getMessage().startswith("HEARTBEAT ") for r in caplog.records)


async def test_service_exits_nonzero_after_prolonged_silence(tmp_path):
    async with FakeDeriv(tick_interval=60.0) as fake:  # connected, never any ticks
        cfg = cfg_for(fake.url, tmp_path, max_silence_exit_s=0.6, stale_after_s=0.2)
        halt = asyncio.Event()
        code = await asyncio.wait_for(run_service(cfg, halt), 10)
    assert code == 1


async def test_service_keeps_running_through_a_stream_drop(tmp_path):
    async with FakeDeriv() as fake:
        fake.drop_ticks_after = 4
        cfg = cfg_for(fake.url, tmp_path)
        halt = asyncio.Event()
        task = asyncio.create_task(run_service(cfg, halt))
        await asyncio.sleep(1.2)
        halt.set()
        code = await asyncio.wait_for(task, 5)
    assert code == 0 and fake.connections >= 2
