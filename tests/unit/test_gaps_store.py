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
