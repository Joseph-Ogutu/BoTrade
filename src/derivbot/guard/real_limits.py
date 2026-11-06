"""Hard ceilings for REAL-money risk configuration. They live in code, not in a config file, so no edit to config/risk.real.json can
loosen them; the owner can only set values at or below them. Real mode refuses to start if any ceiling is exceeded or any required
owner rule is missing.

The ceilings are the owner's own daily rules, stated in chat on 2026-11-06: at most 5 bets a day, stop for the day after 2 losses, stop
after 3 wins in a row, and each bet 15 percent of the balance available at most. Added by the engineer: one open position, a ceiling on
the daily loss as a share of the start-of-day balance (30 percent), the price slippage and the minimum payout.

RISK NOTE for the owner: 15 percent per bet is the ceiling, not a recommendation. Two losses in a row at 15 percent cost about 28 percent
of the account, and a fair coin loses about 0.75 percent of the account per bet on average. Setting a much smaller fraction in
config/risk.real.json is allowed and is what the launch plan proposes.
"""

from __future__ import annotations

from decimal import Decimal

from derivbot.config.risk import RiskConfig

MAX_RISK_FRACTION = Decimal("0.15")
MAX_TRADES_PER_DAY = 5
MAX_LOSSES_PER_DAY = 2
MAX_WIN_STREAK_STOP = 3
MAX_DAILY_LOSS_FRACTION = Decimal("0.30")
MAX_SLIPPAGE = Decimal("0.02")
MIN_NET_PAYOUT_RATIO = Decimal("0.80")


class RealLimitsError(Exception):
    pass


def real_limit_violations(cfg: RiskConfig) -> list[str]:
    v: list[str] = []
    if cfg.risk_per_trade_pct > MAX_RISK_FRACTION:
        v.append(f"risk_per_trade_pct {cfg.risk_per_trade_pct} is above the hard ceiling {MAX_RISK_FRACTION}")
    if cfg.max_trades_per_day is None or cfg.max_trades_per_day > MAX_TRADES_PER_DAY:
        v.append(f"max_trades_per_day must be set and at most {MAX_TRADES_PER_DAY}")
    if cfg.max_losses_per_day is None or cfg.max_losses_per_day > MAX_LOSSES_PER_DAY:
        v.append(f"max_losses_per_day must be set and at most {MAX_LOSSES_PER_DAY}")
    if cfg.max_consecutive_wins_per_day is None or cfg.max_consecutive_wins_per_day > MAX_WIN_STREAK_STOP:
        v.append(f"max_consecutive_wins_per_day must be set and at most {MAX_WIN_STREAK_STOP}")
    if cfg.max_open_positions != 1:
        v.append("max_open_positions must be exactly 1")
    if cfg.max_daily_loss_pct_of_start_balance is None or cfg.max_daily_loss_pct_of_start_balance > MAX_DAILY_LOSS_FRACTION:
        v.append(f"max_daily_loss_pct_of_start_balance must be set and at most {MAX_DAILY_LOSS_FRACTION}")
    if cfg.max_proposal_price_slippage > MAX_SLIPPAGE:
        v.append(f"max_proposal_price_slippage must be at most {MAX_SLIPPAGE}")
    if cfg.min_net_payout_ratio < MIN_NET_PAYOUT_RATIO:
        v.append(f"min_net_payout_ratio must be at least {MIN_NET_PAYOUT_RATIO}")
    return v


def validate_real_limits(cfg: RiskConfig) -> None:
    problems = real_limit_violations(cfg)
    if problems:
        raise RealLimitsError("real-money risk limits refused: " + "; ".join(problems))
