import re
from pathlib import Path

from derivbot import app as appmod

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = (ROOT / "docker-compose.yml").read_text()


def block(name):
    return COMPOSE.split(f"\n  {name}:\n", 1)[1].split("\n\n", 1)[0]


def test_the_real_service_is_opt_in_loopback_only_and_pinned_to_real_settings():
    b = block("bot-real")
    assert 'profiles: ["real"]' in b                                                  # never started by `docker compose up` alone
    assert "TRADING_MODE: real" in b and 'ALLOW_REAL_TRADING: "true"' in b
    assert re.findall(r'^\s*-\s*"([0-9.:]+:\d+)"\s*$', b, re.M) == ["127.0.0.1:8788:8787"]
    assert "/run/secrets/deriv_pat_real" in b and "REAL_APPROVAL_FILE:-/dev/null" in b and ":ro" in b
    assert "HEARTBEAT_NAME: bot-real" in b and "FORWARD_TEST" not in b and "PIPELINE_SOAK" not in b


def test_the_default_services_and_the_demo_bot_never_carry_real_settings():
    demo = block("bot")
    assert "TRADING_MODE: demo" in demo and 'ALLOW_REAL_TRADING: "false"' in demo
    for name in ("postgres", "redis", "migrate", "collector"):
        assert "TRADING_MODE: real" not in block(name) and "deriv_pat_real" not in block(name)
    assert "deriv_pat_real" not in demo and "REAL_TRADING_APPROVED" not in demo


def test_the_kill_switch_state_is_shared_between_the_demo_and_the_real_bot():
    assert "KILL_SWITCH_PATH: /state/KILL_SWITCH" in block("bot") and "KILL_SWITCH_PATH: /state/KILL_SWITCH" in block("bot-real")
    assert "botstate:/state" in block("bot") and "botstate:/state" in block("bot-real")


def test_the_commit_is_read_from_git_else_from_the_baked_environment_value_and_only_if_it_is_forty_hex_digits(tmp_path, monkeypatch):
    good = "0123456789abcdef0123456789abcdef01234567"
    monkeypatch.setenv("DERIVBOT_COMMIT", good)
    assert appmod.head_commit(tmp_path) == good                                    # no git checkout here: the baked value
    for bad in ("", "short", "z" * 40, good + "0", good.upper()[:39]):
        monkeypatch.setenv("DERIVBOT_COMMIT", bad)
        assert appmod.head_commit(tmp_path) is None or len(appmod.head_commit(tmp_path)) == 40
    monkeypatch.setenv("DERIVBOT_COMMIT", "NOT-HEX")
    assert appmod.head_commit(tmp_path) is None


def test_the_approval_file_is_never_committed():
    """The build lock was opened by the owner on 2026-11-06; the approval file stays a local, git-ignored file."""
    import subprocess

    tracked = subprocess.run(["git", "ls-files", "REAL_TRADING_APPROVED"], cwd=ROOT, capture_output=True, text=True).stdout
    assert tracked.strip() == ""
