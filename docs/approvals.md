# Approvals

Only the owner writes in this file.

## Real-data backtests

No real collected tick may be used in a backtest, a strategy test or any digit statistic until Gate 1 has reported
(docs/gates.md) and the owner has approved here. The code (`derivbot.backtest.permit`) refuses to load real ticks
without a line of this exact form, written at the start of a line, and only if a matching Gate 1 run record exists:

    REAL_DATA_BACKTEST_APPROVED: <YYYY-MM-DD> by <owner name> gate1=<OUTCOME>

No such line exists. Real-data backtests are not approved.
