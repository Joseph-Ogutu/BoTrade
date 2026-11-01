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
    assert "demo mode not confirmed before buy" in pre_buy and "get_conn_info()" in pre_buy


def test_layer_5_database_constraints_exist_for_intents_and_orders():
    sql = (ROOT / "migrations/versions/0001_initial_schema.py").read_text()
    assert "order_intents_demo_only CHECK (mode = 'demo')" in sql and "orders_demo_only CHECK (mode = 'demo')" in sql


def test_layer_6_the_approval_file_and_the_checklist_are_not_in_the_repository_and_the_file_is_ignored():
    assert not (ROOT / "REAL_TRADING_APPROVED").exists()
    assert "REAL_TRADING_APPROVED" in (ROOT / ".gitignore").read_text().splitlines()
    assert "[x]" not in (ROOT / "docs/live-readiness.md").read_text()


def test_layer_7_example_env_and_config_enable_nothing():
    env = dict(line.split("=", 1) for line in (ROOT / ".env.example").read_text().splitlines() if "=" in line)
    assert env["TRADING_MODE"] == "demo" and env["ALLOW_REAL_TRADING"] == "false"
    for k in ("DERIV_APP_ID_REAL", "DERIV_PAT_REAL", "DERIV_ACCOUNT_ID_REAL"):
        assert env[k] == ""


def test_layer_8_only_the_otp_provider_and_the_executor_path_connect_and_neither_can_use_real_credentials():
    uses = [p.name for p in (ROOT / "src").rglob("*.py") if re.search(r"deriv_pat_real|deriv_account_id_real|deriv_app_id_real", p.read_text())]
    assert sorted(uses) == ["real_guard.py", "settings.py"]          # real credentials are read by nothing else
