"""Holdout start H0, computed mechanically from git (kill-criterion Amendment 1, A1.4).

H0 is the committer timestamp (Unix seconds) of the first commit whose docs/kill-criterion.md contains the marker.
Ticks are eligible only if their server epoch is strictly greater than H0.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

MARKER = "AMENDMENT-1-MARKER"


def holdout_start_epoch(repo: str | Path, marker: str = MARKER) -> int:
    out = subprocess.run(
        ["git", "log", "--reverse", "--format=%ct", f"-S{marker}", "--", "docs/kill-criterion.md"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.split()
    if not out:
        raise ValueError(f"no commit contains {marker}; the amendment is not committed")
    return int(out[0])
