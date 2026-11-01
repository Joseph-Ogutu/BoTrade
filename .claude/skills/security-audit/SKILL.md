---
name: security-audit
description: Use for any security review, before a commit that touches credentials, endpoints, the real-trading guard, Docker or CI, or when asked to audit. Covers the handbook section 11 checklist, secret scanning and the real-guard review. Review only; do not review code you wrote in the same session.
---

# security-audit

## When to use
Before merging changes to `guard/`, `src/derivbot/config/settings.py`, `api/`, `killswitch/`, Dockerfiles, compose, CI, or anything printing or storing a credential. Also on request.

## Independence
The author does not audit their own change. For a release-level review hand `docs/review-security.md` and `docs/review-risk-executor.md` to a separate reviewer or session.

## Checklist (HANDBOOK section 11)
1. Secrets: none in git, logs, tests, docs, chat. `.env`, `secrets/`, `dev-local/`, token files and `REAL_TRADING_APPROVED` are git-ignored. Run `.venv/bin/python scripts/scan_history.py` (all refs; prints only pattern names, commits and paths). The OTP URL is a secret.
2. Logging: redaction on every path that can print a URL, header or exception text. Check with a registered fake token.
3. Real-money guard (`guard/real_guard.py`): all five conditions required, plus the build lock `REAL_TRADING_IMPLEMENTED = False`. Layers: endpoint allowlist, OTP provider refuses non-demo URL and uses demo credentials only, connect-time demo verification (endpoint demo AND loginid equals configured demo account), risk rule 2, executor re-read before buy, database CHECK `mode='demo'`, approval file git-ignored, unticked `docs/live-readiness.md`. `tests/unit/test_real_unreachable.py` enumerates them. Any change that removes a layer needs the owner.
4. Network: no published ports; admin API loopback only; Postgres and Redis on an internal network; Redis has a password; containers non-root, read-only root, capabilities dropped, `no-new-privileges`.
5. Supply chain: pinned versions and base image digests; `pip-audit` clean (`make audit`); no new dependency without a reason.
6. Kill switch: never depends on Deriv; engage never raises; release refused unless it can be logged.
7. Data integrity: audit_events append-only (triggers on UPDATE, DELETE, TRUNCATE); holdout untouched; permit-gated loaders intact.

## Validation
- `make check` (ruff, mypy, coverage gate, pip-audit, secret scan, compose config).
- `.venv/bin/python -m pytest tests/unit/test_real_guard.py tests/unit/test_real_unreachable.py tests/unit/test_scan_history.py tests/db/test_schema.py -o addopts="" -q`

## Worked example: review a new Telegram command
1. Is the command matched exactly and only for allowlisted chat ids? Can it release the kill switch or change a setting? (It must not.)
2. Does any error path put the bot token in text? Telegram puts the token in the request URL; check the transport redacts it.
3. Add a test with a non-allowlisted chat and with a near-miss command (`/kill_now`).
