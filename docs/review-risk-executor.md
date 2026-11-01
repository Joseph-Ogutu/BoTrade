# Review brief: risk engine, order state machine, executor, reconciler, kill switch

Audience: an independent reviewer who did not write this code and is not the agent session that did. The author cannot review this; if you are that session, stop.

Status of the code: written and tested only against a fake Deriv server. NOTHING authenticated has ever run. Every claim about real Deriv behavior is UNVERIFIED (tagged `UNVERIFIED` in code, listed in STATE.md sections 9 and 9b). Your job is to find defects the author could not see, not to confirm the tests pass.

## Scope (about 1,900 lines)

| Area | Files |
|---|---|
| Risk rules and stake | `src/derivbot/risk/rules.py`, `stake.py`, `engine.py`, `state.py`, `persist.py` |
| Risk config | `src/derivbot/config/risk.py`, `config/risk.json` |
| State machine | `src/derivbot/execution/state_machine.py` |
| Executor | `src/derivbot/execution/executor.py`, `tracker.py`, `snapshot.py`, `src/derivbot/deriv/session.py`, `src/derivbot/db/repo.py` |
| Reconciler | `src/derivbot/execution/reconciler.py` |
| Kill switch | `src/derivbot/killswitch/switch.py`, `telegram_commands.py`, `src/derivbot/api/app.py` |
| Wiring | `src/derivbot/app.py` |

Out of scope: `src/derivbot/gate1/` (separate review request, frozen), strategies, backtester.

## How to run what exists

```
bash scripts/dev_up.sh                 # local Postgres 55432 and Redis 56379, throwaway password
.venv/bin/python -m pytest -o addopts="" -q      # 638 passed on the author's machine, with the services up
make cov                               # 100% line and branch on risk/rules.py and risk/stake.py
make lint typecheck
```
Check the SKIPPED count in the pytest summary. If Postgres or Redis is down, those tests skip (and say so); a green run with skips proves less.

## Invariants to attack (each should be impossible; try to break it)

1. No buy message is ever sent without a stored, approved risk decision for that intent.
2. A stake never rises after a loss; it is never raised to a minimum; an unknown balance or unknown API minimum means no trade. (`risk/stake.py`; hypothesis properties in `tests/unit/test_stake.py`.)
3. All risk rules are evaluated every time and every result is recorded; unknown input fails closed. Look for an input type that can make a rule pass by accident (None, NaN, negative, Decimal vs float, timezone-naive).
4. State BUY_SENT is committed BEFORE the buy message is written. Find a path where the message can go out first, or where a crash between commit and send leaves a state the reconciler cannot resolve.
5. BUY_UNKNOWN never leads to a second buy. Find any retry path (reconnect hook, supervisor restart, timer) that could re-send.
6. The same proposal id is never bought twice. The same idempotency key never buys twice under concurrency (`IntentLock`, unique constraint).
7. Reconciliation never silently fixes: a mismatch alerts, pauses and engages the kill switch. Look for a branch that adopts a contract without certainty (the time and stake tolerances are guesses).
8. The kill switch: any of four stores saying engaged wins; engage never raises and never needs Deriv or Postgres or Redis; release is refused unless it can be logged; a restart does not release. Try: Redis flush, flag file deleted, Postgres down at boot, partial release.
9. The state machine table equals the HANDBOOK section 8 diagram exactly. Compare by hand, not only by the test that compares them.
10. Demo only: every layer in `guard/` and the DB CHECK `mode='demo'`. (The security brief covers these in depth; here check the executor's own re-read before the buy.)

## Questions to answer in your report

- Are there race conditions between `executor.submit`, the tracker's settlement handling and the reconciler running at the same time (connect hook runs reconciler; timer runs it too)?
- What happens on a crash at each line between "risk approved" and "settled"? List the persisted state after each, and what the reconciler does with it at the next boot.
- Is the 10 s purchase-time tolerance and the stake band used to attribute an unknown buy safe? What is the worst wrong attribution, and does the system fail toward manual review?
- Does any exception path swallow an error without engaging the kill switch or alerting?
- Are Decimal/float boundaries clean for money (stake, payout, P&L, balance)?
- Does the daily reset (`daily_reset_utc_hour`) mis-count a loss across the boundary, or around DST-free UTC edge cases?
- `api_min_stake=lambda sym: None` in `app.py` means no trade is ever placed. Confirm that this really fails closed, and that nothing else can supply a minimum.

## Known weak spots the author already suspects (verify, do not trust)

- Settled-message fields, `profit_table` `sell_price` semantics for losses, `balance_after` field names, OTP lifetime and reuse, whether subscriptions survive a reconnect: all UNVERIFIED. The fake server encodes the author's guesses, so tests passing says nothing about those.
- A lost buy reply is assumed to possibly mean a placed contract. If Deriv returns structured errors that prove non-placement, the code is more conservative than needed but not unsafe.
- The reconciler matches on symbol, contract type, stake band and time. A foreign identical contract (placed manually in the same minute on the same demo account) goes to MANUAL_REVIEW; check it cannot be attributed.
- Coverage (line and branch, measured 2026-11-01 with Postgres and Redis up): 100% on `risk/rules.py`, `stake.py`, `engine.py`, `state_machine.py`; `reconciler.py` 97%, `executor.py` 94%, `killswitch/switch.py` 90%, `deriv/session.py` 87%, `execution/tracker.py` 77%. Run `coverage report --show-missing` and read the uncovered lines first, especially in `tracker.py` and `switch.py`.
- Property tests cover stake and the state machine; the executor is covered by scenario tests with failure injection, not by exhaustive fault combination.

## What would change the verdict

Any path to a buy without risk approval; any second buy for one intent; any release of the kill switch without a log; any silent adoption of an ambiguous contract; any way for real mode to reach an order. Report each with the file, the line, and the sequence of events that triggers it.

## Deliverable

A written report with: findings ranked by severity (each with a reproducing test or sequence), what you could not assess, and the exact commit you reviewed (`git rev-parse HEAD`). Do not fix code in the review; open findings for the owner.
