"""Real-money guard (HANDBOOK 11.4). All five conditions must hold and none is enough alone:

1. TRADING_MODE=real in config            2. ALLOW_REAL_TRADING=true in env
3. REAL_TRADING_APPROVED file with the owner's approval date and the git commit it approves (checked against the code
   actually deployed)                      4. real credentials present, and the real account id maps to the real endpoint
5. the live readiness checklist (docs/live-readiness.md) fully ticked and signed by the owner.

On top of that sits a BUILD LOCK: REAL_TRADING_IMPLEMENTED is False, so even a world where all five hold resolves to
demo. Unlocking real trading is a code change that the owner makes deliberately, after the independent review. Agents
never set conditions 1 to 3 and never touch this constant.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from derivbot.config.settings import Settings

REAL_TRADING_IMPLEMENTED = True    # opened 2026-11-06 at the owner's explicit instruction in chat; the other layers still apply
MIN_CHECKLIST_ITEMS = 5


class Mode(str, Enum):
    DEMO = "demo"
    REAL = "real"


_PROOF = object()           # known only to this module: the stamp that resolve_mode puts on a REAL decision


@dataclass(frozen=True)
class ModeDecision:
    mode: Mode
    failed: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    _proof: object = field(default=None, repr=False, compare=False)

    @property
    def real_unlocked(self) -> bool:
        """True only for a decision that resolve_mode itself issued as REAL. A hand-built ModeDecision(Mode.REAL) is not unlocked."""
        return self.mode is Mode.REAL and self._proof is _PROOF


def _approval_ok(repo_root: Path, head_commit: str | None) -> bool:
    f = repo_root / "REAL_TRADING_APPROVED"
    if not f.is_file() or not head_commit:
        return False
    kv: dict[str, str] = {}
    for line in f.read_text().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            kv[k.strip().lower()] = v.strip()
    try:
        dt.date.fromisoformat(kv.get("approved_on", ""))
    except ValueError:
        return False
    commit = kv.get("commit", "").lower()
    return (bool(kv.get("approved_by")) and len(commit) >= 7 and re.fullmatch(r"[0-9a-f]+", commit) is not None
            and head_commit.lower().startswith(commit))


def _checklist_ok(repo_root: Path) -> bool:
    f = repo_root / "docs" / "live-readiness.md"
    if not f.is_file():
        return False
    text = f.read_text()
    ticked = len(re.findall(r"^- \[[xX]\] ", text, re.M))
    unticked = len(re.findall(r"^- \[ \] ", text, re.M))
    sign = re.search(r"^Owner sign-off:\s+(\S.*)$", text, re.M)
    return (ticked >= MIN_CHECKLIST_ITEMS and unticked == 0 and sign is not None
            and re.search(r"\d{4}-\d{2}-\d{2}", sign.group(1)) is not None)


def _real_credentials_ok(s: Settings, otp_endpoint_mode: str | None) -> bool:
    return bool(s.deriv_app_id_real and s.deriv_pat_real and s.deriv_pat_real.get_secret_value()
                and s.deriv_account_id_real and otp_endpoint_mode == "real")


def resolve_mode(s: Settings, *, repo_root: str | Path, head_commit: str | None, otp_endpoint_mode: str | None,
                 build_lock: bool = True) -> ModeDecision:
    root = Path(repo_root)
    checks = {
        "mode_is_real": s.trading_mode == "real",
        "allow_flag": s.allow_real_trading is True,
        "approval_file": _approval_ok(root, head_commit),
        "real_credentials_and_endpoint": _real_credentials_ok(s, otp_endpoint_mode),
        "checklist": _checklist_ok(root),
    }
    failed = [k for k, ok in checks.items() if not ok]
    if failed:
        return ModeDecision(Mode.DEMO, failed, [f"condition not met: {k}" for k in failed])
    if build_lock and not REAL_TRADING_IMPLEMENTED:
        return ModeDecision(Mode.DEMO, [], ["real trading is not implemented in this build (build lock)"])
    return ModeDecision(Mode.REAL, [], [], _PROOF)
