import datetime as dt
import json
import os
import subprocess
from pathlib import Path

import pytest

from derivbot.gate1.holdout import MARKER, holdout_start_epoch
from derivbot.gate1.registration import load_registration
from derivbot.stats.edge import required_n

ROOT = Path(__file__).resolve().parents[2]
V1_COMMIT = "87f96c1"


def _git(*args, cwd=ROOT, env=None):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env, check=True).stdout


def test_original_v1_text_is_preserved_unedited():
    try:
        v1 = _git("show", f"{V1_COMMIT}:docs/kill-criterion.md")
    except subprocess.CalledProcessError:
        pytest.skip("v1 commit not available in this checkout")
    current = (ROOT / "docs" / "kill-criterion.md").read_text()
    placeholder = "(none)\n"
    assert v1.endswith(placeholder)
    # Everything in v1 except the placeholder line must be a byte-for-byte prefix of the current file.
    assert current.startswith(v1[: -len(placeholder)])
    assert MARKER in current[len(v1) - len(placeholder):]


def test_registration_block_is_single_and_consistent_with_the_text():
    reg = load_registration()
    assert reg["amendment"] == 1
    conf, expl, tests = reg["confirmatory_symbols"], reg["exploratory_symbols"], reg["tests"]
    assert conf == ["1HZ100V", "R_100"]
    assert len(tests) == 4 and len(set(tests)) == 4
    assert reg["confirmatory_family_size"] == len(tests) * len(conf) == 8
    assert reg["exploratory_family_size"] == len(tests) * len(expl) == 24
    assert not set(conf) & set(expl)
    assert reg["correction"] == "holm" and reg["alpha_family"] == 0.05


def test_cutoff_epoch_matches_iso_and_run_date_is_a_sunday():
    reg = load_registration()
    iso = dt.datetime.strptime(reg["data_cutoff_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    assert int(iso.timestamp()) == reg["data_cutoff_epoch"]
    assert reg["run_date_utc"] == "2026-11-15" and dt.date(2026, 11, 15).strftime("%A") == "Sunday"
    assert reg["data_cutoff_utc"].startswith(reg["run_date_utc"])


def test_symbol_tiers_match_the_collector_config_exactly():
    reg = load_registration()
    cfg = json.loads((ROOT / "config" / "collector.json").read_text())
    names = [s["symbol"] for s in cfg["symbols"]]
    assert sorted(names) == sorted(reg["confirmatory_symbols"] + reg["exploratory_symbols"])


def test_break_even_values_match_the_observed_quotes():
    reg = load_registration()
    batch = json.loads((ROOT / "docs/evidence/public_probe_batch.json").read_text())["responses"]
    one = batch["1HZ100V:DIGITEVEN:None"]["proposal"]
    minimum = batch["1HZ100V:DIGITEVEN:stake0.35"]["proposal"]
    assert round(one["ask_price"] / one["payout"], 6) == reg["break_even"]["stake_1.00"] == 0.512821
    assert round(minimum["ask_price"] / minimum["payout"], 6) == reg["break_even"]["stake_0.35"] == 0.530303
    text = (ROOT / "docs" / "kill-criterion.md").read_text()
    for needle in ("0.512821", "0.530303", "0.411523", "0.111982", "0.917431"):
        assert needle in text


def test_power_numbers_quoted_in_the_amendment_are_the_computed_ones():
    text = (ROOT / "docs" / "kill-criterion.md").read_text()
    for p in (1 / 1.95, 0.35 / 0.66):
        for k, quoted in ((1, None), (8, None), (20, None)):
            n = required_n(p, 0.02, 0.05 / k, 0.80)
            assert f"{n:,}" in text or str(n) in text, (p, k, n)
    n_parity = required_n(0.5, 1 / 1.95 - 0.5, (0.05 / 8) / 2, 0.80)
    assert f"{n_parity:,}" in text


def test_no_em_dashes():
    assert "—" not in (ROOT / "docs" / "kill-criterion.md").read_text()


def test_holdout_start_is_the_first_commit_with_the_marker(tmp_path):
    def commit(msg, when):
        env = {**os.environ, "GIT_COMMITTER_DATE": when, "GIT_AUTHOR_DATE": when,
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t"}
        _git("add", "-A", cwd=tmp_path, env=env)
        _git("commit", "-q", "-m", msg, cwd=tmp_path, env=env)

    _git("init", "-q", cwd=tmp_path)
    (tmp_path / "docs").mkdir()
    f = tmp_path / "docs" / "kill-criterion.md"
    f.write_text("v1\n")
    commit("v1", "2026-10-31T21:39:27+00:00")
    with pytest.raises(ValueError):
        holdout_start_epoch(tmp_path)
    f.write_text(f"v1\n{MARKER}\n")
    commit("amend", "2026-11-01T00:00:00+03:00")  # offset must not matter: epoch is absolute
    f.write_text(f"v1\n{MARKER}\nmore\n")
    commit("later edit", "2026-11-02T00:00:00+00:00")
    expected = int(dt.datetime(2026, 10, 31, 21, 0, 0, tzinfo=dt.timezone.utc).timestamp())
    assert holdout_start_epoch(tmp_path) == expected
