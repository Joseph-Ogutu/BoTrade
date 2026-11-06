# Approvals

Only the owner writes in this file.

## Real-data backtests

No real collected tick may be used in a backtest, a strategy test or any digit statistic until Gate 1 has reported
(docs/gates.md) and the owner has approved here. The code (`derivbot.backtest.permit`) refuses to load real ticks
without a line of this exact form, written at the start of a line, and only if a matching Gate 1 run record exists:

    REAL_DATA_BACKTEST_APPROVED: <YYYY-MM-DD> by <owner name> gate1=<OUTCOME>

No such line exists. Real-data backtests are not approved.

## Live demo forward test (Amendment 5)

The code (`derivbot.forward.permit`) starts the forward test only if this file has a line of this exact form, and only while
`docs/forward_test_lock.json` equals the strategies in the code. This does NOT unlock reading stored ticks and does not authorise
real-money trading.

    LIVE_DEMO_FORWARD_TEST_APPROVED: <YYYY-MM-DD> by <owner name> amendment=5

Recorded on the owner's explicit instruction in chat on 2026-11-06 ("Let's just go to B"), written by the agent at that instruction:

LIVE_DEMO_FORWARD_TEST_APPROVED: 2026-11-06 by Joseph (owner, instructed in chat) amendment=5
