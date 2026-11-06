import json
import shutil
from pathlib import Path

import pytest

from derivbot.forward.permit import ForwardTestNotApproved, lock_of, require_forward_test_approval
from derivbot.signals.strategies import default_strategies

ROOT = Path(__file__).resolve().parents[2]
LINE = "LIVE_DEMO_FORWARD_TEST_APPROVED: 2026-11-06 by Joseph (owner) amendment=5\n"


@pytest.fixture()
def repo(tmp_path):
    (tmp_path / "docs").mkdir()
    for name in ("kill-criterion.md", "forward_test_lock.json"):
        shutil.copy(ROOT / "docs" / name, tmp_path / "docs" / name)
    (tmp_path / "docs" / "approvals.md").write_text("# Approvals\n\n" + LINE)
    return tmp_path


def test_the_committed_repository_is_approved_and_locked_as_registered():
    a = require_forward_test_approval(ROOT, default_strategies())
    assert a.date == "2026-11-06" and a.registration["family_size_k"] == 14


def test_a_complete_copy_is_accepted(repo):
    assert require_forward_test_approval(repo, default_strategies()).owner.startswith("Joseph")


def test_no_approval_line_means_no_forward_test(repo):
    (repo / "docs" / "approvals.md").write_text("# Approvals\nREAL_DATA_BACKTEST_APPROVED: 2026-11-16 by X gate1=STRUCTURE_FOUND\n")
    with pytest.raises(ForwardTestNotApproved, match="no LIVE_DEMO_FORWARD_TEST_APPROVED"):
        require_forward_test_approval(repo, default_strategies())


@pytest.mark.parametrize("bad", ["  LIVE_DEMO_FORWARD_TEST_APPROVED: 2026-11-06 by Joseph amendment=5\n", "LIVE_DEMO_FORWARD_TEST_APPROVED: 2026-11-06 by Joseph amendment=4\n",
                                 "LIVE_DEMO_FORWARD_TEST_APPROVED: tomorrow by Joseph amendment=5\n", "LIVE_DEMO_FORWARD_TEST_APPROVED: 2026-11-06 amendment=5\n"])
def test_only_the_exact_line_form_counts(repo, bad):
    (repo / "docs" / "approvals.md").write_text(bad)
    with pytest.raises(ForwardTestNotApproved):
        require_forward_test_approval(repo, default_strategies())


def test_the_amendment_must_be_present(repo):
    text = (repo / "docs" / "kill-criterion.md").read_text().replace("AMENDMENT-5-MARKER", "REMOVED")
    (repo / "docs" / "kill-criterion.md").write_text(text)
    with pytest.raises(ForwardTestNotApproved, match="Amendment 5 is not"):
        require_forward_test_approval(repo, default_strategies())


def test_a_changed_strategy_parameter_is_refused_as_a_new_variant(repo):
    import dataclasses

    strategies = default_strategies()
    strategies["ema_trend"] = dataclasses.replace(strategies["ema_trend"], fast=5)      # a quiet tuning edit
    with pytest.raises(ForwardTestNotApproved, match="NEW variant"):
        require_forward_test_approval(repo, strategies)


def test_a_missing_lock_a_missing_strategy_and_an_extra_strategy_are_refused(repo):
    (repo / "docs" / "forward_test_lock.json").unlink()
    with pytest.raises(ForwardTestNotApproved, match="lock.json is missing"):
        require_forward_test_approval(repo, default_strategies())
    shutil.copy(ROOT / "docs" / "forward_test_lock.json", repo / "docs" / "forward_test_lock.json")
    fewer = default_strategies()
    del fewer["ema_trend"]
    with pytest.raises(ForwardTestNotApproved, match="differ from the registered"):
        require_forward_test_approval(repo, fewer)


def test_the_lock_file_equals_what_the_code_produces_today():
    assert json.loads((ROOT / "docs" / "forward_test_lock.json").read_text()) == lock_of(default_strategies())
