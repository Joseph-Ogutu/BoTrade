"""One test per layer that keeps real trading unreachable, so removing a layer is a visible, failing change."""

import re
from pathlib import Path

import pytest

from derivbot.guard import real_guard
from derivbot.guard.endpoint import PROD_POLICY, RealTradingDisabled, build_ws_url
from derivbot.risk import rules

ROOT = Path(__file__).resolve().parents[2]


def test_layer_1_build_lock_is_closed():
    assert real_guard.REAL_TRADING_IMPLEMENTED is False


def test_layer_2_no_url_builder_returns_a_real_endpoint():
    with pytest.raises(RealTradingDisabled):
        build_ws_url("real", PROD_POLICY)


def test_layer_3_risk_rule_rejects_anything_but_demo():
    assert not rules.mode_and_account_consistent("real", "real", "real", False).passed
    assert not rules.mode_and_account_consistent("demo", "demo", "real", True).passed


def test_layer_4_the_executor_rereads_demo_ness_immediately_before_the_buy():
    text = (ROOT / "src/derivbot/execution/executor.py").read_text()
    pre_buy = text.split("# ---- buy: commit BUY_SENT first")[0]
    assert "mode not confirmed before buy" in pre_buy and "get_conn_info()" in pre_buy and "real_unlocked" in pre_buy


def test_layer_5_the_demo_only_database_constraints_were_removed_deliberately_by_migration_0003_and_the_mode_column_check_stays():
    """HISTORY: 0001 created order_intents_demo_only and orders_demo_only. On 2026-11-06 migration 0003 dropped them on purpose (the
    real-account path). The column CHECK (mode IN ('demo','real')) from 0001 remains. The layer was replaced by the executor refusing
    an intent whose mode differs from the process mode (engages the kill switch) and by risk rule 2."""
    first = (ROOT / "migrations/versions/0001_initial_schema.py").read_text()
    third = (ROOT / "migrations/versions/0003_allow_real_mode.py").read_text()
    assert "order_intents_demo_only CHECK (mode = 'demo')" in first and "mode text NOT NULL CHECK (mode IN ('demo','real'))" in first
    assert "DROP CONSTRAINT IF EXISTS order_intents_demo_only" in third and "DROP CONSTRAINT IF EXISTS orders_demo_only" in third


def test_layer_6_the_approval_file_and_the_checklist_are_not_in_the_repository_and_the_file_is_ignored():
    assert not (ROOT / "REAL_TRADING_APPROVED").exists()
    assert "REAL_TRADING_APPROVED" in (ROOT / ".gitignore").read_text().splitlines()
    assert "[x]" not in (ROOT / "docs/live-readiness.md").read_text()


def test_layer_7_example_env_and_config_enable_nothing():
    env = dict(line.split("=", 1) for line in (ROOT / ".env.example").read_text().splitlines() if "=" in line)
    assert env["TRADING_MODE"] == "demo" and env["ALLOW_REAL_TRADING"] == "false"
    for k in ("DERIV_APP_ID_REAL", "DERIV_PAT_REAL", "DERIV_ACCOUNT_ID_REAL"):
        assert env[k] == ""


def test_layer_8_real_credentials_are_read_only_inside_the_guard_package_and_settings():
    """Deliberate change 2026-11-06 (real-account path): guard/real_otp.py reads them too, and only it. Anything new on this list is a
    visible, reviewable decision, which is what this test is for."""
    uses = [p.name for p in (ROOT / "src").rglob("*.py") if re.search(r"deriv_pat_real|deriv_account_id_real|deriv_app_id_real", p.read_text())]
    assert sorted(uses) == ["real_guard.py", "real_otp.py", "settings.py"]
    for p in (ROOT / "src").rglob("*.py"):
        if p.name == "real_otp.py":
            assert p.parent.name == "guard"                           # and it lives in the guard package
