import asyncio

import pytest

from derivbot.common import redaction
from derivbot.killswitch.switch import KillSwitch, MemoryEventLog
from derivbot.killswitch.telegram_commands import TelegramCommands
from derivbot.kv.store import HeartbeatKV, MemoryStore
from derivbot.notify.notifier import Heartbeater, Notifier
from derivbot.notify.telegram import TelegramPoller
from derivbot.notify.transport import FakeTransport

CHAT = 1111


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


@pytest.fixture(autouse=True)
def _clean():
    redaction.reset_secrets()
    yield
    redaction.reset_secrets()


async def test_alert_is_redacted_prefixed_and_sent_to_every_chat():
    redaction.register_secret("FAKE-ADMIN-TOKEN-ABCDEF")
    t = FakeTransport()
    n = Notifier(t, [1, 2], min_level="info")
    ok = await n.alert("critical", "failed with FAKE-ADMIN-TOKEN-ABCDEF at wss://x/ws/demo?otp=SECRETOTP123456")
    assert ok and [c for c, _ in t.sent] == [1, 2]
    body = t.sent[0][1]
    assert body.startswith("[CRITICAL]") and "FAKE-ADMIN-TOKEN" not in body and "SECRETOTP123456" not in body


async def test_min_level_filters_and_unknown_level_is_refused():
    t = FakeTransport()
    n = Notifier(t, [1], min_level="warning")
    assert await n.alert("info", "noise") is False and t.sent == []
    assert await n.alert("warning", "real") is True
    with pytest.raises(ValueError):
        await n.alert("loud", "x")
    with pytest.raises(ValueError):
        Notifier(t, [1], min_level="loud")


async def test_duplicates_inside_the_window_are_suppressed_and_counted_then_allowed_again():
    clock, t = Clock(), FakeTransport()
    n = Notifier(t, [1], min_level="info", dedupe_window_s=60, clock=clock)
    await n.alert("warning", "same")
    await n.alert("warning", "same")
    assert len(t.sent) == 1 and n.status()["suppressed"] == 1
    clock.t += 61
    await n.alert("warning", "same")
    assert len(t.sent) == 2
    await n.alert("critical", "same")                     # a different level is a different alert
    assert len(t.sent) == 3


async def test_a_failing_transport_is_counted_and_never_raises():
    t = FakeTransport()
    t.fail_next = 1
    n = Notifier(t, [1, 2], min_level="info")
    assert await n.alert("warning", "x") is False
    assert n.status()["failed"] == 1 and n.status()["sent"] == 1 and "fake transport failure" in n.status()["last_error"]


async def test_long_messages_are_bounded():
    t = FakeTransport()
    await Notifier(t, [1], min_level="info").alert("warning", "z" * 10_000)
    assert len(t.sent[0][1]) <= 3500


async def test_synchronous_code_can_submit_without_blocking_and_the_queue_is_drained_on_shutdown():
    t = FakeTransport()
    n = Notifier(t, [1], min_level="info")
    halt = asyncio.Event()
    task = asyncio.create_task(n.run(halt))
    n.submit("critical", "from sync code")
    await asyncio.sleep(0.4)
    assert any("from sync code" in m for _, m in t.sent)
    n.submit("critical", "last words")
    halt.set()
    await asyncio.wait_for(task, 3)
    assert any("last words" in m for _, m in t.sent)


async def test_broadcast_ignores_level_and_dedupe_but_still_redacts():
    redaction.register_secret("FAKE-PAT-ABCDEFGH")
    t = FakeTransport()
    n = Notifier(t, [1], min_level="critical")
    await n.broadcast("report FAKE-PAT-ABCDEFGH")
    await n.broadcast("report FAKE-PAT-ABCDEFGH")
    assert len(t.sent) == 2 and "FAKE-PAT" not in t.sent[0][1]


async def test_heartbeat_beats_kv_every_time_and_sends_the_message_only_on_its_slow_schedule():
    clock, t, kv = Clock(), FakeTransport(), MemoryStore()
    hb = Heartbeater(HeartbeatKV(kv, ttl_s=30), "executor", Notifier(t, [1], min_level="warning"),
                     status_provider=lambda: {"mode": "demo"}, message_every_s=3600, clock=clock)
    await hb.beat_once()
    await hb.beat_once()
    assert len(t.sent) == 1 and "heartbeat executor: alive" in t.sent[0][1] and "mode=demo" in t.sent[0][1]
    assert hb.hb.age("executor") is not None
    clock.t += 3601
    await hb.beat_once()
    assert len(t.sent) == 2


async def test_dead_mans_switch_failures_are_counted_and_do_not_stop_the_heartbeat():
    calls = []

    async def ping():
        calls.append(1)
        if len(calls) == 2:
            raise ConnectionError("ping target down")

    hb = Heartbeater(HeartbeatKV(MemoryStore()), "executor", None, dead_mans_switch=ping)
    for _ in range(3):
        await hb.beat_once()
    assert len(calls) == 3 and hb.dms_failures == 1


async def test_heartbeat_loop_runs_and_stops():
    hb = Heartbeater(HeartbeatKV(MemoryStore()), "executor", None, interval_s=0.05)
    halt = asyncio.Event()
    task = asyncio.create_task(hb.run(halt))
    await asyncio.sleep(0.25)
    halt.set()
    await asyncio.wait_for(task, 2)
    assert hb.hb.age("executor") is not None


async def test_telegram_poller_replies_to_allowed_chats_only_and_advances_its_offset(tmp_path):
    t = FakeTransport()
    ks = KillSwitch(MemoryStore(), MemoryEventLog(), tmp_path / "KILL")
    cmds = TelegramCommands(ks, {CHAT}, lambda: {"mode": "demo"})
    poller = TelegramPoller(t, cmds)
    t.push_message(10, CHAT, "/status")
    t.push_message(11, 9999, "/kill")                     # not on the allowlist
    t.push_message(12, CHAT, "/kill")
    await poller.poll_once()
    assert [c for c, _ in t.sent] == [CHAT, CHAT] and ks.is_engaged()
    assert "Kill switch" in t.sent[0][1]
    assert ks.log.events == [("engage", "telegram", "kill")]
    t.push_message(13, CHAT, "/release")
    await poller.poll_once()
    assert t.offsets[-1] == 13 and "Unknown command" in t.sent[-1][1] and ks.is_engaged()


async def test_poller_ignores_malformed_updates_and_survives_transport_errors():
    t = FakeTransport()
    cmds = TelegramCommands(KillSwitch(MemoryStore(), MemoryEventLog(), __import__("pathlib").Path("/tmp/none-ks")), {CHAT}, lambda: {})
    poller = TelegramPoller(t, cmds, error_backoff_s=0.05)
    t.incoming = [{"update_id": 1}, {"update_id": 2, "message": {"chat": {"id": "x"}, "text": 5}}]
    assert await poller.poll_once() == 0

    class Boom(FakeTransport):
        async def get_updates(self, offset, timeout_s):
            from derivbot.notify.transport import TransportError
            raise TransportError("network down")

    p2 = TelegramPoller(Boom(), cmds, error_backoff_s=0.05)
    halt = asyncio.Event()
    task = asyncio.create_task(p2.run(halt))
    await asyncio.sleep(0.15)
    halt.set()
    await asyncio.wait_for(task, 2)


async def test_alerts_are_logged_even_when_no_chat_is_configured(caplog):
    caplog.set_level("INFO")
    n = Notifier(FakeTransport(), [], min_level="info")
    await n.alert("critical", "something broke wss://x/ws/demo?otp=SECRETOTP123456")
    assert any("ALERT [CRITICAL] something broke" in r.getMessage() for r in caplog.records)
    assert "SECRETOTP123456" not in caplog.text
