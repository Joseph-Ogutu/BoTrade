"""Consistent backup, verify and restore of the collector's SQLite file.

Backups use SQLite's online backup API, so they are consistent even while the collector is writing.
Each backup is gzip-compressed with a SHA-256 sidecar. Restore verifies the checksum and runs
PRAGMA integrity_check before touching the target, and refuses to overwrite an existing file.

CLI: python -m derivbot.collector.backup create|verify|restore ...
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


class BackupError(Exception):
    pass


@dataclass(frozen=True)
class BackupInfo:
    integrity_ok: bool
    rows: dict[str, int]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def create_backup(db_path: str | Path, out_dir: str | Path, stamp: str | None = None) -> Path:
    db_path, out_dir = Path(db_path), Path(out_dir)
    if not db_path.exists():
        raise BackupError(f"source database not found: {db_path}")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final = out_dir / f"{db_path.stem}-{stamp}.sqlite.gz"
    with tempfile.TemporaryDirectory(dir=out_dir) as tmp:
        snap = Path(tmp) / "snapshot.sqlite"
        src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        dst = sqlite3.connect(snap)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        partial = out_dir / (final.name + ".partial")
        with open(snap, "rb") as fin, gzip.open(partial, "wb", compresslevel=6) as fout:
            shutil.copyfileobj(fin, fout)
        os.replace(partial, final)
    (out_dir / (final.name + ".sha256")).write_text(f"{_sha256(final)}  {final.name}\n")
    return final


def _check_sum(backup: Path) -> None:
    sidecar = backup.parent / (backup.name + ".sha256")
    if not sidecar.exists():
        raise BackupError(f"checksum file missing: {sidecar.name}")
    expected = sidecar.read_text().split()[0]
    if _sha256(backup) != expected:
        raise BackupError("checksum mismatch: backup is corrupted or was altered")


def _unpack(backup: Path, dest: Path) -> None:
    try:
        with gzip.open(backup, "rb") as fin, open(dest, "wb") as fout:
            shutil.copyfileobj(fin, fout)
    except (OSError, EOFError) as exc:
        raise BackupError(f"cannot decompress backup: {exc}") from exc


def _inspect(db: Path) -> BackupInfo:
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            rows = dict(conn.execute("SELECT symbol, COUNT(*) FROM ticks GROUP BY symbol").fetchall())
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"not a valid database: {exc}") from exc
    if not ok:
        raise BackupError("integrity_check failed")
    return BackupInfo(integrity_ok=True, rows=rows)


def verify_backup(backup: str | Path) -> BackupInfo:
    backup = Path(backup)
    _check_sum(backup)
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "check.sqlite"
        _unpack(backup, db)
        return _inspect(db)


def restore_backup(backup: str | Path, target: str | Path) -> BackupInfo:
    backup, target = Path(backup), Path(target)
    if target.exists():
        raise BackupError(f"refusing to overwrite existing file: {target}")
    info = verify_backup(backup)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".partial")
    _unpack(backup, partial)
    os.replace(partial, target)
    return info


def prune_backups(out_dir: str | Path, keep: int) -> list[Path]:
    out_dir = Path(out_dir)
    files = sorted(out_dir.glob("*.sqlite.gz"))
    removed = []
    for old in files[:-keep] if keep > 0 else files:
        old.unlink()
        side = out_dir / (old.name + ".sha256")
        if side.exists():
            side.unlink()
        removed.append(old)
    return removed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="derivbot.collector.backup")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--db", default="/data/ticks.sqlite")
    c.add_argument("--out", default="/data/backups")
    c.add_argument("--keep", type=int, default=14)
    v = sub.add_parser("verify")
    v.add_argument("backup")
    r = sub.add_parser("restore")
    r.add_argument("backup")
    r.add_argument("--to", required=True)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "create":
            out = create_backup(args.db, args.out)
            info = verify_backup(out)
            removed = prune_backups(args.out, args.keep)
            print(f"created {out} rows={info.rows} pruned={len(removed)}")
        elif args.cmd == "verify":
            print(f"ok rows={verify_backup(args.backup).rows}")
        else:
            print(f"restored to {args.to} rows={restore_backup(args.backup, args.to).rows}")
    except BackupError as exc:
        print(f"BACKUP ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
