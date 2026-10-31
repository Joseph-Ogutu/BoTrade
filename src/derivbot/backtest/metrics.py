"""Backtest metrics (HANDBOOK 9.4). Pure functions of a trade list. Reuses the Wilson interval from derivbot.stats.edge."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from derivbot.backtest.engine import BacktestResult, Trade
from derivbot.stats.edge import expectancy, wilson_lower


@dataclass(frozen=True)
class Metrics:
    n: int
    wins: int
    win_rate: float
    wilson95_lower_one_sided: float
    avg_win: float
    avg_loss: float
    risk_reward: float | None
    expectancy_per_unit: float
    profit_factor: float | None
    total_profit: float
    max_drawdown: float
    max_drawdown_trades: int
    longest_loss_streak: int
    trades_per_day: float
    mean_r: float
    breakeven_p: float


def max_drawdown(profits: list[Decimal]) -> tuple[Decimal, int]:
    """Largest peak-to-trough fall of cumulative profit, and the longest stretch (in trades) spent below a peak."""
    cum = peak = Decimal(0)
    worst = Decimal(0)
    since_peak = longest = 0
    for p in profits:
        cum += p
        if cum >= peak:
            peak, since_peak = cum, 0
        else:
            since_peak += 1
            longest = max(longest, since_peak)
        worst = max(worst, peak - cum)
    return worst, longest


def longest_streak(wins: list[bool], target: bool = False) -> int:
    best = cur = 0
    for w in wins:
        cur = cur + 1 if w is target else 0
        best = max(best, cur)
    return best


def compute(trades: list[Trade]) -> Metrics:
    n = len(trades)
    if n == 0:
        return Metrics(0, 0, 0.0, 0.0, 0.0, 0.0, None, 0.0, None, 0.0, 0.0, 0, 0, 0.0, 0.0, 1.0)
    wins = [t for t in trades if t.win]
    losses = [t for t in trades if not t.win]
    gp = sum((t.profit for t in wins), Decimal(0))
    gl = -sum((t.profit for t in losses), Decimal(0))
    avg_win = float(gp / len(wins)) if wins else 0.0
    avg_loss = float(-gl / len(losses)) if losses else 0.0
    mean_r = float(sum((t.r for t in trades), Decimal(0)) / n)
    p_hat = len(wins) / n
    dd, dd_len = max_drawdown([t.profit for t in trades])
    span_days = max((trades[-1].exit_epoch - trades[0].entry_epoch) / 86400.0, 1e-9)
    return Metrics(
        n=n, wins=len(wins), win_rate=p_hat, wilson95_lower_one_sided=wilson_lower(len(wins), n, 0.95),
        avg_win=avg_win, avg_loss=avg_loss, risk_reward=(avg_win / -avg_loss) if avg_loss else None,
        expectancy_per_unit=float(sum((t.profit / t.stake for t in trades), Decimal(0)) / n),
        profit_factor=float(gp / gl) if gl else None, total_profit=float(gp - gl), max_drawdown=float(dd),
        max_drawdown_trades=dd_len, longest_loss_streak=longest_streak([t.win for t in trades], False),
        trades_per_day=n / span_days, mean_r=mean_r, breakeven_p=1.0 / (1.0 + mean_r),
    )


def by_block(trades: list[Trade], blocks: int) -> list[Metrics]:
    """Equal-size time blocks by trade count (for the regime/time-block condition of the kill criterion)."""
    if blocks < 1:
        raise ValueError("blocks must be at least 1")
    size = len(trades) // blocks
    return [compute(trades[i * size:(i + 1) * size if i < blocks - 1 else len(trades)]) for i in range(blocks)] if size else []


def expectancy_at_shifted_r(trades: list[Trade], delta_r: float) -> float:
    """Sensitivity: expectancy per unit stake if every payout ratio were `delta_r` lower (HANDBOOK 9.4)."""
    if not trades:
        return 0.0
    p = sum(t.win for t in trades) / len(trades)
    mean_r = float(sum((t.r for t in trades), Decimal(0)) / len(trades)) - delta_r
    return expectancy(p, mean_r)


def summary(result: BacktestResult) -> Metrics:
    return compute(result.trades)
