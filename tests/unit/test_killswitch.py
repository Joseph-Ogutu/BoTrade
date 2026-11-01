import subprocess
import sys

import pytest

from derivbot.killswitch.switch import KillSwitch, MemoryEventLog
from derivbot.kv.store import MemoryStore

ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


class BrokenKV(MemoryStore):
    """A Redis that is down: every call raises."""

    def __init__(self):
        super().__init__()
        self.down = True

    def _boom(self, *a, **k):
        if self.down:
            raise ConnectionError("redis down")

    def get(self, key):
        self._boom()
        return super().get(key)

    def set(self, key, value, ttl_s=None):
        self._boom()
        super().set(key, value, ttl_s)


class BrokenLog(MemoryEventLog):
    def __init__(self):
        super().__init__()
        self.down = True

    def append(self, action, source, reason):
        if self.down:
            raise ConnectionError("postgres down")
        super().append(action, source, reason)

    def last_action(self):
        if self.down:
            raise ConnectionError("postgres down")
        return super().last_action()


def make(tmp_path, kv=None, log=None):
    return KillSwitch(kv or MemoryStore(), log or MemoryEventLog(), tmp_path / "KILL_SWITCH")


def test_starts_clear_and_engage_sets_every_store(tmp_path):
    ks = make(tmp_path)
    assert ks.is_engaged() is False
    ks.engage("api", "test")
    assert ks.is_engaged() and ks.kv.get("kill_switch") == "1" and (tmp_path / "KILL_SWITCH").exists()
    assert ks.log.events == [("engage", "api", "test")]


def test_engage_is_idempotent_and_logs_each_request(tmp_path):
    ks = make(tmp_path)
    ks.engage("telegram", "kill")
    ks.engage("api", "again")
    assert ks.is_engaged() and [e[0] for e in ks.log.events] == ["engage", "engage"]


def test_release_requires_a_reason_and_clears_every_store(tmp_path):
    ks = make(tmp_path)
    ks.engage("api", "x")
    with pytest.raises(ValueError):
        ks.release("api", "")
    with pytest.raises(ValueError):
        ks.release("api", "  ")
    assert ks.is_engaged()
    ks.release("api", "checked the logs")
    assert not ks.is_engaged() and ks.kv.get("kill_switch") == "0" and not (tmp_path / "KILL_SWITCH").exists()
    assert ks.log.events[-1] == ("release", "api", "checked the logs")


def test_unknown_source_is_refused(tmp_path):
    with pytest.raises(ValueError):
        make(tmp_path).engage("somebody", "x")


def test_engaging_works_with_redis_down(tmp_path):
    ks = make(tmp_path, kv=BrokenKV())
    ks.engage("telegram", "kill")                       # must not raise
    assert ks.is_engaged() and (tmp_path / "KILL_SWITCH").exists()
    assert ks.status()["degraded"]


def test_engaging_works_with_postgres_down(tmp_path):
    ks = make(tmp_path, log=BrokenLog())
    ks.engage("api", "x")
    assert ks.is_engaged() and ks.kv.get("kill_switch") == "1" and ks.status()["degraded"]


def test_engaging_works_with_redis_and_postgres_both_down(tmp_path):
    ks = make(tmp_path, kv=BrokenKV(), log=BrokenLog())
    ks.engage("flag_file", "disaster")
    assert ks.is_engaged() and (tmp_path / "KILL_SWITCH").exists()


def test_when_redis_cannot_be_read_the_switch_fails_closed(tmp_path):
    ks = make(tmp_path, kv=BrokenKV())
    assert ks.is_engaged() is True                       # cannot verify, so do not trade
    assert any("cannot read" in d for d in ks.status()["degraded"])


def test_release_is_refused_and_state_stays_engaged_if_it_cannot_be_logged(tmp_path):
    log = BrokenLog()
    log.down = False
    ks = make(tmp_path, log=log)
    ks.engage("api", "x")
    log.down = True
    with pytest.raises(RuntimeError, match="not logged"):
        ks.release("api", "try")
    assert ks.is_engaged() and (tmp_path / "KILL_SWITCH").exists() and ks.kv.get("kill_switch") == "1"


def test_flag_file_engages_and_is_logged_with_its_source(tmp_path):
    ks = make(tmp_path)
    (tmp_path / "KILL_SWITCH").write_text("touch")
    assert ks.is_engaged()                               # the file alone is enough, even before any sync
    ks.sync()
    assert ks.log.events == [("engage", "flag_file", "flag file present")] and ks.kv.get("kill_switch") == "1"
    ks.sync()
    assert len(ks.log.events) == 1                        # not logged repeatedly


def test_deleting_the_flag_file_does_not_release_the_switch(tmp_path):
    ks = make(tmp_path)
    ks.engage("api", "x")
    (tmp_path / "KILL_SWITCH").unlink()
    assert ks.is_engaged()                               # Redis and memory still say engaged
    ks.sync()
    assert (tmp_path / "KILL_SWITCH").exists()            # sync restores the marker
    assert ks.log.last_action() == "engage"


def test_state_survives_a_restart_from_each_single_surviving_store(tmp_path):
    for survivor in ("redis", "log", "file"):
        kv, log, path = MemoryStore(), MemoryEventLog(), tmp_path / f"f-{survivor}"
        first = KillSwitch(kv, log, path)
        first.engage("api", "x")
        kv2 = kv if survivor == "redis" else MemoryStore()      # Redis lost its data
        log2 = log if survivor == "log" else MemoryEventLog()   # database lost
        if survivor != "file" and path.exists():
            path.unlink()                                       # file lost
        second = KillSwitch(kv2, log2, path)
        second.load_on_boot()
        assert second.is_engaged(), survivor
        assert second.kv.get("kill_switch") == "1" and path.exists()      # re-asserted everywhere


def test_a_released_switch_stays_released_across_a_restart(tmp_path):
    kv, log, path = MemoryStore(), MemoryEventLog(), tmp_path / "f"
    ks = KillSwitch(kv, log, path)
    ks.engage("api", "x")
    ks.release("api", "ok")
    again = KillSwitch(kv, log, path)
    again.load_on_boot()
    assert not again.is_engaged()


def test_stale_redis_flag_does_not_survive_a_logged_release_but_still_blocks_until_boot_sync(tmp_path):
    kv, log, path = MemoryStore(), MemoryEventLog(), tmp_path / "f"
    log.append("engage", "api", "x")
    log.append("release", "api", "ok")
    kv.set("kill_switch", "1")                            # leftover from a crash mid-release
    ks = KillSwitch(kv, log, path)
    assert ks.is_engaged()                                # conservative until reconciled
    ks.load_on_boot()
    assert ks.is_engaged()                                # any store saying engaged wins; release must be explicit again


def test_the_kill_switch_module_never_imports_the_deriv_adapter():
    code = ("import sys, derivbot.killswitch.switch, derivbot.killswitch.telegram_commands;"
            "bad=[m for m in sys.modules if m.startswith('derivbot.deriv') or m in ('websockets',)];"
            "print(bad); sys.exit(1 if bad else 0)")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                       env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stdout + r.stderr


def test_unlogged_engage_events_are_flushed_when_the_log_recovers(tmp_path):
    log = BrokenLog()
    ks = make(tmp_path, log=log)
    ks.engage("api", "while postgres is down")
    assert ks.status()["pending_log_events"] == 1
    log.down = False
    ks.sync()
    assert ks.status()["pending_log_events"] == 0 and log.events == [("engage", "api", "while postgres is down")]


def test_incomplete_release_keeps_the_switch_engaged_and_says_so(tmp_path):
    kv = BrokenKV()
    kv.down = False
    ks = make(tmp_path, kv=kv)
    ks.engage("api", "x")
    kv.down = True
    with pytest.raises(RuntimeError, match="incomplete"):
        ks.release("api", "try")
    assert ks.is_engaged()
    assert ks.log.events[-1][0] == "engage"           # the record does not claim a release that did not take effect


def test_reasons_are_redacted_and_bounded(tmp_path):
    ks = make(tmp_path)
    ks.engage("api", "see wss://x/ws/demo?otp=SECRETOTP123456 " + "z" * 500)
    reason = ks.log.events[0][2]
    assert "SECRETOTP123456" not in reason and len(reason) <= 200


async def test_watcher_picks_up_a_flag_file_created_while_running(tmp_path):
    import asyncio

    from derivbot.killswitch.switch import watch_flag_file

    ks = make(tmp_path)
    halt = asyncio.Event()
    task = asyncio.create_task(watch_flag_file(ks, halt, interval_s=0.05))
    await asyncio.sleep(0.1)
    assert not ks.is_engaged()
    (tmp_path / "KILL_SWITCH").write_text("x")
    await asyncio.sleep(0.2)
    assert ks.log.events and ks.log.events[0][1] == "flag_file"
    halt.set()
    await asyncio.wait_for(task, 2)
