"""Contract-level backtester (HANDBOOK 9.2). It buys a specific contract at a signal and settles it at expiry.

Timing (each is an explicit parameter, none is a guess hidden in the code):
- decision at tick t uses ticks 0..t only (TickWindow);
- the order reaches the exchange `latency_ticks` ticks later; entry is the tick at t + latency_ticks (at least 1, so the
  entry is strictly after the decision). Latency should be MEASURED from demo executions: UNVERIFIED until they exist;
- settlement tick is entry + duration_ticks. Whether Deriv settles on that tick or one earlier is UNVERIFIED;
- no overlap: while a contract is open no new one is taken (the independent-trade count the kill criterion needs);
- CALL wins only if exit quote > entry quote, so a tie loses (UNVERIFIED tie rule);
- the payout ratio comes from stored proposal quotes (PayoutModel), conservative by default; there is no free gross
  result: a loss costs the stake, a win pays r times the stake.

Causality is checked, not assumed: causality_check() rebuilds the strategy on a truncated series and requires the same
signals, which a strategy that peeks at later data cannot satisfy.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

import numpy as np

from derivbot.backtest.payout import PayoutModel
from derivbot.backtest.series import TickSeries
from derivbot.signals.base import LookaheadError, Signal, Strategy, TickWindow


class LookaheadDetected(Exception):
    pass


@dataclass(frozen=True)
class Trade:
    seq: int
    decision_epoch: int
    entry_epoch: int
    exit_epoch: int
    contract_type: str
    barrier: int | None
    stake: Decimal
    r: Decimal
    win: bool
    profit: Decimal


@dataclass
class BacktestResult:
    strategy_id: str
    strategy_version: str
    params: dict
    symbol: str
    ticks: int
    first_epoch: int
    last_epoch: int
    trades: list[Trade] = field(default_factory=list)
    payout_model: dict = field(default_factory=dict)
    settings: dict = field(default_factory=dict)


def _wins(contract: str, barrier: int | None, series: TickSeries, entry: int, exit_: int) -> bool:
    d = int(series.digits[exit_])
    if contract == "DIGITEVEN":
        return d % 2 == 0
    if contract == "DIGITODD":
        return d % 2 == 1
    if contract == "DIGITOVER":
        return d > int(barrier)  # type: ignore[arg-type]
    if contract == "DIGITUNDER":
        return d < int(barrier)  # type: ignore[arg-type]
    if contract == "DIGITMATCH":
        return d == int(barrier)  # type: ignore[arg-type]
    if contract == "DIGITDIFF":
        return d != int(barrier)  # type: ignore[arg-type]
    if contract == "CALL":
        return bool(series.quotes[exit_] > series.quotes[entry])      # UNVERIFIED tie rule: a tie loses
    if contract == "PUT":
        return bool(series.quotes[exit_] < series.quotes[entry])
    raise ValueError(contract)


def causality_check(factory: Callable[[TickSeries], Strategy], series: TickSeries, probes: int = 40, seed: int = 0) -> None:
    """For random decision ticks t: the signal computed with the full series available to the strategy's constructor must
    equal the signal computed when only ticks 0..t exist. Raises LookaheadDetected otherwise."""
    rng = np.random.default_rng(seed)
    full = factory(series)
    lo = max(full.warmup_ticks, 1)
    if len(series) <= lo + 2:
        return
    for t in sorted(set(rng.integers(lo, len(series) - 1, probes).tolist())):
        prefix = series.prefix(t + 1)
        try:
            a = full.signal(TickWindow(series.quotes, series.digits, series.epochs, t))
            b = factory(prefix).signal(TickWindow(prefix.quotes, prefix.digits, prefix.epochs, t))
        except (LookaheadError, IndexError) as exc:
            raise LookaheadDetected(f"strategy failed when future ticks were absent at t={t}: {type(exc).__name__}") from exc
        if a != b:
            raise LookaheadDetected(f"signal at tick {t} depends on data after tick {t}: {a} != {b}")


def run_backtest(series: TickSeries, factory: Callable[[TickSeries], Strategy], payout: PayoutModel,
                 stake: Decimal = Decimal(1), latency_ticks: int = 1, check_causality: bool = True) -> BacktestResult:
    if latency_ticks < 1:
        raise ValueError("latency_ticks must be at least 1: the entry must be strictly after the decision")
    if check_causality:
        causality_check(factory, series)
    strat = factory(series)
    res = BacktestResult(strat.id, strat.version, dict(strat.params), series.symbol, len(series),
                         int(series.epochs[0]) if len(series) else 0, int(series.epochs[-1]) if len(series) else 0,
                         payout_model=payout.to_json(),
                         settings={"stake": str(stake), "latency_ticks": latency_ticks, "duration_ticks": strat.duration_ticks,
                                   "settle_tick": "entry + duration_ticks (UNVERIFIED)", "overlap": "forbidden"})
    n, dur = len(series), strat.duration_ticks
    t, seq = max(strat.warmup_ticks, 0), 0
    while t < n:
        entry, exit_ = t + latency_ticks, t + latency_ticks + dur
        if exit_ >= n:
            break
        sig: Signal | None = strat.signal(TickWindow(series.quotes, series.digits, series.epochs, t))
        if sig is None:
            t += 1
            continue
        win = _wins(sig.contract_type, sig.barrier, series, entry, exit_)
        r = payout.r(sig.contract_type, stake)
        profit = stake * r if win else -stake
        res.trades.append(Trade(seq, int(series.epochs[t]), int(series.epochs[entry]), int(series.epochs[exit_]),
                                sig.contract_type, sig.barrier, stake, r, win, profit))
        seq += 1
        t = exit_          # no overlap: the next decision is at the settlement tick or later
    return res
