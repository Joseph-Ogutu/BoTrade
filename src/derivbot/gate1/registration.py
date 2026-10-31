"""Machine-readable Gate 1 registration, parsed from the fenced JSON blocks in docs/kill-criterion.md.

The document is the single source of truth: the harness reads its parameters from here and never from constants of
its own, so the code cannot drift from what was pre-registered. Amendment blocks are merged in order; a later block
only adds or overrides the keys it names.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

BLOCK = re.compile(r"```json (GATE1-REGISTRATION(?:-A\d+)?)\n(.*?)\n```", re.S)
DEFAULT_PATH = Path(__file__).resolve().parents[3] / "docs" / "kill-criterion.md"


def load_registration(path: str | Path = DEFAULT_PATH) -> dict[str, Any]:
    text = Path(path).read_text()
    blocks = BLOCK.findall(text)
    names = [n for n, _ in blocks]
    if names.count("GATE1-REGISTRATION") != 1:
        raise ValueError(f"expected exactly one GATE1-REGISTRATION block, found {names.count('GATE1-REGISTRATION')}")
    merged: dict[str, Any] = {}
    amendments: list[int] = []
    for name, body in blocks:
        data = json.loads(body)
        amendments.append(int(data["amendment"]))
        merged.update({k: v for k, v in data.items() if k != "amendment"})
    merged["amendments"] = amendments
    return merged
