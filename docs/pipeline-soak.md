# Pipeline soak test (demo only, not a strategy)

Written 2026-11-05 at the owner's request to start trading on the demo account before Gate 1.

## What it is

`src/derivbot/execution/soak.py`. Every 5 minutes (`SOAK_INTERVAL_S`) it submits one CALL or PUT on 1HZ100V for 5 ticks, the side chosen by a coin flip. The trade goes through the normal path: risk engine, proposal, buy, tracking, reconciliation, settlement. The stake is whatever the risk engine sizes under `config/risk.json`. It is enabled only when the deployment sets `PIPELINE_SOAK=1`, only submits mode `demo`, and is approved in memory only (`config/strategies.json` is not touched).

## Why it exists

To observe, on the real demo exchange and over time, what the unit tests and the fake server cannot show: a WINNING contract's final message, buy error shapes, behaviour across reconnects, the daily loss limit tripping, the dashboard filling with real rows. These are the UNVERIFIED items in `docs/api-notes.md`.

## What it must never be used for

- It reads no prices, no signals, no collected tick data and no statistic (a test checks the module does not mention them). Its results say NOTHING about any edge.
- Do not use its win rate, P&L or timing to choose, tune, approve or reject a strategy. Strategies still wait for Gate 1 and the owner's line in `docs/approvals.md`.
- The expected result of a fair coin is a loss of the payout margin, about 5 percent of stake per trade (payout 1.9 per 1.0 at the observed quote). Losses on the demo account are expected.

## Limits that apply

The same risk engine as any trade: max stake, daily loss limit, consecutive losses, trades per hour, one open position, stale data, reconcile age, kill switch. When a limit trips, the soak simply stops being able to trade until the condition clears (daily limits reset at 00:00 UTC). Turn it off by removing `PIPELINE_SOAK` from the deployment environment and restarting the bot, or stop everything with the kill switch.
