# STATE.md: Deriv Bot Revamp, session handoff

Any agent starting a session reads this file FIRST, then CLAUDE.md, then the relevant part of docs/HANDBOOK.md.
Update this file and commit after every completed task. If it is not in this file and in git, it did not happen.

Last updated: see the newest commit time in UTC (`TZ=UTC git log -1 --date=format-local:%Y-%m-%dT%H:%M:%SZ`) by: Claude Code session
Git HEAD when updated: c399a69 (scaffold commit)

## 0. BUILD PHASE PROGRESS (resume here)

Build phase started 2026-10-31T23:08Z by owner instruction. Order and rules are fixed; do items in order, commit and update this table after each. Integrity rules: no digit, strategy or backtest statistic on real ticks; do not change or freeze src/derivbot/gate1; register hypotheses before any real-data run; no LLM in the loop; real trading unreachable; every authenticated assumption tagged UNVERIFIED.

Local infra for tests: `scripts/dev_up.sh` (Postgres 16 on 127.0.0.1:55432, Redis 7 on 127.0.0.1:56379, throwaway password in git-ignored dev-local/). Database and Redis tests SKIP (and say so) if it is down: a skipped test is not evidence.

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | Persistence: Postgres + Alembic + append-only audit + Redis KV | DONE | see Done list |
| 2 | Typed settings, versioned risk config with SET_BY_OWNER failing loudly | TODO | |
| 3 | Order state machine, property tests | TODO | |
| 4 | Risk engine, stake sizing, 100% branch coverage on rules (REPORT) | TODO | |
| 5 | Kill switch: Telegram, localhost admin API, flag file; survives restart | TODO | |
| 6 | Executor against fake Deriv server, failure injection | TODO | |
| 7 | Reconciler (REPORT) | TODO | |
| 8 | Contract-level backtester, look-ahead canary (simulated data only) | TODO | |
| 9 | Strategy modules (pure, no tuning) + hypotheses registered | TODO | |
| 10 | Notifier (fake transport), admin API on 127.0.0.1 with bearer auth | TODO | |
| 11 | Real-trading guard (REPORT) | TODO | |
| 12 | Full compose, healthchecks, backups, runbook, CI config. Do NOT deploy | TODO | |
| 13 | Project skills in .claude/skills (needs .gitignore exception) | TODO | |
| 14 | Review briefs: docs/review-risk-executor.md, docs/review-security.md | TODO | |

Resume point: item 2.

## 1. Project in one paragraph

Personal Deriv trading bot, rebuilt from scratch as a thin asyncio Python service. Demo only. Profitability is unproven and must pass the pre-registered edge test (HANDBOOK section 4) before anything else matters. The old bot is archived and is not to be reused without owner approval.

## 2. Where we are

Current phase: Phase 0 (API verification, partly done) with credential-free Phase 3 and Phase 4 pieces built ahead, by owner instruction
Current gate: none passed yet

Phase checklist (tick only when acceptance criteria in HANDBOOK section 13 are met and evidence is committed):

- [ ] Housekeeping: old bot archived, new repo created, .gitignore covers env and token files, no token file inside the repo
  - done: new repo created at ~/Desktop/deriv-bot-revamp, .gitignore covers env and token files, no token file inside it
  - not done: old bot not yet archived (owner action), token file still sits in the old repo root (see section 5)
- [ ] Phase 0: docs/api-notes.md answers all ten items in HANDBOOK section 3
- [ ] Phase 3: Deriv adapter, smoke test passes on DEMO, fake Deriv server tests pass
  - done (credential-free): typed messages and schema-checked request builders, rate limiter, fake Deriv server with failure injection, log redaction, public reconnecting tick stream
  - held until DERIV_APP_ID_DEMO is in .env: REST client, OTP flow, authenticated WS session (balance, proposal, buy), scripts/smoke_demo.py, DEMO smoke test, .claude/skills
  - not done: buy/portfolio reconcile logic against the fake (belongs with the executor, Phase 7 and 8)
- [ ] Phase 4: collector running, gap report, candle rebuild test
  - done: SQLite tick store, gap detection, collector, scripts/collect_ticks.py, 2 short live runs on the public endpoint (not a continuous collection)
  - done since: Docker collector (compose, healthcheck, heartbeat, watchdog), backup/restore tooling, runbook
  - not done: Postgres tables and Alembic (not started, by instruction), backfill via ticks_history (public view: 1000 per call, 12 to 24 h retention, silent fall back to latest ticks beyond that, see api-notes), candle builder, the collector running on the Oracle VM, N days of data
- [ ] Gate 1 prep: docs/kill-criterion.md committed BEFORE any strategy test, power calculation done
  - done: kill-criterion.md committed, planning power table (n_min about 3,800 at K=1 and about 8,250 at K=20 for delta 0.02)
  - not done: binding n_min needs real r from proposal (api-notes item 2); holdout hash in docs/gates.md; control experiment harness
- [ ] Gate 1: edge test on locked holdout, control experiment passed
- [ ] Phase 5 and 6: strategies and backtests
- [ ] Phase 7: risk engine, kill switch, state machine, reconciler
- [ ] Phase 8: demo execution
- [ ] Gate 2: demo matches backtest
- [ ] Phase 9: Oracle deployment
- [ ] Phase 10: independent evaluation
- [ ] Gate 3: live readiness (owner only)

## 3. Done so far (append, newest first)

- 2026-10-31 UTC (23:20Z): BUILD ITEM 1 persistence. Alembic migration 0001 creates every handbook section 8 table plus order_events (implied by the ER diagram), with CHECK constraints for the 12 order states, `order_intents_demo_only` and `orders_demo_only` (mode = 'demo', so a real-mode row needs a deliberate migration), and audit_events with an UPDATE/DELETE row trigger and a TRUNCATE statement trigger. src/derivbot/db (engine, append_audit with redaction and a closed event vocabulary) and src/derivbot/kv (Redis and in-memory stores, IntentLock with owner-token release, RateCounter, HeartbeatKV). Local compose docker-compose.dev.yml (127.0.0.1 only, secret via file, tmpfs data). 29 tests pass against the real containers with 0 skipped: audit UPDATE, DELETE and TRUNCATE are each rejected with "append-only" and leave the row count unchanged; a real-mode intent is rejected by the database; downgrade to base then upgrade works. The SQLite tick store for the collector is unchanged
- 2026-10-31 UTC (23:00Z): Gate 1 harness built and controls run on SIMULATED data only (seed 20261031, fixed before running, no re-rolling). Code: src/derivbot/gate1 (numerics without scipy, checked against scipy references; the four registered tests, checked against hand cases, scipy and loop-based oracles on gappy data; pipeline with segmentation, halves, shuffles and the decision rule; controls; a permit-gated real-data loader). Results, docs/evidence/gate1_control_20261031T230012Z.json: negative control, 1000 series of 50,000 iid digits, no gaps and with random gaps: per-test false-positive rates 0.044 to 0.056 and 0.048 to 0.053 (band 0.0293 to 0.0707), KS uniformity of p-values passed for all, any Holm rejection in the family of 8 was 0.0510 and 0.0590 (limit 0.0707). Positive control, 200 series of 100,000 digits per planted structure: detection 1.0 for every required test (limit 0.95). Run-validity machinery: rates 0.040 to 0.065 (band 0.0038 to 0.0962). NOT run on any real tick. scripts/run_gate1.py exists but refuses to start before 2026-11-15T00:00:00Z, without a frozen harness commit in docs/gates.md, or if harness files changed since; its refusal paths are tested. The harness is not frozen yet. Tests: 238 pass
- 2026-10-31 UTC (22:52Z): two defects found while writing the harness, both fixed in the open before any digit statistic on real data. (1) Store bug: JSON numbers drop trailing zeros, so a streamed quote 1310.70 was stored as 1310.7 and its last character (the digit) would be 7 instead of 0, about 1 tick in 10. Fixed (commit "fix(store): restore trailing zeros..."): quotes are zero-padded to pip_size decimals (never rounded) and pip_size is stored per tick. The collector image built earlier contains the bug: the VM must build from a checkout that includes the fix. No holdout tick was affected (no streamed tick after H0 had been stored). (2) Rule defect: the A1.7 shuffle validity check is impossible for the uniformity test (shuffling keeps counts, so its rate is 0 or 1 and every run would be INVALID). Amendment 3 (committed 2026-10-31T22:52:19Z, epoch 1793487139) uses 200 simulated iid-uniform series of the same length for that test and keeps shuffles for the 3 serial tests, and registers the tick validity rule (quote decimals must equal pip_size, otherwise the tick is excluded and counted). Tests: 172 pass
- 2026-10-31 UTC (22:49Z): pre-push security scan, docs/security-scan.md, tool scripts/scan_history.py (prints paths, hashes and pattern names only; tests prove it never prints a secret). 27 commits on all refs: no key-format secrets, no token file and no real env file ever committed (only .env.example with placeholders); the 4 pattern hits are a Deriv documentation example, a fake OTP used in a redaction test, and a CHANGE_ME database placeholder; legacy/ is not in the repo; no remote is configured and nothing was pushed. Open decisions for the owner before the first push: public or private, the personal e-mail in all commit metadata (a rewrite changes hashes, so do it once before pushing, and H0 must be preserved), and the old token file NAME appearing as text in docs/existing-bot-audit.md and STATE.md
- 2026-10-31 UTC (22:46Z): scripts/coverage_report.py (epochs only; a test guards that its SQL never selects quotes): per UTC day ticks, expected slots, uptime, gaps, missing, longest gap, and total contiguous coverage per confirmatory symbol (segments of at least 10 ticks, zero missing ticks allowed). Eligible ticks so far, all from backfill, none from the local collector (it was stopped before H0): 1HZ100V 447 ticks, uptime 1.0000, 1 segment; R_100 230 ticks, uptime 1.0000, 1 segment. Gate 1 needs 100,000 eligible ticks per confirmatory symbol, so the holdout is far short until the VM collector runs. The local collector file (before H0, excluded) had uptime 0.9574 for both symbols with 2 gaps each (longest 7 s and 8 s). Reports: docs/evidence/coverage_report_eligible-after-H0_20261031T224628Z.json and coverage_report_local-collector-pre-H0_20261031T224628Z.json
- 2026-10-31 UTC: kill-criterion Amendment 2 committed as eeca178 at 2026-10-31T22:45:03Z, before the harness is frozen and before any digit statistic on real data. Contiguous means consecutive epochs differ by exactly the nominal interval (1 s for 1HZ100V, 2 s for R_100; zero missing ticks), because a missing tick turns a lag-1 pair into a lag-2 pair, the real data show exact spacing except at outages, and a strict rule cannot be tuned later. Uniformity uses all eligible digits; runs, autocorrelation and pair independence use contiguous segments of at least 10 ticks only. Sources are merged on (symbol, epoch); conflicting quotes exclude the tick. The A1 text is unedited (a test enforces append-only). Registration is merged from both blocks by src/derivbot/gate1/registration.py. Tests: 153 pass
- 2026-10-31 UTC (backfill run 22:43:44Z): scripts/backfill_holdout.py pulled 1HZ100V and R_100 ticks from H0 (1793486149) to now via ticks_history into data/holdout_backfill.sqlite (git-ignored; back it up), rows tagged source=backfill. Result: 1HZ100V 447 rows 22:35:50Z..22:43:16Z, 447/447 expected, 0 gaps; R_100 230 rows 22:35:50Z..22:43:28Z, 230/230, 0 gaps. Checks passed for both: every stored epoch is strictly after H0 (the amendment's rule is strict, so a tick exactly at H0 is excluded, stricter than the ">=" in the request), no page rejected, reached H0, no silent fallback to the latest window, 19 live-stream quotes equalled the history quotes for the same epochs (0 mismatches; equality only, no digit statistic). Report: docs/evidence/backfill_report_20261031T224344Z.json. A first report had a coverage-ratio bug (199/198) and was replaced after the fix. The window to backfill is only about 8 minutes so far; history retention is between 12 and 24 hours, so any later VM outage can be backfilled only within that time. Tests: 150 pass
- 2026-10-31 UTC: kill-criterion Amendment 1 committed as f0d24e5 at 2026-10-31T22:35:49Z (epoch 1793486149 = H0, the holdout start). The v1 text is kept unedited (only the "(none)" placeholder was replaced; a test enforces it). It discloses what had been observed before each version, freezes two symbol tiers (confirmatory 1HZ100V and R_100; exploratory cryBTCUSD, cryETHUSD, frxEURUSD, frxGBPUSD, frxUSDJPY, frxXAUUSD), registers 4 randomness tests (32 in total: 8 confirmatory, 24 exploratory), Holm at 0.05, minimum samples, break-even rates from observed payouts (0.512821 at stake 1.00, 0.530303 at the 0.35 minimum), the decision rule, the harness control criteria, and one Gate 1 run on 2026-11-15 using ticks with H0 < epoch <= 2026-11-15T00:00:00Z. The registration is machine-readable (src/derivbot/gate1/registration.py) and docs/power-gate1.md is generated from the evidence. No tick analysis has been run. Tests: 135 pass
- 2026-10-31 UTC (committed after 22:24Z): evidence hygiene. Every docs/evidence/*.json now has an `_evidence` block with `captured_at_utc` (UTC, Z). The 10 existing files were stamped retroactively from file modification time, read in UTC with Python (provenance says "reconstructed"); the server timestamps inside the proposal and batch files agree with those times to within 1 second. Probe scripts now stamp at capture. Day-of-week error corrected: all earlier work was done on Saturday 2026-10-31 UTC, from 21:27Z to 22:21Z. The date 2026-11-01 was only the local Nairobi date (UTC+3), and that date is a Sunday. Docs that mixed the two now give exact UTC times. Added derivbot.common.timeutil/evidence, a time-convention section in CLAUDE.md, and tests that fail on a weekday next to the wrong date or an unstamped evidence file. Found two tool traps while doing this: `ls --time-style` and `git log --date=format:` print local/commit-offset times, not UTC
- 2026-10-31 UTC: step 4 tagged every error code literal and the error-shape assumptions in src/ and tests/ with OBSERVED-ERR or UNVERIFIED-ERR; listed in section 9b; added tests/unit/test_unverified_tags.py which fails on an untagged code literal or one missing from STATE.md. Evidence: this commit
- 2026-10-31 UTC: step 3 docs/runbook.md for the Oracle VM (non-root user, SSH-only inbound from owner IP /32, docker compose up, health checks and troubleshooting table, daily off-host backup via systemd timer + rclone, restore drill procedure with real dev-machine output, verification log, known gaps). scripts/backup_daily.sh and deploy/ unit files added. Tested on the dev machine only: the backup/verify/restore tooling and an actual restore comparing identical to live. NOT tested: anything on an Oracle VM, rclone (not installed here), the systemd timer firing, the off-host copy
- 2026-10-31 UTC: step 2 collector Docker setup: Dockerfile.collector (non-root, read-only rootfs, no env secrets), docker-compose.yml (collector only, ./data volume, restart unless-stopped, json-file log rotation, healthcheck that fails if the heartbeat file is stale or an always-open symbol has not ticked in 120s), config/collector.json (1HZ100V, R_100, cryBTCUSD, cryETHUSD always open; frxEURUSD, frxGBPUSD, frxUSDJPY, frxXAUUSD closed-market tolerant), heartbeat log line, silence watchdog (exit 1 after 600s), backup/restore tooling with tests. Built and run live on this machine: healthy, graceful stop in 3s, data persisted across restart, health failure path verified. Found and fixed a reconnect storm caused by subscribing to closed markets (MarketIsClosed). 113 tests pass. NOT verified: the image has not run on the Oracle VM; pip installs are version-pinned but not hash-pinned (the base image is now pinned by digest, 2026-10-31)
- 2026-10-31 UTC: step 1 public-endpoint probes (active_symbols 89 symbols, contracts_for, proposal quotes without auth, ticks_history limits, observed error shapes), evidence: docs/api-notes.md "Public endpoint probes", docs/instruments.md, docs/evidence/. Phase 0 items 1, 2, 5 answered for the PUBLIC view only; DEMO confirmation still needed
- 2026-10-31 UTC: credential-free Phase 3 and collector work. 88 tests pass. Redaction (and a found DEBUG URL leak in websockets), rate limiter, typed messages + request builders checked against vendored schemas (schemas/deriv), fake Deriv server with failure injection (drop before/after processing, delay, duplicate, error, hold/reorder, stream drop, 401), reconnecting public tick stream with stall detection, SQLite store, gap detection, collector CLI. Live public endpoint verified with no credentials. Evidence: commits fb56cd7 through the latest, docs/api-notes.md schema and live sections
- 2026-10-31 UTC: Phase 0 docs-based answers recorded in docs/api-notes.md (items 3, 6, 8 answered; 4, 5, 7 partial; 9, 10 not found); docs/kill-criterion.md pre-registered; derivbot.stats.edge (Wilson, exact binomial, power, Holm) with 14 passing unit tests; docs/power-table.md, evidence: commit after 19af90c
- 2026-10-31 UTC: HANDBOOK saved as docs/HANDBOOK.md, .env.example aligned to it (DERIV_PAT_DEMO etc.), api-notes.md restructured to the ten Phase 0 items, evidence: commit following c399a69
- 2026-10-31 UTC: new repo scaffolded (.gitignore, CLAUDE.md, STATE.md, Makefile, .env.example, docs/api-notes.md skeleton), evidence: first commit in this repo
- 2026-10-31 UTC: read-only audit of the old bot, evidence: docs/existing-bot-audit.md

## 4. In progress

- Phase 0 API verification, branch: main, blocker: needs the owner's NEW App ID (type PAT) and a demo PAT in .env; the old bot's legacy App ID does not work with the new API (HANDBOOK 2.1). Items 9 and 10 can be researched without credentials.

## 5. Next actions (ordered)

000. Owner: deploy the collector to the VM now. Every tick collected after H0 (2026-10-31T22:35:49Z) is holdout. Until the Gate 1 run on 2026-11-15, nobody reads digit values of ticks with epoch > H0 (only counts, min/max epoch, gaps, health). Push the amendment commit to a remote for an external timestamp.
001. Agent (next, per owner decisions): scripts/check_vm_health.sh and runbook update (item 4); then the randomness test harness and the simulated-data control (item 5), record the frozen harness commit in docs/gates.md before 2026-11-14T00:00:00Z.
00. Owner: decide the open items for the runbook (section 1 of docs/runbook.md: your SSH IP, off-host backup target, Oracle shape), then run the VM steps and fill the verification log (runbook section 10).
00b. Owner: decide which non-synthetic symbols to keep collecting. config/collector.json currently has cryBTCUSD, cryETHUSD (always open) and frxEURUSD, frxGBPUSD, frxUSDJPY, frxXAUUSD. Non-synthetics only offer 15 minute or longer fixed-payout contracts or multipliers (api-notes), so they test a different hypothesis than 5-tick digit contracts.
0. Owner: add DERIV_APP_ID_DEMO (and the demo PAT and account ID) to .env. Agents do not open token files; once .env is populated, the held authenticated work above can start.
1. Owner: follow HANDBOOK 12 Step A. Register a NEW application of type PAT at developers.deriv.com (sign up with a different email if it redirects to a trading account), create a demo-scope PAT (`trade` only), and put App ID, PAT and demo account ID in .env (git-ignored, chmod 600). If "Ogutu Bot API TOKEN pat.txt" is that PAT, move it out of the old repo root first; it has not been opened by any agent. Never paste the PAT into chat.
2. Owner: archive the old bot (do not commit it; its token file is not git-ignored).
3. Owner: answer the four questions in HANDBOOK 16 (section 7 below).
4. Agent: complete docs/api-notes.md, ten items, with dated requests and redacted responses. Start with items 9 and 10 and the synthetics description (HANDBOOK 4.2), which need no credentials.
5. Agent: write docs/kill-criterion.md and the power calculation BEFORE any strategy work (HANDBOOK 4.3).
6. Phase 3: fake Deriv server and adapter tests first, then the adapter. Demo/real check must fail closed. Create .claude/skills per HANDBOOK 15.2 at the start of Phase 3.

## 6. Decisions made by the owner

- Build from scratch, no open-source framework (NautilusTrader, Freqtrade, LEAN, Hummingbot rejected).
- Demo mode only. Real trading stays disabled.
- Hosting: Oracle Cloud VM (free trial, stay within Always Free limits where possible).
- Funding is manual through Deriv's cashier. The bot never moves money.
- Instruments: OPEN, not yet decided.
- Maximum acceptable loss if ever live: OPEN.
- Telegram chat IDs allowed to control the bot: OPEN.

## 7. Open questions for the owner

1. Which instruments, based on what active_symbols and contracts_for return for this account?
2. Telegram chat ID(s)?
3. Does the old bot still run on the VPS? Which account type does its token belong to?
4. Does the VPS Postgres hold any real trade history from the old bot?
5. Who will independently review the work in Phase 10 (must not be the agent session that wrote the code)?
6. Maximum total amount you could lose entirely if you ever go live ("none, demo only" is acceptable)?
7. Is the credential in "Ogutu Bot API TOKEN pat.txt" a Deriv PAT for the new API, or something else?

Resolved: HANDBOOK is now at docs/HANDBOOK.md. The new API uses REST OTP plus WebSocket (HANDBOOK 2.1 and 2.2), not the legacy authorize message.

## 8. Facts about the old bot (from the read-only audit, docs/existing-bot-audit.md)

- No trade history in the dev database: 1,160 ticks (R_100, 2026-04-04 to 2026-04-07), zero trades.
- backtest_results.json looks synthetic or stale. No evidentiary weight.
- Working tree and HEAD are different bots. HEAD used an LLM strategy. Working tree is rule-based and data-collection-first.
- Risk controls defeated by code (found by reading, not run): loss pause cleared by the Redis watcher within about a second, drawdown peak overwritten on every balance update, Docker restarts after a halt.
- Stake sizing bugs: Kelly fed gross payout where net is expected, recomputed stake never used for buy.
- No demo/real switch and no account ID check.
- Token file in the old repo root is not git-ignored. Never commit the old tree.
- Old test suite: 297 passed, but it does not catch the defects above. Do not treat it as safety evidence.
- Unverified: runtime behaviour of the defeated controls, buy-timeout edge cases, VPS Postgres contents, whether real mode was ever used.

## 9. UNVERIFIED items carried forward

- OTP single-use and lifetime
- Whether buy has an idempotency key
- Contract types, durations, and real payout ratios for this account
- ticks_history count limits
- Legacy endpoint deprecation date
- Licences of Freqtrade, LEAN, Hummingbot
- Whether automated trading is permitted for the owner's account type

## 9b. Assumed error codes and error shapes (UNVERIFIED unless marked OBSERVED)

Source of truth for tags: comments `OBSERVED-ERR:` and `UNVERIFIED-ERR:` in src/ and tests/. tests/unit/test_unverified_tags.py fails if a code literal is untagged or missing from this list. Update both together.

OBSERVED live on the PUBLIC endpoint, 2026-10-31T22:03Z (docs/api-notes.md "Error objects actually observed"; raw in docs/evidence/public_probe_batch.json). Still UNVERIFIED on the authenticated demo endpoint:

| Code or shape | Where used | Status |
|---|---|---|
| Error object shape `{"error": {"code", "message", "details"?}, "msg_type", "req_id", "echo_req"}` | src/derivbot/deriv/errors.py, messages.py | OBSERVED for proposal, ticks, balance, portfolio (public). UNVERIFIED for buy, sell, proposal_open_contract, any authenticated path |
| `MarketIsClosed` (msg_type `ticks`) | src/derivbot/deriv/public_stream.py, fake server | OBSERVED (public). UNVERIFIED on demo |
| `ContractBuyValidationError`, `InvalidInputAsset`, `AuthorizationRequired`, `InputValidationFailed` | not in code; recorded in api-notes only | OBSERVED (public), no code depends on them yet |

ASSUMED, never observed (UNVERIFIED):

| Code or shape | Where used | Note |
|---|---|---|
| `InsufficientBalance` | tests/unit/test_models.py | The old bot's string from the legacy API. Real code on the new API unknown |
| Rate-limit rejection code and shape | src/derivbot/deriv/ratelimit.py (penalize docstring), tests use `FakeRateLimit` | Unknown. No code detects a server rate-limit rejection yet |
| Error on buying an unknown or already used proposal id | fake server (`FakeInvalidProposal`) | Real code and shape unknown. The docs say a proposal id cannot be bought twice but give no error |
| Error for an unknown contract_id | fake server (`FakeContractNotFound`) | Unknown |
| Error for an unsupported request | fake server (`FakeUnsupported`) | Invented |
| `FakeError` (default injected code), `FakeRateLimit`, `SomethingElse` | tests | Invented placeholders, never claims about Deriv |
| Handshake rejection as HTTP 401 | fake server `reject_handshake_status`, tests | The docs describe 401 for REST. Behaviour of the WebSocket upgrade with a bad or reused OTP is unknown |
| OTP expired or reused error | none yet | Unknown (api-notes item 6) |
| Errors for market closed on `buy`, for stake out of range on `buy`, for price moved | none yet | Unknown. Proposal-time equivalents were observed on the public endpoint only |
| Server does not enforce the schemas' `additionalProperties: false` | api-notes | OBSERVED on `ping` only (extra field accepted) |

Other assumed response values (not errors, same rule):

- `proposal_open_contract.status` values `won` / `lost` at expiry (fake server and one test use them). The schema only says `sold` for early sale. UNVERIFIED.
- `OpenContract.is_settled` treats `is_sold == 1` or `is_expired == 1` as settled. Which flag Deriv sets for tick contracts is UNVERIFIED.

## 10. How to resume (fresh session checklist)

1. Read this file, CLAUDE.md, then docs/HANDBOOK.md sections relevant to the current phase.
2. Run `git log --oneline -20` and `git status`. Compare against section 3.
3. Run `make test`. Report the real result.
4. Pick the first item in section 5. Do not skip phases or gates.
5. Before ending a session: update sections 2 to 5, commit, and write the commit hash at the top.

## 11. Rules that never change

See CLAUDE.md. Demo only, no secrets in logs or git, every trade risk-validated, no blind buy retries, no martingale, no fabricated results, no em dashes in documents.
