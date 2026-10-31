import datetime as dt
import json
import re
from pathlib import Path

import pytest

from derivbot.common.evidence import EvidenceError, check_evidence, stamp_file
from derivbot.common.timeutil import iso_from_epoch, parse_utc, utc_now_iso, weekday_name

ROOT = Path(__file__).resolve().parents[2]
UTC_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def test_calendar_facts():
    assert weekday_name("2026-10-31") == "Saturday"
    assert weekday_name("2026-11-01") == "Sunday"
    assert weekday_name("2026-11-02") == "Monday"


def test_iso_from_epoch_is_utc_with_z():
    assert iso_from_epoch(1793484098) == "2026-10-31T22:01:38Z"
    assert iso_from_epoch(0) == "1970-01-01T00:00:00Z"


def test_utc_now_iso_format_and_clock_injection():
    assert UTC_Z.match(utc_now_iso())
    fixed = dt.datetime(2026, 11, 1, 3, 4, 5, tzinfo=dt.timezone(dt.timedelta(hours=3)))
    assert utc_now_iso(lambda: fixed) == "2026-11-01T00:04:05Z"  # converted, not relabelled


def test_parse_utc_rejects_naive_and_non_z():
    assert parse_utc("2026-10-31T22:01:38Z").tzinfo is not None
    for bad in ("2026-10-31T22:01:38", "2026-10-31T22:01:38+03:00", "2026-10-31 22:01:38Z", "nope"):
        with pytest.raises(ValueError):
            parse_utc(bad)


def test_stamp_file_adds_evidence_block_and_keeps_response(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"msg_type": "time", "time": 1793484262}))
    stamp_file(p, captured_at_utc="2026-10-31T22:04:22Z", provenance="recorded", request={"time": 1},
               server_time_utc="2026-10-31T22:04:22Z")
    data = json.loads(p.read_text())
    assert data["time"] == 1793484262 and data["msg_type"] == "time"
    ev = data["_evidence"]
    assert ev["captured_at_utc"] == "2026-10-31T22:04:22Z" and ev["request"] == {"time": 1}
    check_evidence(p)


def test_stamp_file_refuses_non_utc_label(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{}")
    with pytest.raises(ValueError):
        stamp_file(p, captured_at_utc="2026-11-01T01:00:51+03:00", provenance="x")


def test_stamp_is_idempotent_and_does_not_overwrite_original_stamp(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("{}")
    stamp_file(p, captured_at_utc="2026-10-31T22:00:00Z", provenance="first")
    stamp_file(p, captured_at_utc="2026-11-05T00:00:00Z", provenance="second")
    assert json.loads(p.read_text())["_evidence"]["captured_at_utc"] == "2026-10-31T22:00:00Z"


def test_check_evidence_fails_when_unstamped_or_bad(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"a": 1}))
    with pytest.raises(EvidenceError):
        check_evidence(p)
    p.write_text(json.dumps({"_evidence": {"captured_at_utc": "2026-11-01T01:00:51", "provenance": "x"}}))
    with pytest.raises(EvidenceError):
        check_evidence(p)


def test_every_committed_evidence_file_records_a_raw_utc_timestamp():
    files = sorted((ROOT / "docs" / "evidence").glob("*.json"))
    assert files, "no evidence files found"
    for f in files:
        check_evidence(f)


# --- documents: a date followed by a weekday name must match the calendar ---------------------------

DATE_DAY = re.compile(
    r"(\d{4}-\d{2}-\d{2})[^.\n|]{0,40}?\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|"
    r"Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b"
)
DAY_DATE = re.compile(
    r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b[,\s]{1,3}"
    r"(\d{4}-\d{2}-\d{2})"
)


def _docs():
    for p in list((ROOT / "docs").rglob("*.md")) + [ROOT / "STATE.md", ROOT / "CLAUDE.md"]:
        if p.name in ("HANDBOOK.md",):
            continue
        yield p


def test_weekday_names_next_to_dates_match_the_calendar():
    bad = []
    for p in _docs():
        text = p.read_text()
        for m in DATE_DAY.finditer(text):
            if weekday_name(m.group(1))[:3] != m.group(2)[:3]:
                bad.append(f"{p.name}: {m.group(0)!r} is a {weekday_name(m.group(1))}")
        for m in DAY_DATE.finditer(text):
            if weekday_name(m.group(2))[:3] != m.group(1)[:3]:
                bad.append(f"{p.name}: {m.group(0)!r} is a {weekday_name(m.group(2))}")
    assert not bad, "\n".join(bad)


def test_no_doc_labels_a_non_utc_time_as_utc():
    # A "Z" or "UTC" time stamp must not be followed by a +03:00 style offset.
    bad = [p.name for p in _docs() if re.search(r"\d{2}:\d{2}:\d{2}Z[+-]\d{2}:\d{2}", p.read_text())]
    assert not bad
