import gzip
from decimal import Decimal

import pytest

from derivbot.collector.backup import BackupError, create_backup, prune_backups, restore_backup, verify_backup
from derivbot.collector.store import SqliteTickStore
from derivbot.deriv.messages import Tick


def make_db(path, n=50):
    store = SqliteTickStore(path)
    store.insert_many([Tick(symbol="1HZ100V", epoch=1000 + i, quote=Decimal("100.5"), pip_size=2) for i in range(n)])
    store.insert_many([Tick(symbol="R_100", epoch=2000 + i, quote=Decimal("200.5"), pip_size=2) for i in range(n // 2)])
    store.close()


def test_backup_is_consistent_compressed_and_verifiable(tmp_path):
    db = tmp_path / "ticks.sqlite"
    make_db(db)
    out = create_backup(db, tmp_path / "bk", stamp="20261101T000000Z")
    assert out.name == "ticks-20261101T000000Z.sqlite.gz"
    assert (tmp_path / "bk" / (out.name + ".sha256")).exists()
    info = verify_backup(out)
    assert info.integrity_ok and info.rows == {"1HZ100V": 50, "R_100": 25}


def test_backup_works_while_db_is_open_for_writing(tmp_path):
    db = tmp_path / "ticks.sqlite"
    make_db(db)
    live = SqliteTickStore(db)  # collector still has it open
    live.insert_many([Tick(symbol="1HZ100V", epoch=9999, quote=Decimal("1"), pip_size=2)])
    out = create_backup(db, tmp_path / "bk", stamp="s1")
    live.close()
    assert verify_backup(out).rows["1HZ100V"] == 51


def test_restore_round_trip_preserves_every_row_and_quote(tmp_path):
    db = tmp_path / "ticks.sqlite"
    make_db(db)
    out = create_backup(db, tmp_path / "bk", stamp="s1")
    target = tmp_path / "restored" / "ticks.sqlite"
    restore_backup(out, target)
    a, b = SqliteTickStore(db), SqliteTickStore(target)
    assert a.epochs_and_quotes("1HZ100V") == b.epochs_and_quotes("1HZ100V")
    assert a.epochs_and_quotes("R_100") == b.epochs_and_quotes("R_100")
    a.close()
    b.close()


def test_restore_refuses_to_overwrite_existing_file(tmp_path):
    db = tmp_path / "ticks.sqlite"
    make_db(db)
    out = create_backup(db, tmp_path / "bk", stamp="s1")
    with pytest.raises(BackupError):
        restore_backup(out, db)
    assert SqliteTickStore(db).count("1HZ100V") == 50


def test_corrupted_backup_is_detected_before_restore(tmp_path):
    db = tmp_path / "ticks.sqlite"
    make_db(db)
    out = create_backup(db, tmp_path / "bk", stamp="s1")
    data = bytearray(out.read_bytes())
    data[len(data) // 2] ^= 0xFF
    out.write_bytes(bytes(data))
    with pytest.raises(BackupError):
        restore_backup(out, tmp_path / "r" / "ticks.sqlite")
    assert not (tmp_path / "r" / "ticks.sqlite").exists()


def test_missing_checksum_file_fails_verification(tmp_path):
    db = tmp_path / "ticks.sqlite"
    make_db(db)
    out = create_backup(db, tmp_path / "bk", stamp="s1")
    (tmp_path / "bk" / (out.name + ".sha256")).unlink()
    with pytest.raises(BackupError):
        verify_backup(out)


def test_non_sqlite_payload_fails_integrity(tmp_path):
    bad = tmp_path / "bk" / "ticks-x.sqlite.gz"
    bad.parent.mkdir()
    with gzip.open(bad, "wb") as f:
        f.write(b"this is not a database")
    import hashlib

    (tmp_path / "bk" / (bad.name + ".sha256")).write_text(hashlib.sha256(bad.read_bytes()).hexdigest() + "  " + bad.name)
    with pytest.raises(BackupError):
        verify_backup(bad)


def test_prune_keeps_newest_n(tmp_path):
    db = tmp_path / "ticks.sqlite"
    make_db(db)
    for s in ("20261101", "20261102", "20261103", "20261104"):
        create_backup(db, tmp_path / "bk", stamp=s)
    removed = prune_backups(tmp_path / "bk", keep=2)
    left = sorted(p.name for p in (tmp_path / "bk").glob("*.gz"))
    assert left == ["ticks-20261103.sqlite.gz", "ticks-20261104.sqlite.gz"]
    assert len(removed) == 2
    assert not list((tmp_path / "bk").glob("*20261101*"))


def test_backup_source_missing_raises(tmp_path):
    with pytest.raises(BackupError):
        create_backup(tmp_path / "nope.sqlite", tmp_path / "bk", stamp="s")
