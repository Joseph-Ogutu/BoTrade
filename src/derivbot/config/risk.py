"""Versioned risk configuration (HANDBOOK 10, 8).

Every owner-set value ships as the literal SET_BY_OWNER and loading fails loudly, naming ALL unset values, until the
owner fills them in. No default can quietly stand in for a risk limit. The validated config is hashed and stored in
config_versions so every risk decision can name the exact limits it applied.

Parameters beyond the handbook table (min_net_payout_ratio, reconcile_max_age_s, recent_duplicate_window_s,
daily_reset_utc_hour) are needed by pre-trade checks 3, 7, 8 and by section 10.3 ("reset at a defined UTC time");
they are ADDITIONS by the implementer and are SET_BY_OWNER like the rest.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

SET_BY_OWNER = "SET_BY_OWNER"


class RiskConfigError(Exception):
    pass


# name -> (kind, required)
_FIELDS: dict[str, tuple[str, bool]] = {
    "risk_per_trade_pct": ("frac", True),            # stake as a fraction of balance, (0, 1]
    "max_stake_abs": ("pos_dec", True),
    "min_stake_abs": ("pos_dec", False),             # optional floor above the API minimum
    "max_daily_loss_abs": ("pos_dec", False),        # one of the two daily loss limits is required
    "max_daily_loss_pct_of_start_balance": ("frac", False),
    "max_consecutive_losses": ("pos_int", True),
    "max_open_positions": ("pos_int", True),
    "max_trades_per_hour": ("pos_int", True),
    "max_trades_per_day": ("pos_int", False),        # optional: buys sent since the daily reset; unset means no daily cap
    "max_losses_per_day": ("pos_int", False),        # optional: settled losses since the daily reset; unset means no daily stop
    "max_proposal_price_slippage": ("nonneg_dec", True),
    "stale_data_max_age_s": ("pos_int", True),
    "min_net_payout_ratio": ("pos_dec", True),
    "reconcile_max_age_s": ("pos_int", True),
    "recent_duplicate_window_s": ("pos_int", True),
    "daily_reset_utc_hour": ("hour", True),
}


class RiskConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    risk_per_trade_pct: Decimal
    max_stake_abs: Decimal
    min_stake_abs: Decimal | None = None
    max_daily_loss_abs: Decimal | None = None
    max_daily_loss_pct_of_start_balance: Decimal | None = None
    max_consecutive_losses: int
    max_open_positions: int
    max_trades_per_hour: int
    max_trades_per_day: int | None = None
    max_losses_per_day: int | None = None
    max_proposal_price_slippage: Decimal
    stale_data_max_age_s: int
    min_net_payout_ratio: Decimal
    reconcile_max_age_s: int
    recent_duplicate_window_s: int
    daily_reset_utc_hour: int


def _is_unset(v: Any) -> bool:
    return v is None or v == SET_BY_OWNER


def _parse(name: str, kind: str, v: Any) -> Any:
    if kind.endswith("dec") or kind == "frac":
        if not isinstance(v, str):
            raise RiskConfigError(f"{name}: money and ratio values must be written as a string, got {type(v).__name__}")
        try:
            d = Decimal(v)
        except InvalidOperation as exc:
            raise RiskConfigError(f"{name}: {v!r} is not a decimal number") from exc
        if not d.is_finite():
            raise RiskConfigError(f"{name}: must be finite")
        if kind == "pos_dec" and d <= 0:
            raise RiskConfigError(f"{name}: must be greater than 0")
        if kind == "nonneg_dec" and d < 0:
            raise RiskConfigError(f"{name}: must be 0 or more")
        if kind == "frac" and not (0 < d <= 1):
            raise RiskConfigError(f"{name}: must be in (0, 1]")
        return d
    if isinstance(v, bool) or not isinstance(v, int):
        raise RiskConfigError(f"{name}: must be an integer")
    if kind == "pos_int" and v <= 0:
        raise RiskConfigError(f"{name}: must be greater than 0")
    if kind == "hour" and not (0 <= v <= 23):
        raise RiskConfigError(f"{name}: must be 0 to 23")
    return v


def parse_risk_config(raw: dict[str, Any]) -> RiskConfig:
    unknown = sorted(set(raw) - set(_FIELDS))
    if unknown:
        raise RiskConfigError(f"unknown keys (typo?): {unknown}")
    unset = [n for n, (_, req) in _FIELDS.items() if req and _is_unset(raw.get(n))]
    if _is_unset(raw.get("max_daily_loss_abs")) and _is_unset(raw.get("max_daily_loss_pct_of_start_balance")):
        unset.append("max_daily_loss_abs or max_daily_loss_pct_of_start_balance (a daily loss limit)")
    if unset:
        raise RiskConfigError(f"{SET_BY_OWNER}: these risk values are unset and must be set by the owner: {unset}")
    values = {n: _parse(n, kind, raw[n]) for n, (kind, _) in _FIELDS.items() if not _is_unset(raw.get(n))}
    try:
        cfg = RiskConfig(**values)
    except ValidationError as exc:  # pragma: no cover - parse() already validated every field
        raise RiskConfigError(str(exc)) from exc
    if cfg.min_stake_abs is not None and cfg.min_stake_abs > cfg.max_stake_abs:
        raise RiskConfigError("min_stake_abs may not exceed max_stake_abs")
    return cfg


def load_risk_config(path: str | Path) -> RiskConfig:
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise RiskConfigError(f"cannot read risk config {path}: {type(exc).__name__}") from exc
    return parse_risk_config(raw)


def canonical(cfg: BaseModel) -> dict[str, Any]:
    """Canonical content: decimals normalised ('5.00' and '5' are the same limit), keys sorted by json.dumps."""
    out: dict[str, Any] = {}
    for k, v in cfg.model_dump().items():
        out[k] = format(v.normalize(), "f") if isinstance(v, Decimal) else v
    return out


def config_hash(cfg: BaseModel) -> str:
    return hashlib.sha256(json.dumps(canonical(cfg), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class StrategyApprovals(BaseModel):
    """Strategy id -> the one approved version. Nothing is approved by default (pre-trade check 9)."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    approved: dict[str, str] = {}

    def is_approved(self, strategy_id: str, version: str) -> bool:
        return self.approved.get(strategy_id) == version


def load_strategy_approvals(path: str | Path) -> StrategyApprovals:
    return StrategyApprovals(**json.loads(Path(path).read_text()))
