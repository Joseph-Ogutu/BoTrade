import numpy as np
import pytest

from derivbot.collector.report import coverage_summary, day_rows
from derivbot.gate1.segments import segment_ids, segment_lengths, split_segments


def test_segment_ids_break_when_gap_exceeds_max_gap():
    ep = np.array([10, 11, 12, 14, 15, 30, 31])
    assert segment_ids(ep, max_gap=1).tolist() == [0, 0, 0, 1, 1, 2, 2]
    assert segment_ids(ep, max_gap=2).tolist() == [0, 0, 0, 0, 0, 1, 1]


def test_segment_ids_empty_and_single():
    assert segment_ids(np.array([], dtype=np.int64), 1).tolist() == []
    assert segment_ids(np.array([5]), 1).tolist() == [0]


def test_segment_lengths_and_split():
    ep = np.array([1, 2, 3, 10, 11, 20])
    ids = segment_ids(ep, 1)
    assert segment_lengths(ids).tolist() == [3, 2, 1]
    parts = split_segments(ep, ids)
    assert [p.tolist() for p in parts] == [[1, 2, 3], [10, 11], [20]]


def test_unsorted_input_is_rejected_not_silently_sorted():
    with pytest.raises(ValueError):
        segment_ids(np.array([3, 1, 2]), 1)
    with pytest.raises(ValueError):
        segment_ids(np.array([1, 1, 2]), 1)  # duplicates must be removed first


DAY = 86400
BASE = 1_793_404_800  # 2026-10-31T00:00:00Z


def test_day_rows_split_by_utc_day_and_count_gaps():
    # day 1: ticks 1 s apart for 100 s starting 1000 s into the day, then a 50 s hole, then 50 more
    start = BASE + 1000
    ep = list(range(start, start + 100)) + list(range(start + 150, start + 200))
    # day 2: 30 ticks right after midnight
    ep += list(range(BASE + DAY, BASE + DAY + 30))
    rows = day_rows(ep, interval=1, since=BASE + 1000 - 1, until=BASE + DAY + 29)
    assert [r.day for r in rows] == ["2026-10-31", "2026-11-01"]
    d1, d2 = rows
    assert d1.ticks == 150 and d1.gap_count == 1 and d1.missing == 50 and d1.longest_gap_s == 51
    assert d2.ticks == 30 and d2.gap_count == 0
    assert 0 < d1.uptime < 0.01 and d2.uptime > 0          # the day counts every nominal slot in range, not just the run


def test_coverage_summary_counts_only_segments_of_min_length():
    ep = list(range(100, 160)) + list(range(200, 205)) + list(range(300, 330))
    s = coverage_summary(ep, interval=1, min_segment_len=10)
    assert s.ticks == 95 and s.segments == 3 and s.valid_segments == 2
    assert s.ticks_in_valid_segments == 90 and s.longest_segment == 60
    assert s.contiguous_fraction == pytest.approx(90 / 95)


def test_coverage_summary_two_second_symbol():
    ep = list(range(0, 40, 2)) + list(range(100, 120, 2))
    s = coverage_summary(ep, interval=2, min_segment_len=10)
    assert s.segments == 2 and s.valid_segments == 2 and s.ticks_in_valid_segments == 30


def test_coverage_summary_empty():
    s = coverage_summary([], interval=1, min_segment_len=10)
    assert s.ticks == 0 and s.contiguous_fraction == 0.0


def test_coverage_tooling_never_selects_quote_values():
    """The pre-run rule (A1.4) allows epochs, counts and gaps only. Guard the SQL of the epoch-only tools."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    for rel in ("scripts/coverage_report.py", "src/derivbot/collector/report.py"):
        sql = re.findall(r"SELECT[^\"']*", (root / rel).read_text(), flags=re.I)
        assert sql or rel.endswith("report.py")
        for q in sql:
            assert "quote" not in q.lower() and "*" not in q, (rel, q)
