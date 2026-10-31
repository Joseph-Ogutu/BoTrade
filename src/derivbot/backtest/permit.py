"""Owner approval gate for any backtest on REAL collected ticks.

Rule (integrity rules of the build phase): no real-data run happens until Gate 1 has reported and the owner has
approved. Approval is a line in docs/approvals.md written by the owner, in exactly this form:

    REAL_DATA_BACKTEST_APPROVED: 2026-11-16 by <owner name> gate1=<OUTCOME>

and it only counts if a Gate 1 run record (docs/evidence/gate1_RUN_*.json) exists whose outcome equals <OUTCOME>.
Agents never write that line.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

LINE = re.compile(r"^REAL_DATA_BACKTEST_APPROVED: (\d{4}-\d{2}-\d{2}) by (\S.*?) gate1=([A-Z_]+)\s*$", re.M)


class RealDataNotApproved(Exception):
    pass


@dataclass(frozen=True)
class Approval:
    date: str
    owner: str
    gate1_outcome: str


def require_real_data_approval(repo: str | Path) -> Approval:
    repo = Path(repo)
    f = repo / "docs" / "approvals.md"
    if not f.exists():
        raise RealDataNotApproved("docs/approvals.md does not exist")
    m = LINE.search(f.read_text())
    if not m:
        raise RealDataNotApproved("no REAL_DATA_BACKTEST_APPROVED line in docs/approvals.md")
    runs = sorted((repo / "docs" / "evidence").glob("gate1_RUN_*.json"))
    if not runs:
        raise RealDataNotApproved("Gate 1 has not been run: no docs/evidence/gate1_RUN_*.json")
    outcome = json.loads(runs[-1].read_text()).get("outcome")
    if outcome != m.group(3):
        raise RealDataNotApproved(f"approval says gate1={m.group(3)} but the Gate 1 record says {outcome}")
    return Approval(m.group(1), m.group(2), m.group(3))


def load_real_series(db_path: str, symbol: str, since_epoch: int, until_epoch: int, approval: Approval):
    """The only path from the tick store into a backtest. Requires an Approval object, which only the check above makes."""
    import sqlite3

    import numpy as np

    from derivbot.backtest.series import TickSeries

    if not isinstance(approval, Approval):
        raise RealDataNotApproved("an Approval is required")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT epoch, quote, pip_size FROM ticks WHERE symbol = ? AND epoch > ? AND epoch <= ? "
                            "AND pip_size IS NOT NULL ORDER BY epoch", (symbol, since_epoch, until_epoch)).fetchall()
    finally:
        conn.close()
    if not rows:
        raise ValueError("no ticks in range")
    pip = int(rows[0][2])
    return TickSeries.from_quotes(symbol, np.array([r[0] for r in rows]), np.array([float(r[1]) for r in rows]), pip)
