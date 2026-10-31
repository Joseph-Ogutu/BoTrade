from decimal import Decimal

from derivbot.collector.gaps import Gap, find_gaps
from derivbot.collector.store import SqliteTickStore
from derivbot.deriv.messages import Tick


def tick(epoch, quote="100.10", symbol="1HZ100V"):
    return Tick(symbol=symbol, epoch=epoch, quote=Decimal(quote), pip_size=2)


def test_no_gaps_when_continuous():
    assert find_gaps([1, 2, 3, 4, 5], expected_interval=1) == []


def test_gap_detected_with_missing_estimate():
    gaps = find_gaps([1, 2, 3, 10, 11], expected_interval=1)
    assert gaps == [Gap(start_epoch=3, end_epoch=10, missing=6)]


def test_tolerance_allows_small_jitter():
    # 1-second index, one tick arriving 2s later is within the default tolerance factor of 2
    assert find_gaps([1, 2, 4, 5], expected_interval=1) == []
    assert len(find_gaps([1, 2, 5, 6], expected_interval=1)) == 1


def test_two_second_symbol():
    assert find_gaps([0, 2, 4, 6], expected_interval=2) == []
    assert find_gaps([0, 2, 12], expected_interval=2) == [Gap(2, 12, 4)]


def test_unsorted_and_duplicate_epochs_are_handled():
    assert find_gaps([3, 1, 2, 2, 3], expected_interval=1) == []


def test_empty_and_single():
    assert find_gaps([], 1) == [] and find_gaps([5], 1) == []


def test_store_insert_dedupes_on_symbol_epoch(tmp_path):
    store = SqliteTickStore(tmp_path / "t.sqlite")
    assert store.insert_many([tick(1), tick(2), tick(2), tick(3)]) == 3
    assert store.insert_many([tick(3), tick(4)]) == 1
    assert store.count("1HZ100V") == 4
    store.close()


def test_store_preserves_exact_decimal_quote(tmp_path):
    store = SqliteTickStore(tmp_path / "t.sqlite")
    store.insert_many([tick(1, quote="1234.56780000")])
    assert store.epochs_and_quotes("1HZ100V")[0] == (1, Decimal("1234.56780000"))
    store.close()


def test_store_last_epoch_and_range_per_symbol(tmp_path):
    store = SqliteTickStore(tmp_path / "t.sqlite")
    store.insert_many([tick(5), tick(9), tick(7, symbol="R_100")])
    assert store.last_epoch("1HZ100V") == 9
    assert store.last_epoch("nope") is None
    assert store.epoch_range("1HZ100V") == (5, 9)
    store.close()


def test_store_survives_reopen(tmp_path):
    p = tmp_path / "t.sqlite"
    s1 = SqliteTickStore(p)
    s1.insert_many([tick(1), tick(2)])
    s1.close()
    s2 = SqliteTickStore(p)
    assert s2.count("1HZ100V") == 2
    s2.close()


# ---- trailing zeros: the digit rule needs the quote at exactly pip_size decimals (kill-criterion A1.4) -------------

def _tick(q, pip=2, epoch=1, symbol="1HZ100V"):
    return Tick(symbol=symbol, epoch=epoch, quote=Decimal(q), pip_size=pip)


def test_store_pads_trailing_zeros_to_pip_size(tmp_path):
    store = SqliteTickStore(tmp_path / "t.sqlite")
    store.insert_many([_tick("1310.7", epoch=1), _tick("1310", epoch=2), _tick("1310.75", epoch=3),
                       _tick("0.5", pip=3, epoch=4)])
    raw = [r[0] for r in store._conn.execute("SELECT quote FROM ticks ORDER BY epoch")]
    assert raw == ["1310.70", "1310.00", "1310.75", "0.500"]
    store.close()


def test_store_never_rounds_a_quote_that_has_more_decimals_than_pip_size(tmp_path):
    store = SqliteTickStore(tmp_path / "t.sqlite")
    store.insert_many([_tick("1.23456", pip=2)])
    assert store._conn.execute("SELECT quote, pip_size FROM ticks").fetchone() == ("1.23456", 2)
    store.close()


def test_store_records_pip_size_per_row(tmp_path):
    store = SqliteTickStore(tmp_path / "t.sqlite")
    store.insert_many([_tick("5.5", pip=1, epoch=1), _tick("5.50", pip=2, epoch=2, symbol="R_100")])
    assert dict(store._conn.execute("SELECT epoch, pip_size FROM ticks")) == {1: 1, 2: 2}
    store.close()


def test_old_database_is_migrated_and_backfill_rows_get_their_pip_size(tmp_path):
    import sqlite3

    p = tmp_path / "old.sqlite"
    c = sqlite3.connect(p)
    c.executescript("""
      CREATE TABLE ticks (symbol TEXT NOT NULL, epoch INTEGER NOT NULL, quote TEXT NOT NULL,
        received_at TEXT NOT NULL DEFAULT 'x', source TEXT NOT NULL DEFAULT 'stream',
        PRIMARY KEY (symbol, epoch)) WITHOUT ROWID;
      INSERT INTO ticks (symbol, epoch, quote, source) VALUES ('A', 1, '10.70', 'backfill');
      INSERT INTO ticks (symbol, epoch, quote, source) VALUES ('A', 2, '10.7', 'stream');
    """)
    c.commit()
    c.close()
    store = SqliteTickStore(p)
    rows = dict(store._conn.execute("SELECT epoch, pip_size FROM ticks"))
    assert rows[1] == 2          # backfill rows were quantised to pip_size decimals, so the count is recoverable
    assert rows[2] is None       # stream rows from before the fix lost trailing zeros: pip_size unknown, never guessed
    store.close()
