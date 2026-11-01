---
name: trading-execution
description: Use when changing the order lifecycle, the executor, the reconciler or anything that sends or interprets buy, portfolio or settlement messages. Covers the state machine, idempotency, and the reconciliation procedure.
---

# trading-execution

## When to use
`src/derivbot/execution/`, `src/derivbot/deriv/session.py`, `src/derivbot/db/repo.py`, `tests/execution/`.

## State machine (`execution/state_machine.py`, pure)
States: INTENT_CREATED, RISK_REJECTED, RISK_APPROVED, PROPOSAL_REQUESTED, PROPOSAL_REJECTED, BUY_SENT, OPEN, BUY_UNKNOWN, NOT_PLACED, MANUAL_REVIEW, SETTLED, SOLD. The transition table equals the diagram in HANDBOOK section 8; a test compares them. Rules that must survive any change:
- BUY_UNKNOWN can never go back to BUY_SENT. A lost buy reply is resolved by looking at the exchange, never by buying again.
- EXPOSURE states are BUY_SENT, BUY_UNKNOWN, OPEN, MANUAL_REVIEW. Risk counts them as open positions; an unresolved BUY_UNKNOWN blocks the next trade.
- BUY_SENT is COMMITTED to Postgres BEFORE the buy message is written. Transitions use an optimistic check (`repo.transition`); a stale writer fails.

## Idempotency
- There is no exchange-side idempotency token (DOCS). Idempotency is ours: a unique key per intent (`IntentLock` in Redis, unique constraint in Postgres), duplicate rule in risk, buy by proposal id (a proposal id is bought at most once), and the recent-duplicate window.
- The executor re-reads kill switch and demo-ness immediately before the buy.

## Reconciliation procedure (`execution/reconciler.py`, `execution/snapshot.py`)
1. Take a snapshot: portfolio (open contracts) AND profit_table (settled), because portfolio lists only open contracts and a fast contract may already be settled.
2. Match local rows to remote contracts by contract id, then for BUY_UNKNOWN by symbol, contract type, stake band and purchase time within a tolerance (10 s is a guess, UNVERIFIED).
3. Exactly one candidate: attribute it. Zero after the wait window: NOT_PLACED. More than one, or a contested contract: MANUAL_REVIEW, engage the kill switch, alert. NEVER silently fix: every mismatch is alerted, the bot pauses, and a human decides.
4. Run on every connect (hook runs BEFORE the session is published to the executor) and on a timer.

## Instructions for changes
1. Test first against `tests/fakes/fake_deriv.py` with failure injection; add a case for the new failure.
2. Keep the global invariant in tests: buy messages sent never exceed intents that reached BUY_SENT; no proposal id bought twice; every exposed intent has an approved risk decision.
3. Tag every unverified API assumption (`UNVERIFIED:`), add to STATE.md section 9.
4. Unexpected exceptions in the executor engage the kill switch.

## Validation
- `.venv/bin/python -m pytest tests/execution tests/unit/test_state_machine.py -o addopts="" -q` (needs `scripts/dev_up.sh`; DB tests SKIP if Postgres is down, and the summary says so. Check the skipped count).

## Worked example: handle "buy reply arrives twice"
1. Fake server fault `duplicate` sends the buy reply two times.
2. Expected: one contract, state OPEN once, second reply ignored, audit shows a single transition. Add the test to `tests/execution/test_executor.py`.
