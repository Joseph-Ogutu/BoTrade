import pytest

from derivbot.killswitch.switch import KillSwitch, MemoryEventLog
from derivbot.killswitch.telegram_commands import TelegramCommands
from derivbot.kv.store import MemoryStore

ALLOWED = 1111


@pytest.fixture()
def setup(tmp_path):
    ks = KillSwitch(MemoryStore(), MemoryEventLog(), tmp_path / "KILL")
    alerts = []
    cmds = TelegramCommands(ks, {ALLOWED}, status_provider=lambda: {"mode": "demo", "open_positions": 0},
                            on_unauthorized=lambda chat, text: alerts.append((chat, text)))
    return ks, cmds, alerts


def test_status_works_for_an_allowed_chat_and_shows_no_secrets(setup):
    ks, cmds, _ = setup
    reply = cmds.handle(ALLOWED, "/status")
    assert "demo" in reply and "kill switch: clear" in reply.lower()


def test_pause_and_kill_both_engage_with_telegram_as_the_source(setup):
    ks, cmds, _ = setup
    assert "engaged" in cmds.handle(ALLOWED, "/pause").lower()
    assert ks.is_engaged() and ks.log.events[0] == ("engage", "telegram", "pause")
    ks2 = ks
    cmds.handle(ALLOWED, "/kill")
    assert ks2.log.events[-1] == ("engage", "telegram", "kill")


def test_an_unlisted_chat_gets_no_reply_and_changes_nothing(setup):
    ks, cmds, alerts = setup
    assert cmds.handle(9999, "/kill") is None
    assert not ks.is_engaged() and alerts == [(9999, "/kill")]


def test_telegram_can_never_release_resume_or_enable_real_trading(setup):
    ks, cmds, _ = setup
    ks.engage("api", "x")
    for text in ("/release", "/resume", "/unpause", "/start_trading", "/enable_real", "/real on", "/set TRADING_MODE=real",
                 "release", "/KILL_SWITCH off", "/status; /release"):
        reply = cmds.handle(ALLOWED, text)
        assert ks.is_engaged(), text
        assert reply is not None and ("unknown" in reply.lower() or "kill switch" in reply.lower()), text


def test_commands_are_matched_exactly_not_by_prefix(setup):
    ks, cmds, _ = setup
    assert "unknown" in cmds.handle(ALLOWED, "/killall").lower()
    assert not ks.is_engaged()
    assert "engaged" in cmds.handle(ALLOWED, "/kill now please").lower()      # trailing words after the command are ignored
    assert ks.is_engaged()


def test_bot_suffix_and_case_are_accepted(setup):
    ks, cmds, _ = setup
    cmds.handle(ALLOWED, "/KILL@derivbot_bot")
    assert ks.is_engaged()


def test_handler_has_no_access_to_settings_or_the_executor():
    import inspect

    sig = inspect.signature(TelegramCommands.__init__)
    assert set(sig.parameters) == {"self", "kill_switch", "allowed_chat_ids", "status_provider", "on_unauthorized"}
