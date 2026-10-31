import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run_gate1.py"
spec = importlib.util.spec_from_file_location("run_gate1", SCRIPT)
run_gate1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_gate1)


def test_refuses_without_the_explicit_flag(capsys):
    assert run_gate1.main(["--dbs", "/nonexistent.sqlite"]) == 2
    assert "refusing" in capsys.readouterr().out


def test_gate_is_closed_before_the_run_date_and_reads_no_database(capsys, tmp_path):
    missing = tmp_path / "must_not_be_opened.sqlite"
    code = run_gate1.main(["--execute-gate1", "--dbs", str(missing)], now_epoch=1_793_500_000)  # 2026-10-31, before the cutoff
    out = capsys.readouterr().out
    assert code == 3 and "GATE CLOSED" in out and "run date" in out
    assert not missing.exists()       # sqlite would have created the file had it been opened


def test_gate_stays_closed_after_the_date_without_a_frozen_harness_commit(capsys, tmp_path):
    code = run_gate1.main(["--execute-gate1", "--dbs", str(tmp_path / "x.sqlite")], now_epoch=1_794_700_800)
    out = capsys.readouterr().out
    assert code == 3 and "no frozen harness commit" in out
