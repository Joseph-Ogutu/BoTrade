"""Real mode must be impossible to reach by accident, by one flag, by a missing file, or by a wrong account type."""

import itertools
from pathlib import Path

import pytest

from derivbot.config.settings import load_settings
from derivbot.guard import real_guard as rg
from derivbot.guard.endpoint import (
    DEMO_PATH,
    PROD_POLICY,
    EndpointPolicy,
    RealTradingDisabled,
    build_ws_url,
    classify_url,
)

ROOT = Path(__file__).resolve().parents[2]
HEAD = "0123456789abcdef0123456789abcdef01234567"
ENV = {"DATABASE_URL": "postgresql+psycopg://u:FAKEPW1234@h/d", "REDIS_URL": "redis://r:6379/0"}
READY = ("# checklist\n" + "".join(f"- [x] item {i}\n" for i in range(5)) + "\nOwner sign-off: Joseph 2026-12-01\n")


def settings(**over):
    env = {**ENV, "TRADING_MODE": "demo", "ALLOW_REAL_TRADING": "false", **over}
    return load_settings(env)


def repo(tmp_path, *, approved=True, checklist=True, commit=HEAD):
    (tmp_path / "docs").mkdir(exist_ok=True)
    if approved:
        (tmp_path / "REAL_TRADING_APPROVED").write_text(f"approved_on: 2026-12-01\ncommit: {commit}\napproved_by: Joseph\n")
    if checklist:
        (tmp_path / "docs" / "live-readiness.md").write_text(READY)
    return tmp_path


REAL_ENV = {"TRADING_MODE": "real", "ALLOW_REAL_TRADING": "true", "DERIV_APP_ID_REAL": "999", "DERIV_PAT_REAL": "pat_FAKE_real_token_000",
            "DERIV_ACCOUNT_ID_REAL": "REAL-ACC-1"}

CONDITIONS = ["mode_is_real", "allow_flag", "approval_file", "real_credentials_and_endpoint", "checklist"]


def build(tmp_path, flags):
    """flags: set of the condition names that are TRUE."""
    env = {}
    if "mode_is_real" in flags:
        env["TRADING_MODE"] = "real"
    if "allow_flag" in flags:
        env["ALLOW_REAL_TRADING"] = "true"
    if "real_credentials_and_endpoint" in flags:
        env.update({k: v for k, v in REAL_ENV.items() if k.startswith("DERIV_")})
    r = repo(tmp_path, approved="approval_file" in flags, checklist="checklist" in flags)
    return settings(**env), r, ("real" if "real_credentials_and_endpoint" in flags else None)


# ------------------------------------------------------------------ the five conditions, exhaustively

def test_all_five_conditions_with_the_build_lock_open_would_resolve_real(tmp_path):
    s, r, ep = build(tmp_path, set(CONDITIONS))
    d = rg.resolve_mode(s, repo_root=r, head_commit=HEAD, otp_endpoint_mode=ep, build_lock=False)
    assert d.mode is rg.Mode.REAL and d.failed == []


@pytest.mark.parametrize("size", range(0, 5))
def test_every_incomplete_combination_of_conditions_resolves_demo_and_names_what_is_missing(tmp_path_factory, size):
    for combo in itertools.combinations(CONDITIONS, size):
        s, r, ep = build(tmp_path_factory.mktemp("c"), set(combo))
        d = rg.resolve_mode(s, repo_root=r, head_commit=HEAD, otp_endpoint_mode=ep, build_lock=False)
        assert d.mode is rg.Mode.DEMO, combo
        assert set(d.failed) == set(CONDITIONS) - set(combo), combo


def test_with_the_production_build_lock_even_all_five_conditions_stay_demo(tmp_path):
    s, r, ep = build(tmp_path, set(CONDITIONS))
    d = rg.resolve_mode(s, repo_root=r, head_commit=HEAD, otp_endpoint_mode=ep)          # default build_lock=True
    assert d.mode is rg.Mode.DEMO and "not implemented in this build" in " ".join(d.reasons)
    assert rg.REAL_TRADING_IMPLEMENTED is False


def test_the_committed_repository_and_defaults_resolve_demo():
    d = rg.resolve_mode(settings(), repo_root=ROOT, head_commit=HEAD, otp_endpoint_mode=None)
    assert d.mode is rg.Mode.DEMO and set(d.failed) == set(CONDITIONS)
    assert not (ROOT / "REAL_TRADING_APPROVED").exists()


# ------------------------------------------------------------------ the approval file

@pytest.mark.parametrize("body,why", [
    ("", "empty"),
    ("approved_on: 2026-12-01\napproved_by: J\n", "no commit"),
    (f"approved_on: not-a-date\ncommit: {HEAD}\napproved_by: J\n", "bad date"),
    (f"approved_on: 2026-12-01\ncommit: {HEAD}\napproved_by:\n", "no approver"),
    ("approved_on: 2026-12-01\ncommit: fedcba9876543210\napproved_by: J\n", "other commit"),
    ("approved_on: 2026-12-01\ncommit: abc\napproved_by: J\n", "commit too short"),
    ("I approve", "free text"),
])
def test_a_wrong_or_stale_approval_file_does_not_count(tmp_path, body, why):
    (tmp_path / "REAL_TRADING_APPROVED").write_text(body)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "live-readiness.md").write_text(READY)
    s = settings(**{k: v for k, v in REAL_ENV.items()})
    d = rg.resolve_mode(s, repo_root=tmp_path, head_commit=HEAD, otp_endpoint_mode="real", build_lock=False)
    assert d.mode is rg.Mode.DEMO and "approval_file" in d.failed, why


def test_an_approval_for_a_short_prefix_of_the_deployed_commit_is_accepted_only_at_seven_or_more_characters(tmp_path):
    r = repo(tmp_path, commit=HEAD[:7])
    s = settings(**REAL_ENV)
    assert rg.resolve_mode(s, repo_root=r, head_commit=HEAD, otp_endpoint_mode="real", build_lock=False).mode is rg.Mode.REAL


# ------------------------------------------------------------------ the checklist

@pytest.mark.parametrize("body", [
    "", "- [ ] one\n\nOwner sign-off: J 2026-12-01\n", READY.replace("[x] item 2", "[ ] item 2"),
    READY.replace("Owner sign-off: Joseph 2026-12-01", "Owner sign-off:"), "no boxes at all\nOwner sign-off: J 2026-12-01\n",
])
def test_an_incomplete_checklist_fails(tmp_path, body):
    r = repo(tmp_path, checklist=False)
    (tmp_path / "docs" / "live-readiness.md").write_text(body)
    d = rg.resolve_mode(settings(**REAL_ENV), repo_root=r, head_commit=HEAD, otp_endpoint_mode="real", build_lock=False)
    assert "checklist" in d.failed and d.mode is rg.Mode.DEMO


def test_the_committed_checklist_template_is_entirely_unticked():
    text = (ROOT / "docs" / "live-readiness.md").read_text()
    assert "- [x]" not in text and text.count("- [ ]") >= 8


# ------------------------------------------------------------------ account type and credentials

def test_real_credentials_must_all_be_present_and_the_account_must_map_to_the_real_endpoint(tmp_path):
    r = repo(tmp_path)
    for drop in ("DERIV_APP_ID_REAL", "DERIV_PAT_REAL", "DERIV_ACCOUNT_ID_REAL"):
        env = {k: v for k, v in REAL_ENV.items() if k != drop}
        d = rg.resolve_mode(settings(**env), repo_root=r, head_commit=HEAD, otp_endpoint_mode="real", build_lock=False)
        assert d.mode is rg.Mode.DEMO and "real_credentials_and_endpoint" in d.failed, drop
    for ep in (None, "demo"):                                     # unknown or demo endpoint for the real account id
        d = rg.resolve_mode(settings(**REAL_ENV), repo_root=r, head_commit=HEAD, otp_endpoint_mode=ep, build_lock=False)
        assert d.mode is rg.Mode.DEMO and "real_credentials_and_endpoint" in d.failed, ep


def test_demo_account_with_real_flags_set_but_no_real_credentials_stays_demo(tmp_path):
    r = repo(tmp_path)
    s = settings(TRADING_MODE="real", ALLOW_REAL_TRADING="true", DERIV_APP_ID_DEMO="1", DERIV_PAT_DEMO="pat_FAKE_demo_token_000",
                 DERIV_ACCOUNT_ID_DEMO="DEMO-1")
    d = rg.resolve_mode(s, repo_root=r, head_commit=HEAD, otp_endpoint_mode="demo", build_lock=False)
    assert d.mode is rg.Mode.DEMO


# ------------------------------------------------------------------ endpoint allowlist and url building

def test_classify_url_accepts_only_the_exact_production_demo_and_real_urls():
    base = "wss://api.derivws.com/trading/v1/options/ws"
    assert classify_url(f"{base}/demo?otp=abc", PROD_POLICY) == "demo"
    assert classify_url(f"{base}/real?otp=abc", PROD_POLICY) == "real"
    for bad in ("ws://api.derivws.com/trading/v1/options/ws/demo?otp=a", "wss://api.derivws.com.evil.com/trading/v1/options/ws/demo?otp=a",
                "wss://evil.com/trading/v1/options/ws/demo", "wss://api.derivws.com/trading/v1/options/ws/demo/extra",
                "wss://api.derivws.com/trading/v1/options/ws/public", "wss://api.derivws.com/", "", "garbage",
                "wss://user:pw@api.derivws.com/trading/v1/options/ws/demo", "wss://api.derivws.com:8443/trading/v1/options/ws/demo"):
        assert classify_url(bad, PROD_POLICY) is None, bad


def test_build_ws_url_refuses_real_in_every_form():
    assert build_ws_url("demo", PROD_POLICY) == "wss://api.derivws.com" + DEMO_PATH
    for mode in ("real", "REAL", " real", "live", "", None):
        with pytest.raises(RealTradingDisabled):
            build_ws_url(mode, PROD_POLICY)   # type: ignore[arg-type]


def test_a_test_policy_for_the_fake_server_is_explicit_and_never_the_default():
    pol = EndpointPolicy(scheme="ws", host="127.0.0.1", port=12345)
    assert classify_url(f"ws://127.0.0.1:12345{DEMO_PATH}?otp=x", pol) == "demo"
    assert classify_url(f"ws://127.0.0.1:12345{DEMO_PATH}", PROD_POLICY) is None
    assert PROD_POLICY.host == "api.derivws.com" and PROD_POLICY.scheme == "wss"


def test_no_source_file_builds_or_hard_codes_a_real_websocket_url_except_the_endpoint_policy():
    for p in (ROOT / "src").rglob("*.py"):
        if p.name == "endpoint.py":
            continue
        assert "ws/real" not in p.read_text(), p
