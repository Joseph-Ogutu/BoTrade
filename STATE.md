# STATE.md: Deriv Bot Revamp, session handoff

Any agent starting a session reads this file FIRST, then CLAUDE.md, then the relevant part of docs/HANDBOOK.md.
Update this file and commit after every completed task. If it is not in this file and in git, it did not happen.

Last updated: 2026-11-01 (UTC) by: Claude Code session (repo scaffold)
Git HEAD when updated: c399a69 (scaffold commit)

## 1. Project in one paragraph

Personal Deriv trading bot, rebuilt from scratch as a thin asyncio Python service. Demo only. Profitability is unproven and must pass the pre-registered edge test (HANDBOOK section 4) before anything else matters. The old bot is archived and is not to be reused without owner approval.

## 2. Where we are

Current phase: Phase 0 (API verification) and housekeeping
Current gate: none passed yet

Phase checklist (tick only when acceptance criteria in HANDBOOK section 13 are met and evidence is committed):

- [ ] Housekeeping: old bot archived, new repo created, .gitignore covers env and token files, no token file inside the repo
  - done: new repo created at ~/Desktop/deriv-bot-revamp, .gitignore covers env and token files, no token file inside it
  - not done: old bot not yet archived (owner action), token file still sits in the old repo root (see section 5)
- [ ] Phase 0: docs/api-notes.md answers all ten items in HANDBOOK section 3
- [ ] Phase 3: Deriv adapter, smoke test passes on DEMO, fake Deriv server tests pass
- [ ] Phase 4: collector running, gap report, candle rebuild test
- [ ] Gate 1 prep: docs/kill-criterion.md committed BEFORE any strategy test, power calculation done
- [ ] Gate 1: edge test on locked holdout, control experiment passed
- [ ] Phase 5 and 6: strategies and backtests
- [ ] Phase 7: risk engine, kill switch, state machine, reconciler
- [ ] Phase 8: demo execution
- [ ] Gate 2: demo matches backtest
- [ ] Phase 9: Oracle deployment
- [ ] Phase 10: independent evaluation
- [ ] Gate 3: live readiness (owner only)

## 3. Done so far (append, newest first)

- 2026-11-01: HANDBOOK saved as docs/HANDBOOK.md, .env.example aligned to it (DERIV_PAT_DEMO etc.), api-notes.md restructured to the ten Phase 0 items, evidence: commit following c399a69
- 2026-11-01: new repo scaffolded (.gitignore, CLAUDE.md, STATE.md, Makefile, .env.example, docs/api-notes.md skeleton), evidence: first commit in this repo
- 2026-11-01: read-only audit of the old bot, evidence: docs/existing-bot-audit.md

## 4. In progress

- Phase 0 API verification, branch: main, blocker: needs the owner's NEW App ID (type PAT) and a demo PAT in .env; the old bot's legacy App ID does not work with the new API (HANDBOOK 2.1). Items 9 and 10 can be researched without credentials.

## 5. Next actions (ordered)

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

## 10. How to resume (fresh session checklist)

1. Read this file, CLAUDE.md, then docs/HANDBOOK.md sections relevant to the current phase.
2. Run `git log --oneline -20` and `git status`. Compare against section 3.
3. Run `make test`. Report the real result.
4. Pick the first item in section 5. Do not skip phases or gates.
5. Before ending a session: update sections 2 to 5, commit, and write the commit hash at the top.

## 11. Rules that never change

See CLAUDE.md. Demo only, no secrets in logs or git, every trade risk-validated, no blind buy retries, no martingale, no fabricated results, no em dashes in documents.
