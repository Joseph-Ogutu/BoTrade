"""Pre-trade risk rules (HANDBOOK 10.1). One small pure function per rule so each is testable branch by branch.

Every rule returns a RuleResult; the engine runs ALL of them (no short-circuit) so a rejection records every reason.
Unknown input fails closed: a missing balance, tick age, reconciliation age or account type is a failed rule.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from derivbot.risk.stake import StakeResult


@dataclass(frozen=True)
class RuleResult:
    name: str
    passed: bool
    observed: str
    limit: str
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "passed": self.passed, "observed": self.observed, "limit": self.limit,
                "detail": self.detail}


def _s(v: object) -> str:
    return "unknown" if v is None else str(v)


# 1
def kill_switch_clear(engaged: bool) -> RuleResult:
    return RuleResult("kill_switch_clear", not engaged, "engaged" if engaged else "clear", "clear")


# 2
def mode_and_account_consistent(configured_mode: str, intent_mode: str, endpoint_mode: str | None,
                                account_is_demo: bool | None) -> RuleResult:
    observed = f"configured={configured_mode} intent={intent_mode} endpoint={_s(endpoint_mode)} demo_account={_s(account_is_demo)}"
    if configured_mode != "demo" or intent_mode != "demo":
        return RuleResult("mode_and_account_consistent", False, observed, "demo only", "real mode is unreachable in this build")
    if endpoint_mode != "demo":
        return RuleResult("mode_and_account_consistent", False, observed, "demo only", "connected endpoint is not the demo endpoint")
    if account_is_demo is not True:
        return RuleResult("mode_and_account_consistent", False, observed, "demo only", "account type is not confirmed demo")
    return RuleResult("mode_and_account_consistent", True, observed, "demo only")


# 3
def connection_and_reconciliation_healthy(connection_healthy: bool, last_reconcile_age_s: float | None,
                                          max_age_s: int) -> RuleResult:
    observed = f"connected={connection_healthy} last_reconcile_age_s={_s(last_reconcile_age_s)}"
    limit = f"connected and reconciled within {max_age_s}s"
    if not connection_healthy:
        return RuleResult("connection_and_reconciliation_healthy", False, observed, limit, "connection not healthy")
    if last_reconcile_age_s is None:
        return RuleResult("connection_and_reconciliation_healthy", False, observed, limit, "never reconciled")
    if last_reconcile_age_s > max_age_s:
        return RuleResult("connection_and_reconciliation_healthy", False, observed, limit, "reconciliation too old")
    return RuleResult("connection_and_reconciliation_healthy", True, observed, limit)


# 4
def market_data_fresh(last_tick_age_s: float | None, max_age_s: int) -> RuleResult:
    limit = f"<= {max_age_s}s"
    if last_tick_age_s is None:
        return RuleResult("market_data_fresh", False, "no tick seen", limit, "no market data")
    return RuleResult("market_data_fresh", last_tick_age_s <= max_age_s, f"{last_tick_age_s:.1f}s", limit)


# 5
def open_positions_within_limit(open_positions: int, max_open: int) -> RuleResult:
    return RuleResult("open_positions_within_limit", open_positions < max_open, str(open_positions), f"< {max_open}")


def daily_loss_within_limit(realized_pnl_today: Decimal, start_balance: Decimal | None,
                            max_abs: Decimal | None, max_pct: Decimal | None) -> RuleResult:
    loss = max(Decimal(0), -realized_pnl_today)
    parts = []
    if max_abs is not None:
        parts.append(f"abs {max_abs}")
    if max_pct is not None:
        parts.append(f"{max_pct} of start balance")
    limit = " and ".join(parts) or "none configured"
    observed = f"loss {loss}"
    if max_abs is not None and loss >= max_abs:
        return RuleResult("daily_loss_within_limit", False, observed, limit, "absolute daily loss limit reached")
    if max_pct is not None:
        if start_balance is None or start_balance <= 0:
            return RuleResult("daily_loss_within_limit", False, observed, limit, "start-of-day balance unknown")
        if loss >= max_pct * start_balance:
            return RuleResult("daily_loss_within_limit", False, observed, limit, "percentage daily loss limit reached")
    return RuleResult("daily_loss_within_limit", True, observed, limit)


def consecutive_losses_within_limit(consecutive_losses: int, max_losses: int) -> RuleResult:
    return RuleResult("consecutive_losses_within_limit", consecutive_losses < max_losses, str(consecutive_losses), f"< {max_losses}")


def trade_rate_within_limit(trades_last_hour: int, max_per_hour: int) -> RuleResult:
    return RuleResult("trade_rate_within_limit", trades_last_hour < max_per_hour, str(trades_last_hour), f"< {max_per_hour}")


def trades_per_day_within_limit(trades_today: int, max_per_day: int | None) -> RuleResult:
    """Owner rule: at most N bets per risk day. None means the owner set no daily cap."""
    if max_per_day is None:
        return RuleResult("trades_per_day_within_limit", True, str(trades_today), "no daily cap set")
    return RuleResult("trades_per_day_within_limit", trades_today < max_per_day, str(trades_today), f"< {max_per_day}")


def losses_per_day_within_limit(losses_today: int, max_losses_per_day: int | None) -> RuleResult:
    """Owner rule: stop for the rest of the risk day once N bets have lost. None means no daily stop is set."""
    if max_losses_per_day is None:
        return RuleResult("losses_per_day_within_limit", True, str(losses_today), "no daily stop set")
    return RuleResult("losses_per_day_within_limit", losses_today < max_losses_per_day, str(losses_today), f"< {max_losses_per_day}")


# 6
def stake_within_bounds(result: StakeResult) -> RuleResult:
    observed = f"{_s(result.stake)} ({result.reason})"
    limit = f"min {_s(result.effective_min)}"
    return RuleResult("stake_within_bounds", result.stake is not None, observed, limit,
                      "" if result.stake is not None else result.reason)


# 7
def duplicate_protection(key_already_used: bool, identical_recent_or_in_flight: bool) -> RuleResult:
    observed = f"key_used={key_already_used} identical_recent={identical_recent_or_in_flight}"
    if key_already_used:
        return RuleResult("duplicate_protection", False, observed, "unique key, no identical intent", "idempotency key already used")
    if identical_recent_or_in_flight:
        return RuleResult("duplicate_protection", False, observed, "unique key, no identical intent", "identical intent in flight or recent")
    return RuleResult("duplicate_protection", True, observed, "unique key, no identical intent")


# 8 (evaluated when the proposal arrives)
def proposal_price_acceptable(stake: Decimal, ask_price: Decimal, max_slippage: Decimal) -> RuleResult:
    cap = stake * (1 + max_slippage)
    return RuleResult("proposal_price_acceptable", ask_price <= cap, str(ask_price), f"<= {cap}")


def proposal_payout_acceptable(ask_price: Decimal, payout: Decimal, min_net_ratio: Decimal) -> RuleResult:
    if ask_price <= 0:
        return RuleResult("proposal_payout_acceptable", False, f"ask {ask_price}", f">= {min_net_ratio}", "non-positive ask price")
    ratio = (payout - ask_price) / ask_price
    return RuleResult("proposal_payout_acceptable", ratio >= min_net_ratio, f"{ratio:.6f}", f">= {min_net_ratio}")


# 9
def strategy_approved(approved: bool, strategy_id: str, version: str) -> RuleResult:
    return RuleResult("strategy_approved", approved, f"{strategy_id}@{version}", "approved id and version")
