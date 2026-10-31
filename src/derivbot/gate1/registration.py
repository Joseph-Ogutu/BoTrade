"""Machine-readable Gate 1 registration, parsed from the fenced JSON block in docs/kill-criterion.md.

The document is the single source of truth: the harness reads its parameters from here and never from constants of
its own, so the code cannot drift from what was pre-registered.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

BLOCK = re.compile(r"```json GATE1-REGISTRATION\n(.*?)\n```", re.S)
DEFAULT_PATH = Path(__file__).resolve().parents[3] / "docs" / "kill-criterion.md"


def load_registration(path: str | Path = DEFAULT_PATH) -> dict[str, Any]:
    text = Path(path).read_text()
    blocks = BLOCK.findall(text)
    if len(blocks) != 1:
        raise ValueError(f"expected exactly one GATE1-REGISTRATION block, found {len(blocks)}")
    return json.loads(blocks[0])
