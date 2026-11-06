"""Risk engine: the only component that turns a signal into an approved intent (HANDBOOK 6.1, 10).

evaluate() runs every pre-trade rule (no short circuit), returns the stake only when all pass, and carries a snapshot
of the state it judged. check_proposal() is rule 8, run when the proposal arrives. Nothing here touches I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from derivbot.config.risk import RiskConfig, StrategyApprovals
from derivbot.risk import rules
from derivbot.risk.rules import RuleResult
from derivbot.risk.stake import StakeResult, compute_stake


@dataclass(frozen=True)
class IntentRequest:
    idempotency_key: str
    symbol: str
    contract_type: str
    strategy_id: str
    strategy_version: str
    mode: str
    account_id: str


@dataclass(frozen=True)
class RiskContext:
    intent: IntentRequest
    configured_mode: str
    endpoint_mode: str | None          # "demo", "real" or None when not connected / not determined
    account_is_demo: bool | None       # None = could not be determined (fails closed)
    kill_switch_engaged: bool
    connection_healthy: bool
    last_reconcile_age_s: float | None
    last_tick_age_s: float | None
    balance: Decimal | None
    api_min_stake: Decimal | None      # UNVERIFIED for the demo account (public view showed 0.35)
    open_positions: int
    realized_pnl_today: Decimal
    start_of_day_balance: Decimal | None
    consecutive_losses: int
    trades_last_hour: int
    idempotency_key_used: bool
    identical_recent_or_in_flight: bool
    trades_today: int = 0
    losses_today: int = 0
    win_streak_today: int = 0
    real_unlocked: bool = False


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    stake: Decimal | None
    results: tuple[RuleResult, ...]
    snapshot: dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> list[str]:
        return [r.name for r in self.results if not r.passed]

    def rule_results_json(self) -> list[dict[str, object]]:
        return [r.as_dict() for r in self.results]


def snapshot_of(ctx: RiskContext) -> dict[str, Any]:
    def conv(v: Any) -> Any:
        return str(v) if isinstance(v, Decimal) else v

    d = {k: conv(v) for k, v in ctx.__dict__.items() if k != "intent"}
    d["intent"] = {k: conv(v) for k, v in ctx.intent.__dict__.items()}
    return d


class RiskEngine:
    def __init__(self, cfg: RiskConfig, approvals: StrategyApprovals) -> None:
        self.cfg = cfg
        self.approvals = approvals

    def stake_for(self, ctx: RiskContext) -> StakeResult:
        c = self.cfg
        return compute_stake(ctx.balance, c.risk_per_trade_pct, c.max_stake_abs, ctx.api_min_stake, c.min_stake_abs)

    def evaluate(self, ctx: RiskContext) -> RiskDecision:
        c, i = self.cfg, ctx.intent
        stake = self.stake_for(ctx)
        results = (
            rules.kill_switch_clear(ctx.kill_switch_engaged),
            rules.mode_and_account_consistent(ctx.configured_mode, i.mode, ctx.endpoint_mode, ctx.account_is_demo, ctx.real_unlocked),
            rules.connection_and_reconciliation_healthy(ctx.connection_healthy, ctx.last_reconcile_age_s, c.reconcile_max_age_s),
            rules.market_data_fresh(ctx.last_tick_age_s, c.stale_data_max_age_s),
            rules.open_positions_within_limit(ctx.open_positions, c.max_open_positions),
            rules.daily_loss_within_limit(ctx.realized_pnl_today, ctx.start_of_day_balance, c.max_daily_loss_abs,
                                          c.max_daily_loss_pct_of_start_balance),
            rules.consecutive_losses_within_limit(ctx.consecutive_losses, c.max_consecutive_losses),
            rules.trade_rate_within_limit(ctx.trades_last_hour, c.max_trades_per_hour),
            rules.trades_per_day_within_limit(ctx.trades_today, c.max_trades_per_day),
            rules.losses_per_day_within_limit(ctx.losses_today, c.max_losses_per_day),
            rules.consecutive_wins_today_within_limit(ctx.win_streak_today, c.max_consecutive_wins_per_day),
            rules.stake_within_bounds(stake),
            rules.duplicate_protection(ctx.idempotency_key_used, ctx.identical_recent_or_in_flight),
            rules.strategy_approved(self.approvals.is_approved(i.strategy_id, i.strategy_version), i.strategy_id, i.strategy_version),
        )
        approved = all(r.passed for r in results)
        return RiskDecision(approved, stake.stake if approved else None, results, snapshot_of(ctx))

    def check_proposal(self, stake: Decimal, ask_price: Decimal, payout: Decimal) -> tuple[bool, tuple[RuleResult, ...]]:
        res = (
            rules.proposal_price_acceptable(stake, ask_price, self.cfg.max_proposal_price_slippage),
            rules.proposal_payout_acceptable(ask_price, payout, self.cfg.min_net_payout_ratio),
        )
        return all(r.passed for r in res), res
