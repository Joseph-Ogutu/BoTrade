# CLAUDE.md: rules for any agent working on the Deriv Bot Revamp

Read STATE.md first, then this file, then the relevant part of docs/HANDBOOK.md (not yet supplied, see STATE.md section 7).

## Non-negotiable rules

1. Demo only. Real trading stays disabled. Code must refuse to trade unless the authorized account is a demo account (loginid check), and must fail closed if the account type cannot be determined.
2. No secrets in git, logs, tests, docs, or chat. Credentials come from git-ignored `.env` (names in `.env.example`: DERIV_APP_ID_DEMO, DERIV_PAT_DEMO, DERIV_ACCOUNT_ID_DEMO). The OTP WebSocket URL is a secret too. Never print, echo, cat, or log a token or OTP URL, and never open `.env` or token files. Redact as [REDACTED].
2a. Never set TRADING_MODE=real, ALLOW_REAL_TRADING=true, or create REAL_TRADING_APPROVED (HANDBOOK 11.4). Leave real credentials empty.
2b. No LLM in the trading loop. Live decisions are deterministic code (HANDBOOK 15.1). Do not use the server-side Automation API for decisions.
3. Every trade passes the risk engine before any buy message is sent. No code path may call buy without a recorded risk approval.
4. No blind buy retries. After a buy timeout or disconnect, reconcile against the portfolio before any further trade. Use an idempotency key only if verified in docs/api-notes.md.
5. No martingale, no stake increase after a loss, no loss-chasing.
6. No fabricated results. Backtests, metrics and evidence come only from real data and a committed command that reproduces them. Say UNVERIFIED when something was not checked.
7. The old bot is archived and not reused without owner approval. Never commit or copy its tree. Lessons are in docs/existing-bot-audit.md.
8. Do not skip phases or gates. Strategy testing starts only after docs/kill-criterion.md is committed.
9. Build from scratch. No NautilusTrader, Freqtrade, LEAN or Hummingbot.
10. No em dashes in documents.

## Working method

- Small commits, format `type(scope): description`. Commit authored by the owner only. No co-author lines.
- Tests first for new logic. Run `make test` and report the real result, including failures.
- Update STATE.md and commit after every completed task.
- Write tests that target the failure modes from the audit (defeated breakers, buy timeout, disconnect during settlement), not only the happy path.
- Use a fake Deriv server for adapter tests. Never point tests at a real endpoint.
