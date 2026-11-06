import json

import pytest

from derivbot.db.audit import append_audit
from derivbot.forward.promote import passed_strategies, promote

pytestmark = pytest.mark.postgres

LOCK = {"ema_trend": {"id": "ema_trend", "version": "1"}, "regime_ema_trend": {"id": "regime_trending(ema_trend)", "version": "1"}}


def record(conn, variant, passed, void=False):
    append_audit(conn, "forward_report", "FORWARD_EVALUATION", {"passed": passed, "void": void}, ref_id=variant)


def test_nothing_is_approved_without_a_recorded_pass(conn, tmp_path):
    record(conn, "ema_trend:R_100", False)
    record(conn, "control_random:R_100", None)
    f = tmp_path / "strategies.json"
    assert promote(conn, LOCK, f) == {} and not f.exists()


def test_a_recorded_pass_approves_the_strategy_id_and_version_and_keeps_existing_entries(conn, tmp_path):
    f = tmp_path / "strategies.json"
    f.write_text(json.dumps({"approved": {"other": "7"}}))
    record(conn, "regime_ema_trend:1HZ100V", True)
    assert promote(conn, LOCK, f) == {"regime_trending(ema_trend)": "1"}
    assert json.loads(f.read_text()) == {"approved": {"other": "7", "regime_trending(ema_trend)": "1"}}
    assert promote(conn, LOCK, f) == {}                                              # second run changes nothing


def test_a_voided_evaluation_and_an_unregistered_variant_never_approve(conn, tmp_path):
    record(conn, "ema_trend:R_100", True, void=True)
    record(conn, "unknown:R_100", True)
    assert passed_strategies(conn, LOCK) == {}
