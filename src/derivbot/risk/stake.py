"""Stake sizing: a fixed fraction of the current balance, capped, rounded DOWN to cents (HANDBOOK 10.2).

Properties (tested with hypothesis):
- never above max_stake_abs, never above balance * risk_per_trade_pct, never above the balance;
- non-decreasing in the balance, so after a loss (balance falls) the next stake can only be the same or smaller;
- never raised UP to a minimum: if the computed stake is below the effective minimum there is no trade. (The old bot
  floored the stake up to the minimum, which silently broke its own 2 percent cap on small balances.)
There is no parameter that scales a stake after a loss or a win streak: no martingale, no anti-martingale.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

CENT = Decimal("0.01")


@dataclass(frozen=True)
class StakeResult:
    stake: Decimal | None
    reason: str          # "ok", "balance_unknown", "min_stake_unknown", "below_min"
    raw: Decimal | None = None
    effective_min: Decimal | None = None


def compute_stake(balance: Decimal | None, risk_per_trade_pct: Decimal, max_stake_abs: Decimal,
                  api_min_stake: Decimal | None, min_stake_floor: Decimal | None = None) -> StakeResult:
    if balance is None or balance <= 0:
        return StakeResult(None, "balance_unknown")
    if api_min_stake is None:
        # The exchange minimum comes from the API (UNVERIFIED for the demo account). Unknown means no trade.
        return StakeResult(None, "min_stake_unknown")
    effective_min = max(api_min_stake, min_stake_floor) if min_stake_floor is not None else api_min_stake
    raw = (balance * risk_per_trade_pct).quantize(CENT, rounding=ROUND_DOWN)
    stake = min(raw, max_stake_abs.quantize(CENT, rounding=ROUND_DOWN), balance.quantize(CENT, rounding=ROUND_DOWN))
    if stake < effective_min:
        return StakeResult(None, "below_min", raw, effective_min)
    return StakeResult(stake, "ok", raw, effective_min)
