"""Stamp raw evidence files with their capture time in UTC.

Evidence files are raw Deriv responses saved as JSON. A top-level `_evidence` block (not part of the server
response) records when it was captured and how we know. The server response keys are never altered.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from derivbot.common.timeutil import parse_utc


class EvidenceError(Exception):
    pass


def stamp_file(path: str | Path, captured_at_utc: str, provenance: str, request: Any = None,
               server_time_utc: str | None = None, note: str | None = None) -> None:
    """Add `_evidence`. If the file is already stamped, the original stamp is kept (never rewritten)."""
    parse_utc(captured_at_utc)
    if server_time_utc is not None:
        parse_utc(server_time_utc)
    path = Path(path)
    data = json.loads(path.read_text())
    if "_evidence" in data:
        return
    block: dict[str, Any] = {"captured_at_utc": captured_at_utc, "provenance": provenance}
    if server_time_utc is not None:
        block["server_time_utc"] = server_time_utc
    if request is not None:
        block["request"] = request
    if note:
        block["note"] = note
    data["_evidence"] = block
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def check_evidence(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    try:
        ev = json.loads(path.read_text()).get("_evidence")
    except ValueError as exc:
        raise EvidenceError(f"{path.name}: not valid JSON") from exc
    if not isinstance(ev, dict):
        raise EvidenceError(f"{path.name}: no _evidence block")
    for key in ("captured_at_utc", "provenance"):
        if key not in ev:
            raise EvidenceError(f"{path.name}: _evidence.{key} missing")
    try:
        parse_utc(ev["captured_at_utc"])
        if "server_time_utc" in ev:
            parse_utc(ev["server_time_utc"])
    except ValueError as exc:
        raise EvidenceError(f"{path.name}: {exc}") from exc
    return ev
