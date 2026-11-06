"""Automatic strategy approval from the recorded forward-test result (Amendment 5).

A strategy is added to config/strategies.json only when the forward report has RECORDED a PASS for one of its variants and the
evaluation was not voided by a control that rejected. It reads the recorded evaluation; it never computes one, never evaluates
early, and never removes an approval. The decision rule stays in forward/report.py (decide) exactly as registered.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, text

ROOT = Path(__file__).resolve().parents[3]


def passed_strategies(conn: Connection, lock: dict[str, Any]) -> dict[str, str]:
    """strategy id -> version, for every registered strategy with a recorded, non-void PASS on at least one variant."""
    rows = conn.execute(text("SELECT ref_id, payload FROM audit_events WHERE event_type = 'FORWARD_EVALUATION' ORDER BY id")).all()
    out: dict[str, str] = {}
    for ref_id, payload in rows:
        name = str(ref_id).split(":", 1)[0]
        if payload.get("passed") is True and payload.get("void") is False and name in lock:
            out[str(lock[name]["id"])] = str(lock[name]["version"])
    return out


def promote(conn: Connection, lock: dict[str, Any], strategies_path: Path) -> dict[str, str]:
    """Add newly passed strategies to the approvals file. Returns the ones added. Existing entries are kept as they are."""
    current = json.loads(strategies_path.read_text()) if strategies_path.exists() else {"approved": {}}
    approved = dict(current.get("approved", {}))
    added = {k: v for k, v in passed_strategies(conn, lock).items() if k not in approved}
    if added:
        approved.update(added)
        strategies_path.write_text(json.dumps({"approved": approved}, indent=2, sort_keys=True) + "\n")     # in place: the file may be a bind mount
    return added


def main() -> int:  # pragma: no cover - exercised on the server
    from derivbot.config.settings import load_settings, parse_env_file
    from derivbot.db.engine import make_engine

    ap = argparse.ArgumentParser(description="Approve strategies whose forward-test PASS is recorded.")
    ap.add_argument("--strategies", default=str(ROOT / "config" / "strategies.json"), help="approvals file to update")
    args = ap.parse_args()
    env = dict(os.environ)
    if os.environ.get("ENV_FILE"):
        env = {**parse_env_file(os.environ["ENV_FILE"]), **env}
    engine = make_engine(load_settings(env).database_url.get_secret_value())
    lock = json.loads((ROOT / "docs" / "forward_test_lock.json").read_text())
    with engine.connect() as conn:
        added = promote(conn, lock, Path(args.strategies))
    print("approved now: " + (", ".join(f"{k} v{v}" for k, v in added.items()) or "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
