"""Loading real ticks for the ONE Gate 1 run, behind a permit.

This is the only module that reads quote values (digits) from a database. It refuses to run unless a RunPermit is
supplied, and a permit can only be obtained after the registered run date AND with a harness commit frozen in
docs/gates.md whose files have not changed since (kill-criterion A1.8, A1.9). Tests use synthetic databases and a
test-only permit.
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from derivbot.gate1.holdout import holdout_start_epoch
from derivbot.gate1.pipeline import Series

_TOKEN = object()
FROZEN_FILES = ("src/derivbot/gate1", "src/derivbot/stats", "scripts/run_gate1.py")
FROZEN_ROW = re.compile(r"\|\s*Harness commit \(frozen\)\s*\|\s*`([0-9a-f]{7,40})`")


class GateNotOpen(Exception):
    pass


@dataclass(frozen=True)
class RunPermit:
    reason: str
    _token: object = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self._token is not _TOKEN:
            raise GateNotOpen("a RunPermit can only be created by permit_real_run() or the test helper")


def test_only_permit() -> RunPermit:
    """For unit tests on synthetic databases. Must never be used by a script that reads real data."""
    return RunPermit("test only", _TOKEN)


def check_permit_conditions(now_epoch: int, cutoff_epoch: int, frozen_hash: str | None, frozen_files_unchanged: bool) -> None:
    if now_epoch < cutoff_epoch:
        raise GateNotOpen(f"the Gate 1 run date has not been reached (now {now_epoch} < cutoff {cutoff_epoch})")
    if not frozen_hash:
        raise GateNotOpen("no frozen harness commit is recorded in docs/gates.md")
    if not frozen_files_unchanged:
        raise GateNotOpen("harness files changed since the frozen commit; the run must use exactly the frozen code")


def permit_real_run(repo: str | Path, reg: dict, now_epoch: int | None = None) -> RunPermit:
    repo = Path(repo)
    gates = (repo / "docs" / "gates.md").read_text()
    m = FROZEN_ROW.search(gates)
    frozen = m.group(1) if m else None
    unchanged = False
    if frozen:
        diff = subprocess.run(["git", "diff", "--quiet", frozen, "--", *FROZEN_FILES], cwd=repo)
        status = subprocess.run(["git", "status", "--porcelain", "--", *FROZEN_FILES], cwd=repo,
                                capture_output=True, text=True).stdout.strip()
        unchanged = diff.returncode == 0 and not status
    check_permit_conditions(int(time.time()) if now_epoch is None else now_epoch, reg["data_cutoff_epoch"], frozen, unchanged)
    return RunPermit(f"frozen harness {frozen}", _TOKEN)


@dataclass
class LoadReport:
    symbol: str
    rows_read: int = 0
    outside_window: int = 0
    excluded_pip_mismatch: Counter = field(default_factory=Counter)   # by source
    excluded_conflict: int = 0
    duplicates_agreeing: int = 0
    eligible: int = 0


def _decimals(text: str) -> int:
    return len(text.split(".")[1]) if "." in text else 0


def load_series(db_paths: list[str], symbol: str, h0: int, cutoff: int, permit: RunPermit) -> tuple[Series, LoadReport]:
    """Merge ticks of one symbol from several databases (A2.4) and apply the eligibility and validity rules."""
    if not isinstance(permit, RunPermit):
        raise GateNotOpen("load_series requires a RunPermit")
    rep = LoadReport(symbol)
    seen: dict[int, tuple[str, int | None, str]] = {}
    conflicts: set[int] = set()
    for path in db_paths:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            for epoch, quote, pip, source in conn.execute(
                    "SELECT epoch, quote, pip_size, source FROM ticks WHERE symbol = ?", (symbol,)):
                rep.rows_read += 1
                if not (h0 < epoch <= cutoff):
                    rep.outside_window += 1
                    continue
                if epoch in seen:
                    if seen[epoch][0] == quote:
                        rep.duplicates_agreeing += 1
                    else:
                        conflicts.add(epoch)
                    continue
                seen[epoch] = (quote, pip, source)
        finally:
            conn.close()
    epochs, digits = [], []
    for epoch in sorted(seen):
        quote, pip, source = seen[epoch]
        if epoch in conflicts:
            rep.excluded_conflict += 1
            continue
        if pip is None or _decimals(quote) != pip:
            rep.excluded_pip_mismatch[source] += 1
            continue
        epochs.append(epoch)
        digits.append(int(quote[-1]))
    rep.eligible = len(epochs)
    return Series(symbol, np.asarray(epochs, dtype=np.int64), np.asarray(digits, dtype=np.int8)), rep
