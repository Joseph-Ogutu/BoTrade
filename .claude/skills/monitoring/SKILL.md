---
name: monitoring
description: Use when adding or changing heartbeats, alerts, the daily report, healthchecks, logging, or the admin and Telegram status surfaces. Covers what must be observable and how it is tested.
---

# monitoring

## When to use
`src/derivbot/notify/`, `src/derivbot/api/app.py`, `src/derivbot/healthcheck_bot.py`, `src/derivbot/collector/healthcheck.py`, `src/derivbot/opsctl.py`, healthchecks in `docker-compose.yml`.

## What exists
- Notifier (`notify/notifier.py`): redacts registered secrets, OTP URL query strings and bearer tokens; bounded to 3500 characters; level filter; duplicate suppression with a visible count; a failing transport is counted and never raises; `submit()` is a non-blocking queue for synchronous code, drained on shutdown. Alerts are also logged, so a missing Telegram token does not hide them.
- Heartbeater: beats the Redis heartbeat key every interval, sends the Telegram heartbeat on a slower schedule, pings an optional external dead-man's switch, counts (does not propagate) its failures.
- Daily report (`notify/report.py`): built from the database (intents by state, wins and losses and P&L, risk rejections by rule, balances, open or unresolved exposure, reconciliation runs, kill switch events). Sent once per UTC day after the configured hour; the last-sent day lives in the KV store so a restart does not resend.
- Health: `python -m derivbot.healthcheck_bot` (admin API answers and heartbeat under 60 s); collector healthcheck (heartbeat file and per-symbol tick age); postgres `pg_isready`; redis `PING`.
- Admin API (`api/app.py`): `/health`, `/status`, `/kill`, `/release` only, bearer token, 503 if no token is configured, bound to 127.0.0.1 or ::1 only (validated), no docs routes. Telegram accepts exactly `/status`, `/pause`, `/kill` from allowlisted chat ids; there is no release or setting change over Telegram.

## Instructions
1. Every new alert has a level, a one-line cause and the next action. Never put a secret, an OTP URL or an account id in an alert.
2. Log with `logging`, UTC, via `setup_logging`; sensitive values pass through `common.redaction`.
3. A new healthcheck must fail closed (unknown means unhealthy) and be cheap.
4. Test with the fake transport (`notify/transport.py`), never a real Telegram.

## Validation
- `.venv/bin/python -m pytest tests/unit/test_notifier.py tests/unit/test_admin_api.py tests/unit/test_admin_api_binding.py tests/db/test_report.py -o addopts="" -q`
- `scripts/opsctl.sh status` shows the live state in the container.

## Known gaps
No log shipping, no alert on a stale backup marker, no restart-storm alert, real Telegram transport never run (UNVERIFIED). See docs/runbook.md section 12.6.

## Worked example: alert when the last reconcile is older than the limit
1. Add the condition to the status callable; test with a fake clock that the alert fires once and is suppressed on repeat with a count.
2. Confirm the text contains no secret by running the redaction test with a registered fake token.
