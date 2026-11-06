"""Owner approval and parameter lock for the live demo forward test (Amendment 5).

Two things must hold before the forward test may run, and both are checked every time the service builds:
1. docs/approvals.md has a line written by the owner, in exactly this form (agents write it only when the owner instructs it in
   plain words, and the line records that):

       LIVE_DEMO_FORWARD_TEST_APPROVED: <YYYY-MM-DD> by <owner> amendment=5

   and docs/kill-criterion.md contains Amendment 5 with its registration block.
2. The strategies in the code are exactly the registered ones: docs/forward_test_lock.json holds every strategy's id, version and
   parameters as they were when the test was registered. If any parameter, version or strategy changes, the test refuses to start:
   a change is a NEW variant and needs a new amendment (A5.3), never a quiet edit.

It does not unlock reading stored ticks: derivbot.backtest.permit is untouched and still refuses without Gate 1.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from derivbot.gate1.registration import load_registration
from derivbot.signals.base import Strategy

LINE = re.compile(r"^LIVE_DEMO_FORWARD_TEST_APPROVED: (\d{4}-\d{2}-\d{2}) by (\S.*?) amendment=5\s*$", re.M)
MARKER = "AMENDMENT-5-MARKER"


class ForwardTestNotApproved(Exception):
    pass


@dataclass(frozen=True)
class ForwardApproval:
    date: str
    owner: str
    registration: dict[str, Any]


def lock_of(strategies: dict[str, Strategy]) -> dict[str, dict[str, Any]]:
    return {key: {"id": s.id, "version": s.version, "duration_ticks": s.duration_ticks, "warmup_ticks": s.warmup_ticks,
                  "params": json.loads(json.dumps(s.params, sort_keys=True, default=str))}
            for key, s in sorted(strategies.items())}


def require_forward_test_approval(repo: str | Path, strategies: dict[str, Strategy]) -> ForwardApproval:
    repo = Path(repo)
    approvals = repo / "docs" / "approvals.md"
    if not approvals.exists():
        raise ForwardTestNotApproved("docs/approvals.md does not exist")
    m = LINE.search(approvals.read_text())
    if not m:
        raise ForwardTestNotApproved("no LIVE_DEMO_FORWARD_TEST_APPROVED line in docs/approvals.md")
    criterion = repo / "docs" / "kill-criterion.md"
    if not criterion.exists() or MARKER not in criterion.read_text():
        raise ForwardTestNotApproved("Amendment 5 is not in docs/kill-criterion.md")
    ft = load_registration(criterion).get("forward_test")
    if not ft:
        raise ForwardTestNotApproved("Amendment 5 has no registration block")
    if sorted(ft["strategies"]) != sorted(strategies):
        raise ForwardTestNotApproved("the strategies in the code differ from the registered variants (a change needs a new amendment)")
    lock_file = repo / "docs" / "forward_test_lock.json"
    if not lock_file.exists():
        raise ForwardTestNotApproved("docs/forward_test_lock.json is missing")
    if json.loads(lock_file.read_text()) != lock_of(strategies):
        raise ForwardTestNotApproved("a strategy parameter, version or definition differs from the locked registration: a change is a "
                                     "NEW variant and needs a new amendment")
    return ForwardApproval(m.group(1), m.group(2), ft)
