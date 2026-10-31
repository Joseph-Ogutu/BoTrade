# Deriv API notes (Phase 0)

Status: not started. Every item is UNVERIFIED until answered with evidence: a dated request and a response with secrets redacted, or an official docs URL and date. Items marked "handbook claim" come from HANDBOOK section 2 (researched 2026-10-30) and are not independently confirmed.

Do not paste PATs, OTP URLs or account IDs here. Use [REDACTED].

## The ten items (HANDBOOK section 3)

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | Contract types, durations, min/max stakes from `contracts_for` on DEMO for chosen symbols | UNVERIFIED | needs demo PAT |
| 2 | Real payout ratio from `proposal` per candidate contract (sets p_be) | UNVERIFIED | needs demo PAT |
| 3 | Whether `buy` has an idempotency token or client reference (assume no) | UNVERIFIED | needs schema review (deriv-api-schemas) |
| 4 | How `proposal_open_contract` reports settlement; subscription behaviour after reconnect | UNVERIFIED | needs demo PAT |
| 5 | `ticks_history` max `count` and pagination for backfill | UNVERIFIED | public endpoint, no auth needed |
| 6 | OTP single-use and lifetime (assume single-use, short-lived, fetch fresh per connect) | UNVERIFIED | needs demo PAT |
| 7 | Fields returned by `portfolio` and `profit_table` (reconciliation design) | UNVERIFIED | needs demo PAT |
| 8 | Whether the demo balance can be reset | UNVERIFIED | docs review |
| 9 | Whether legacy `ws.binaryws.com` is deprecated, and when | UNVERIFIED | docs review |
| 10 | Terms of use on automated trading for owner's jurisdiction (Kenya) and account type | UNVERIFIED | owner compliance check, not code |

## Also to record (HANDBOOK 4.2)

- Deriv's own published description of how synthetic indices are generated: UNVERIFIED
- How to tell a demo account from a real one in account/OTP responses (needed for the fail-closed mode guard): UNVERIFIED

## Entries

Format for each finding: date, request (secrets redacted), response (redacted), conclusion.

(none yet)
