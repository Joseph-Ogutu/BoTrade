# Review brief: security (secrets, real-money guard, network, containers, supply chain)

Audience: an independent reviewer who did not write this code. The author cannot review this.

Status: nothing has been deployed and no credential has ever been used. Docker images were built and the compose file was validated on the development machine only. The CI workflow has never run on GitHub. Your report is the check the author could not do.

## Scope

| Area | Files |
|---|---|
| Real-money guard | `src/derivbot/guard/real_guard.py`, `endpoint.py`, `otp.py`, `verify.py`, `docs/live-readiness.md` |
| Settings and secrets | `src/derivbot/config/settings.py`, `.env.example`, `.gitignore`, `.dockerignore`, `scripts/init_secrets.sh` |
| Redaction and logging | `src/derivbot/common/redaction.py`, `logs.py`, `notify/notifier.py`, `notify/telegram.py` |
| Admin surface | `src/derivbot/api/app.py`, `src/derivbot/opsctl.py`, `killswitch/telegram_commands.py` |
| Database | `migrations/versions/0001_initial_schema.py` (CHECK constraints, append-only audit triggers) |
| Containers and CI | `Dockerfile.bot`, `Dockerfile.collector`, `docker-compose.yml`, `.github/workflows/ci.yml`, `.pre-commit-config.yaml` |
| History scan | `scripts/scan_history.py`, `docs/security-scan.md` |

Out of scope: `src/derivbot/gate1/` (frozen, separate review).

## How to run what exists

```
make check                                  # ruff, mypy, coverage gate, pip-audit, history scan, compose config
.venv/bin/python scripts/scan_history.py    # prints pattern names, commits and paths only
.venv/bin/python -m pytest tests/unit/test_real_guard.py tests/unit/test_real_unreachable.py tests/unit/test_otp_and_verify.py -o addopts="" -q
docker compose config -q
```
Independently verify rather than trust: run your own secret scanner (for example gitleaks or trufflehog) over all refs, and `pip-audit` with your own database snapshot.

## Questions and attack list

### A. Can real money be reached?
Defence layers (all must hold independently): build lock `REAL_TRADING_IMPLEMENTED = False`; endpoint allowlist accepting only the exact demo WebSocket path; OTP provider using demo credentials only and discarding any non-demo URL; connect-time verification (endpoint classifies demo AND balance loginid equals the configured demo account, anything unknown fails closed); risk rule `mode_and_account_consistent`; executor re-reads demo-ness before the buy; database CHECK `mode='demo'` on `order_intents` and `orders`; `REAL_TRADING_APPROVED` git-ignored and absent; `docs/live-readiness.md` unticked.
- Try to reach a real endpoint with only configuration changes (env vars, compose, settings file). `TRADING_MODE`, `ALLOW_REAL_TRADING` are hard-coded in compose; verify no override path exists in `app.main`.
- URL parsing: userinfo tricks, lookalike hosts, trailing dots, uppercase, IDN, port, path traversal, extra query parameters.
- Does anything other than `guard/` and `settings` read real credentials (`DERIV_PAT_REAL` and friends)?
- The legacy real-money loginid prefix list (CR, MF, MLT, MX) is a guess (UNVERIFIED).

### B. Secrets
- Is any secret reachable in git history, build context, image layers (`docker history`), environment of the running container, logs, alerts, exceptions, or `repr` of a settings object? Settings uses `SecretStr`; check every `str(exc)` path, including httpx errors that embed URLs, and the Telegram token that travels in a request URL.
- OTP WebSocket URLs are secrets: are they ever logged or stored (including in the audit table payloads)?
- `.dockerignore` excludes `secrets`, `dev-local`, `.env*`. Confirm with `docker build` and inspect the image filesystem.
- `scripts/init_secrets.sh` makes files mode 0644 inside a 0700 directory so non-root containers can read them. Judge whether that is acceptable; propose better if not.
- A file named "Ogutu Bot API TOKEN pat.txt" exists in the OLD bot's directory (outside this repository, untracked). Confirm it is not in this repository or its history, and advise on rotating that token if it was ever exposed.

### C. Admin surface and dashboard
- The admin API now also serves a dashboard (`src/derivbot/api/static/dashboard.html`, `api/dashboard.py`, `api/accounts_view.py`). In the container it binds all interfaces ONLY when `ADMIN_API_BIND=container` is set, and compose publishes it on the host loopback only (`127.0.0.1:8787`). Judge whether another container on the `backend` or `egress` networks could reach it (bearer token still required) and whether that is acceptable.
- `accounts_view.py` makes one read-only GET to the accounts list with the DEMO token and returns the REAL account's balance. Check it cannot be steered to another path, that account ids never reach the page, and that the real account is never used for OTP, WebSocket or any write. A Trade-scope token can see the real account (OBSERVED), so confirm no other code path could act on it.
- Page: strict Content-Security-Policy by hash, no inline handlers or styles, no innerHTML, token in sessionStorage only, the only write is the kill switch, release is not offered. Try XSS through any field that originates from the database or the exchange (symbols, states, reason text).

- Bearer comparison: constant time? Behavior with no token configured (503)? Brute-force exposure?
- Bind validation to 127.0.0.1 or ::1 only; verify from the container network that the port is not reachable from other containers on `backend` (it should not be: loopback inside the bot container).
- Telegram: allowlist, exact command match, no release or setting change available.

### C1. Password login (added after the first dashboard)
- `api/passwords.py` (scrypt, parameters inside the stored string), `SessionStore` and the login, logout and auth-methods routes in `api/app.py`, `scripts/set_dashboard_password.py`. Review: scrypt cost and memory against the container limit, timing of verify, the session store (only hashes kept, bounded, idle and absolute expiry, invalidated by a password change), cookie flags and the `Secure` decision behind a proxy, the CSRF header requirement, that release is bearer-only, that a missing credential is not counted as a guess (and whether that opens a hole), and that the password hash file is readable by the container user (mode 0644 inside a 0700 directory).
- No second factor and no per-user accounts. Judge whether password plus lockout is enough for a page that shows balances and can engage the kill switch.

### C2. Public HTTPS front (optional profile)
- `deploy/Caddyfile` and the `caddy` compose service expose the dashboard at a public name. Review: the lockout (`AuthLimiter`, `client_ip` in `api/app.py`) including whether the forwarded-address handling can be abused, the 404 on `/release`, that no other path of the admin API is reachable that should not be, TLS settings, the Caddy image pin, and the absence of a second factor (is a 256 bit token plus lockout acceptable for a page that can engage the kill switch and shows balances?).
- Docker publishes ports through its own firewall chains: confirm the Oracle security list, not host iptables, is the real gate, and that only 80 and 443 are open.

### D. Database
- Audit table is append-only through row triggers (UPDATE, DELETE) and a statement trigger (TRUNCATE). Can the application role bypass them (owner can disable triggers; consider a separate role)? The bot connects as the database owner today: judge and recommend.
- SQL injection surface in `db/repo.py` and `audit.py` (all parameterised?).

### E. Containers, network, CI
- Read-only root, `cap_drop: ALL`, `no-new-privileges`, non-root: confirm in `docker inspect`. Postgres has `cap_add` for its entrypoint; judge it.
- `backend` network is `internal: true`; only `bot` (and `collector`) have egress. Confirm Postgres and Redis cannot reach the internet.
- Base image digests pinned; Python requirements pinned by version but NOT by hash (pip-audit warns). Recommend whether to add hashes.
- CI: `permissions: contents: read`, third-party actions pinned by tag only (`actions/checkout@v4`, `actions/setup-python@v5`), not by SHA. Judge.
- The test database password in CI is a throwaway written into the workflow.

### F. Data integrity controls (security-adjacent)
- Permit-gated real-data loaders (`backtest/permit.py`, `gate1/loader.py`) and the holdout in `data/` (git-ignored). Can any non-gated path read the holdout?

## Known gaps the author already knows

- Real Telegram transport, OTP REST call, and all authenticated Deriv behavior have never run.
- No log shipping; no alert on backup failure; no restart-storm alert.
- The VM, host hardening, firewall rules and the off-host backup (rclone) are described in `docs/runbook.md` but UNVERIFIED on a real VM.
- No rate limiting or lockout on the admin API (loopback only).
- Requirements are not hash-pinned.

## Deliverable

A written report: findings ranked by severity with a reproduction for each, a list of what you could not assess, the commit reviewed (`git rev-parse HEAD`), and a clear statement of whether you consider the demo-only guarantee sound. Do not modify code during the review.
