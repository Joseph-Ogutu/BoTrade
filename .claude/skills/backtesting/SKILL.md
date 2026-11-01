---
name: backtesting
description: Use when running or changing the contract-level backtester, its metrics or verdicts, or when asked to evaluate a strategy. Covers contract simulation, look-ahead rules, splits, negative controls, metrics, and the real-data permit.
---

# backtesting

## When to use
Anything under `src/derivbot/backtest/`, `scripts/run_backtest.py`, or a request to "test" a strategy.

## INTEGRITY RULES
- Simulated data only until Gate 1 reports and the owner writes the approval line in `docs/approvals.md`. `scripts/run_backtest.py` is simulated by default and REFUSES real data without the line plus a Gate 1 run record. Never edit `docs/approvals.md` yourself.
- Do not touch `src/derivbot/gate1/`. Do not run any statistic on the holdout in `data/`.

## Model
- Contract level, not price level. A trade is: signal at tick t, entry at the next tick (t+1), duration d ticks, outcome from the contract rules (`backtest/engine.py::_wins`), payout from `backtest/payout.py::PayoutModel` built from quotes (`Quote(contract, stake, payout)`). Break-even probability is `stake / payout`; the spread is the whole edge to beat.
- No fills at the signal tick, no use of a tick later than the decision. `causality_check` appends altered future ticks and requires identical earlier signals; `LookaheadDetected` is raised otherwise. `run_backtest` runs it.
- Splits: chronological only (train/validate/test never shuffled). Parameters are fixed in the hypothesis file before any split is looked at.

## Controls (a backtest without these proves nothing)
1. Negative control: the same strategy on `simulate_random_walk` and `shuffled_returns(series, seed)` must show no edge; if it does, the harness has a bug.
2. Positive control: plant a known edge in simulated data and confirm the harness finds it.
3. Look-ahead canary: a deliberately peeking strategy must trip `LookaheadDetected`.

## Metrics and verdict
`backtest/metrics.py` (win rate, expectancy per stake, max drawdown counted as in the tests, trades below peak) and `backtest/verdict.py` (compares with break-even and the corrected alpha, handles the block-condition). Report n, the confidence interval and the corrected p. Never report a Sharpe-like number from fewer trades than the power table requires.

## Instructions
1. `.venv/bin/python scripts/run_backtest.py --strategy ema_trend --simulated --ticks 50000 --seed 1` (optional `--rho` plants momentum for a positive control). `--real-db` is refused without the approval. Results are written by `backtest/store.py` (Postgres `backtest_runs`, `backtest_trades`) with the command that reproduces them.
2. Every reported number must come from a committed command. No hand-typed results.
3. State what was NOT tested.

## Validation
- `.venv/bin/python -m pytest tests/unit/test_backtest.py tests/unit/test_backtest_verdict.py tests/unit/test_backtest_permit.py tests/db/test_backtest_store.py -o addopts="" -q`

## Worked example: confirm a strategy has no edge on a random walk
1. `series = simulate_random_walk(50_000, seed=7)`; `payout = PayoutModel([Quote("CALL", D("1"), D("1.95"))])   # Quote(contract_type, stake, payout)`.
2. `run_backtest(series, lambda s: EmaTrend(), payout)`; expected win rate is about 0.5, below break-even `1/1.95`, expectancy about -2.5 percent of stake.
3. Repeat on `shuffled_returns(series, 8)`; the difference must be within noise. If not, stop and debug the harness.
