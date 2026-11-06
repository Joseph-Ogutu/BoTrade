# Real-money launch plan (not started, no date)

Written 2026-11-06 at the owner's request. Nothing in this file enables real trading. The code still cannot send an order to the real
account (`REAL_TRADING_IMPLEMENTED = False`, the endpoint allowlist, demo-only OTP, a database CHECK and the other layers in
`src/derivbot/guard`).

## Where things stand (2026-11-06)

- The real account exists and is view only. Its balance is 0.00 USD, so there is nothing to trade.
- The only trading so far is a coin-flip soak on the demo account: 162 trades, 48.1 percent wins against a 52.6 percent break-even,
  a result that is what chance gives. Strategies have not been tested yet; the forward test (Amendment 5) and Gate 1 are the tests.
- The real path has not been written, reviewed or exercised.

## Conditions to start (all of them, in this order)

1. Evidence: a registered strategy passes the forward test of Amendment 5 at its single evaluation, and its direction is consistent with
   the Gate 1 result. If none passes, the correct outcome is "no edge" and real trading does not start.
2. Replication: the same variant passes again on a second forward window of equal size.
3. Independent review: `docs/review-risk-executor.md` and `docs/review-security.md` reviewed by someone other than the author, findings
   fixed and re-reviewed.
4. Real-path build, as its own phase: tests with the fake exchange first, then a review of the diff that removes each guard layer.
5. Funding: only an amount the owner can lose entirely.
6. `docs/live-readiness.md` ticked and signed by the owner, and the REAL_TRADING_APPROVED file created by the owner (HANDBOOK 11.4).

## Starting limits (set by the owner before step 6; these are only a proposal)

| Setting | Proposal |
|---|---|
| Stake per bet | 1.00 USD flat, no scaling after wins or losses |
| Bets per day | at most 5 |
| Stop for the day | after 2 losses (the owner tightened the demo rule from 3 to 2 on 2026-11-06) |
| Daily loss limit | 5.00 USD |
| Total loss limit | the amount funded; the bot halts at 50 percent of it and waits for the owner |
| Kill switch | tested on demo with the real account's guards in place before the first real bet |

## First weeks

Read the dashboard daily. Run the accuracy audit against the real account's records after every day. Do not raise a limit until at least
two weeks of results have been reviewed, and never raise two limits at once.
