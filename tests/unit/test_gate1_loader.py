import sqlite3

import numpy as np
import pytest

from derivbot.gate1.loader import (
    GateNotOpen,
    RunPermit,
    check_permit_conditions,
    load_series,
    test_only_permit as make_test_permit,   # alias keeps pytest from collecting the helper as a test
)

H0, CUT = 1000, 5000


def make_db(path, rows):
    c = sqlite3.connect(path)
    c.executescript("""CREATE TABLE ticks (symbol TEXT, epoch INTEGER, quote TEXT, received_at TEXT DEFAULT 'x',
                       source TEXT DEFAULT 'stream', pip_size INTEGER, PRIMARY KEY (symbol, epoch));""")
    c.executemany("INSERT INTO ticks (symbol, epoch, quote, source, pip_size) VALUES (?,?,?,?,?)", rows)
    c.commit()
    c.close()
    return str(path)


def test_permit_cannot_be_constructed_directly():
    with pytest.raises(GateNotOpen):
        RunPermit("sneaky")
    with pytest.raises(GateNotOpen):
        load_series([], "A", H0, CUT, permit=None)


def test_permit_conditions():
    check_permit_conditions(CUT, CUT, "abc1234", True)            # at the cutoff: open
    with pytest.raises(GateNotOpen, match="run date"):
        check_permit_conditions(CUT - 1, CUT, "abc1234", True)
    with pytest.raises(GateNotOpen, match="no frozen"):
        check_permit_conditions(CUT, CUT, None, True)
    with pytest.raises(GateNotOpen, match="changed"):
        check_permit_conditions(CUT, CUT, "abc1234", False)


def test_eligibility_window_is_strictly_after_h0_and_inclusive_of_cutoff(tmp_path):
    db = make_db(tmp_path / "a.sqlite", [("A", H0, "1.10", "stream", 2), ("A", H0 + 1, "1.11", "stream", 2),
                                         ("A", CUT, "1.12", "stream", 2), ("A", CUT + 1, "1.13", "stream", 2)])
    s, rep = load_series([db], "A", H0, CUT, make_test_permit())
    assert s.epochs.tolist() == [H0 + 1, CUT] and s.digits.tolist() == [1, 2]
    assert rep.outside_window == 2


def test_digit_is_the_last_character_including_a_trailing_zero(tmp_path):
    db = make_db(tmp_path / "a.sqlite", [("A", 2000, "1310.70", "stream", 2), ("A", 2001, "1310.75", "stream", 2),
                                         ("A", 2002, "0.500", "stream", 3)])
    s, _ = load_series([db], "A", H0, CUT, make_test_permit())
    assert s.digits.tolist() == [0, 5, 0]


def test_ticks_whose_decimals_do_not_equal_pip_size_or_have_unknown_pip_are_excluded_and_counted(tmp_path):
    db = make_db(tmp_path / "a.sqlite", [("A", 2000, "1310.7", "stream", 2),      # lost its zero
                                         ("A", 2001, "1310.75", "stream", None),    # pip unknown
                                         ("A", 2002, "1.23456", "backfill", 2),     # more decimals than pip
                                         ("A", 2003, "9.99", "stream", 2)])
    s, rep = load_series([db], "A", H0, CUT, make_test_permit())
    assert s.epochs.tolist() == [2003]
    assert rep.excluded_pip_mismatch == {"stream": 2, "backfill": 1}


def test_merge_across_databases_agreeing_duplicates_count_once_and_conflicts_are_excluded(tmp_path):
    a = make_db(tmp_path / "a.sqlite", [("A", 2000, "1.10", "stream", 2), ("A", 2001, "1.11", "stream", 2),
                                        ("A", 2002, "1.12", "stream", 2)])
    b = make_db(tmp_path / "b.sqlite", [("A", 2001, "1.11", "backfill", 2), ("A", 2002, "9.99", "backfill", 2),
                                        ("A", 2003, "1.13", "backfill", 2)])
    s, rep = load_series([a, b], "A", H0, CUT, make_test_permit())
    assert s.epochs.tolist() == [2000, 2001, 2003]
    assert rep.duplicates_agreeing == 1 and rep.excluded_conflict == 1 and rep.eligible == 3


def test_other_symbols_are_not_mixed_in(tmp_path):
    db = make_db(tmp_path / "a.sqlite", [("A", 2000, "1.10", "stream", 2), ("B", 2001, "2.20", "stream", 2)])
    s, _ = load_series([db], "A", H0, CUT, make_test_permit())
    assert s.epochs.tolist() == [2000]
