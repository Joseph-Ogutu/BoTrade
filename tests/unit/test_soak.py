import asyncio
import random
from types import SimpleNamespace

import pytest

from derivbot.execution.soak import SOAK_ID, SOAK_VERSION, SoakTrader


class Recorder:
    def __init__(self):
        self.reqs = []

    async def __call__(self, req):
        self.reqs.append(req)
        return SimpleNamespace(state="SETTLED", reason="settled")


def trader(rec, kill=False, ready=True, interval=30.0, seed=1):
    n = {"i": 0}

    def key():
        n["i"] += 1
        return f"k{n['i']}"

    return SoakTrader(rec, "DEMO-ACC", lambda: kill, lambda: ready, interval_s=interval, rng=random.Random(seed), key=key)


async def test_it_submits_demo_only_calls_and_puts_for_the_soak_id_with_unique_keys():
    rec = Recorder()
    t = trader(rec)
    for _ in range(40):
        assert await t.run_once() is True
    assert len(rec.reqs) == 40 and t.submitted == 40
    assert {r.contract_type for r in rec.reqs} == {"CALL", "PUT"}                      # a coin flip, both sides appear
    assert all(r.mode == "demo" and r.account_id == "DEMO-ACC" and r.strategy_id == SOAK_ID and r.strategy_version == SOAK_VERSION
               and r.symbol == "1HZ100V" for r in rec.reqs)
    assert len({r.idempotency_key for r in rec.reqs}) == 40


async def test_it_skips_when_the_kill_switch_is_engaged_or_there_is_no_session():
    rec = Recorder()
    assert await trader(rec, kill=True).run_once() is False
    assert await trader(rec, ready=False).run_once() is False
    assert rec.reqs == []


def test_the_direction_does_not_depend_on_anything_but_the_coin():
    a = [trader(Recorder(), seed=7).next_request().contract_type for _ in range(1)]
    b = [trader(Recorder(), seed=7).next_request().contract_type for _ in range(1)]
    assert a == b                                                                       # same coin, same side: nothing else is read


def test_a_short_interval_is_refused():
    with pytest.raises(ValueError):
        trader(Recorder(), interval=5)


async def test_the_loop_waits_the_startup_delay_then_trades_each_interval_and_stops_on_halt():
    rec = Recorder()
    t = trader(rec, interval=30.0)
    t.interval_s = 0.05                                                                 # speed the test up after validation
    halt = asyncio.Event()
    task = asyncio.create_task(t.run(halt, startup_delay_s=0.1))
    await asyncio.sleep(0.05)
    assert rec.reqs == []                                                               # still in the startup delay
    await asyncio.sleep(0.35)
    assert len(rec.reqs) >= 3
    halt.set()
    await asyncio.wait_for(task, 2)


async def test_an_error_in_one_attempt_does_not_stop_the_loop():
    calls = {"n": 0}

    async def flaky(req):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return SimpleNamespace(state="SETTLED", reason="settled")

    t = SoakTrader(flaky, "A", lambda: False, lambda: True, interval_s=30.0)
    t.interval_s = 0.05
    halt = asyncio.Event()
    task = asyncio.create_task(t.run(halt, startup_delay_s=0.01))
    await asyncio.sleep(0.3)
    halt.set()
    await asyncio.wait_for(task, 2)
    assert calls["n"] >= 2


def test_the_soak_never_reads_prices_or_collected_data():
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "src" / "derivbot" / "execution" / "soak.py").read_text()
    code = re.sub(r'""".*?"""', "", src, flags=re.S)
    for banned in ("collector", "ticks", "sqlite", "TickWindow", "backtest", "gate1", "signals", "numpy", "stake"):
        assert banned not in code, banned
