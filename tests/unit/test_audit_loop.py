import asyncio
import datetime as dt
import json

from derivbot.audit.accuracy import Check
from derivbot.audit.loop import KEY, AuditLoop, last_verdict, verdict
from derivbot.kv.store import MemoryStore

NOW = dt.datetime(2026, 11, 6, 8, 0, 0, tzinfo=dt.UTC)
GOOD = [Check("a", True, 3), Check("b", True, 3)]
BAD = [Check("a", True, 3), Check("money_fields_match_exactly", False, 3, "contract 1: local 1.00 vs exchange 1.01")]


def loop(result, ready=True, interval=21600.0, delay=600.0):
    kv, alerts = MemoryStore(), []

    async def run():
        r = result() if callable(result) else result
        return r

    return AuditLoop(run, kv, lambda lvl, txt: alerts.append((lvl, txt)), lambda: ready, interval_s=interval, first_delay_s=delay,
                     clock=lambda: NOW), kv, alerts


def test_verdict_shape():
    v = verdict(BAD, 3, NOW)
    assert v == {"at": "2026-11-06T08:00:00Z", "trades": 3, "checks": 2, "passed": False,
                 "failed": [{"name": "money_fields_match_exactly", "detail": "contract 1: local 1.00 vs exchange 1.01"}]}


async def test_a_pass_is_stored_and_does_not_alert():
    lp, kv, alerts = loop((GOOD, 3))
    v = await lp.run_once()
    assert v["passed"] and alerts == [] and last_verdict(kv)["trades"] == 3


async def test_a_failure_is_stored_and_raises_a_critical_alert_naming_the_check():
    lp, kv, alerts = loop((BAD, 3))
    v = await lp.run_once()
    assert not v["passed"] and not last_verdict(kv)["passed"]
    assert len(alerts) == 1 and alerts[0][0] == "critical" and "money_fields_match_exactly" in alerts[0][1]


async def test_no_session_or_no_trades_is_pending_never_a_pass():
    lp, kv, _ = loop((GOOD, 3), ready=False)
    assert await lp.run_once() is None and last_verdict(kv) is None
    lp, kv, _ = loop(([], 0))
    assert await lp.run_once() is None and last_verdict(kv) is None


def test_last_verdict_tolerates_garbage_and_missing_values():
    kv = MemoryStore()
    assert last_verdict(kv) is None
    kv.set(KEY, "{not json")
    assert last_verdict(kv) is None
    kv.set(KEY, json.dumps({"passed": True}))
    assert last_verdict(kv) == {"passed": True}


async def test_the_loop_waits_then_audits_on_the_interval_survives_errors_and_stops():
    calls = {"n": 0}

    def result():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return GOOD, 2

    lp, kv, _ = loop(result, interval=0.05, delay=0.1)
    halt = asyncio.Event()
    task = asyncio.create_task(lp.run(halt))
    await asyncio.sleep(0.05)
    assert calls["n"] == 0                                           # still in the first delay
    await asyncio.sleep(0.4)
    assert calls["n"] >= 3 and last_verdict(kv)["passed"]            # the first error did not stop it
    halt.set()
    await asyncio.wait_for(task, 2)


def test_store_verdict_replaces_the_previous_verdict_so_a_stale_failure_can_be_cleared():
    from derivbot.audit.loop import store_verdict
    kv = MemoryStore()
    store_verdict(kv, BAD, 11, NOW)
    assert last_verdict(kv)["passed"] is False
    store_verdict(kv, GOOD, 12, NOW)
    v = last_verdict(kv)
    assert v["passed"] is True and v["trades"] == 12 and v["failed"] == []
