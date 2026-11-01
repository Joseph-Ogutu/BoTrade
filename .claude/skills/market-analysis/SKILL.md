---
name: market-analysis
description: Use when handling tick data, digits, resampling, indicators or regime features. Covers the tick store, exact decimals and last-digit rules, the causal TickWindow, and the integrity rule that real data is permit-gated.
---

# market-analysis

## When to use
Reading or transforming ticks, computing indicators, adding regime features, or looking at the data at all.

## INTEGRITY RULES (read first)
- The holdout data in `data/` is OFF LIMITS. Do not run any digit, strategy or backtest statistic on real collected ticks. Develop and test on simulated data (`backtest.series.simulate_random_walk`, `simulate_digits`) and on the fake server.
- Loaders for real ticks are permit-gated: `backtest.permit.require_real_data_approval` needs the owner's approval line in `docs/approvals.md` and a Gate 1 run record; `gate1.loader` has its own gate. Never bypass them and never read `data/ticks.sqlite` directly.
- Do not touch `src/derivbot/gate1/` (awaiting independent review).

## Data handling
- Quotes are stored as exact decimal text padded to `pip_size` decimals (`collector/store.py`). The last digit of the quote at exactly `pip_size` decimals is the digit. Trailing zeros matter (Amendment A1.4). A row with unknown `pip_size` is never guessed.
- All times are UTC epoch seconds or ISO-8601 with `Z` from `derivbot.common.timeutil`. Do not format local time.
- Gaps: `collector/gaps.py` `find_gaps`. A gap breaks any window that spans it; do not interpolate.

## Instructions
1. Strategies see only a `signals.base.TickWindow`: ticks 0..t, no way to ask for a later tick. Keep lookbacks bounded.
2. Indicators are plain numpy in `signals/strategies.py` (EMA with finite memory, RSI, Bollinger, efficiency ratio for the regime filter). Add a new indicator with an independent loop-based oracle test (see `tests/unit/test_strategies.py::oracle_ema`).
3. Resampling: do it on epoch buckets from the stored ticks, take the last tick in a bucket, and never forward-fill across a gap.
4. Regime features must be causal: compute from ticks up to t only. The backtester's `causality_check` proves prefix invariance; run it on every new feature.

## Validation
- `.venv/bin/python -m pytest tests/unit/test_strategies.py tests/unit/test_backtest.py -o addopts="" -q`
- A feature passes only if `causality_check` raises nothing on a simulated series.

## Worked example: add a "tick range" volatility feature
1. Write the oracle: for each t, `max(q[t-n+1:t+1]) - min(...)` with a Python loop.
2. Implement it vectorised over the `TickWindow`; compare at every t on `simulate_random_walk(2000, seed=1)`.
3. Run `causality_check` with the feature inside a trivial strategy; append a changed future and confirm earlier outputs are unchanged.
4. Do NOT evaluate it on collected ticks. Register it as a hypothesis first (strategy-research skill).
