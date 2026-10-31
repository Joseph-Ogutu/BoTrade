import json
import sqlite3
from pathlib import Path

import pytest

from derivbot.backtest.permit import Approval, RealDataNotApproved, load_real_series, require_real_data_approval

ROOT = Path(__file__).resolve().parents[2]


def repo(tmp_path, approval_line=None, gate1_outcome=None):
    (tmp_path / "docs" / "evidence").mkdir(parents=True)
    (tmp_path / "docs" / "approvals.md").write_text("# Approvals\n" + (approval_line + "\n" if approval_line else ""))
    if gate1_outcome:
        (tmp_path / "docs" / "evidence" / "gate1_RUN_20261115T000000Z.json").write_text(json.dumps({"outcome": gate1_outcome}))
    return tmp_path


def test_the_committed_repository_has_no_approval():
    with pytest.raises(RealDataNotApproved):
        require_real_data_approval(ROOT)


def test_missing_file_missing_line_or_missing_gate1_run_are_all_refusals(tmp_path):
    with pytest.raises(RealDataNotApproved, match="does not exist"):
        require_real_data_approval(tmp_path)
    r = repo(tmp_path / "a")
    with pytest.raises(RealDataNotApproved, match="no REAL_DATA"):
        require_real_data_approval(r)
    r = repo(tmp_path / "b", "REAL_DATA_BACKTEST_APPROVED: 2026-11-16 by Joseph gate1=STRUCTURE_FOUND")
    with pytest.raises(RealDataNotApproved, match="Gate 1 has not been run"):
        require_real_data_approval(r)


def test_approval_must_match_the_gate1_record(tmp_path):
    line = "REAL_DATA_BACKTEST_APPROVED: 2026-11-16 by Joseph gate1=STRUCTURE_FOUND"
    with pytest.raises(RealDataNotApproved, match="Gate 1 record says NO_STRUCTURE_DETECTED"):
        require_real_data_approval(repo(tmp_path / "a", line, "NO_STRUCTURE_DETECTED"))
    ok = require_real_data_approval(repo(tmp_path / "b", line, "STRUCTURE_FOUND"))
    assert ok == Approval("2026-11-16", "Joseph", "STRUCTURE_FOUND")


def test_an_indented_example_or_a_quoted_line_is_not_an_approval(tmp_path):
    r = repo(tmp_path, "    REAL_DATA_BACKTEST_APPROVED: 2026-11-16 by Joseph gate1=STRUCTURE_FOUND", "STRUCTURE_FOUND")
    with pytest.raises(RealDataNotApproved):
        require_real_data_approval(r)


def test_loading_real_ticks_requires_an_approval_object(tmp_path):
    db = tmp_path / "t.sqlite"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE ticks (symbol TEXT, epoch INTEGER, quote TEXT, pip_size INTEGER)")
    c.execute("INSERT INTO ticks VALUES ('A', 5000, '1.10', 2)")
    c.commit()
    c.close()
    with pytest.raises(RealDataNotApproved):
        load_real_series(str(db), "A", 0, 10_000, approval=None)       # type: ignore[arg-type]
    s = load_real_series(str(db), "A", 0, 10_000, Approval("2026-11-16", "Joseph", "STRUCTURE_FOUND"))
    assert len(s) == 1 and s.digits.tolist() == [0]


def test_no_backtest_module_reads_the_tick_store_except_through_the_permit():
    for p in (ROOT / "src" / "derivbot" / "backtest").glob("*.py"):
        if p.name == "permit.py":
            continue
        text = p.read_text()
        assert "sqlite3" not in text and "FROM ticks" not in text, p.name
    for p in (ROOT / "src" / "derivbot" / "signals").glob("*.py"):
        assert "sqlite3" not in p.read_text() and "FROM ticks" not in p.read_text()
