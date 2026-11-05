# BoTrade: Deriv demo trading bot

A rebuild from scratch of a Deriv trading bot, built to be tested and audited before it is trusted. **Demo account only.** Real trading is disabled and unreachable in this build.

## Status

- The trading service, risk engine, order state machine, executor, reconciler, kill switch, notifier and admin API are built and tested against a fake Deriv server with failure injection.
- Verified once on the live **demo** API: account list, OTP, WebSocket connection, balance, minimum stake, one 0.35 USD contract end to end through the real executor path. Details and the remaining unknowns are in [docs/api-notes.md](docs/api-notes.md).
- **No strategy is approved, so the bot places no trades.** Strategies are coded as pure deterministic functions and pre-registered in [docs/hypotheses/](docs/hypotheses/INDEX.md). They stay blocked until the statistical Gate 1 reports and the owner approves in [docs/approvals.md](docs/approvals.md).
- Current state and the resume point: [STATE.md](STATE.md).

## Rules the code enforces

- No LLM in the trading loop. No martingale. A stake never rises after a loss.
- Every trade passes the risk engine before any buy message. All rules are always evaluated and recorded; unknown inputs fail closed.
- A lost buy reply is resolved by looking at the exchange, never by buying again. The reconciler alerts and pauses; it never silently fixes.
- The kill switch keeps its state in four places, never depends on Deriv, and is not released without a logged reason.
- Real trading is blocked by layered guards: a build lock, an endpoint allowlist, demo-only OTP, connect-time demo verification, a risk rule, an executor re-check and a database constraint.
- Real collected tick data is gated: no statistic is run on it before Gate 1 and an owner approval line.

## Layout

| Path | Contents |
|---|---|
| `src/derivbot/risk`, `execution`, `killswitch`, `guard` | Risk rules and stake sizing, executor and reconciler, kill switch, real-trading guard |
| `src/derivbot/deriv`, `collector` | Deriv API session, rate limiter, public tick collector |
| `src/derivbot/signals`, `backtest`, `gate1` | Strategies, contract-level backtester, Gate 1 statistical harness (under independent review) |
| `src/derivbot/db`, `migrations` | Postgres schema, append-only audit trail, Alembic migrations |
| `tests` | Unit, database, execution and integration tests plus the fake Deriv server |
| `docker-compose.yml`, `Dockerfile.*` | Postgres, Redis, migrate, collector and bot |
| `docs` | Handbook, API notes, runbook, kill criterion, review briefs |

## Run the tests

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
bash scripts/dev_up.sh                # local Postgres and Redis for the integration tests
.venv/bin/python -m pytest -o addopts="" -q
make check                            # ruff, mypy, coverage gate, pip-audit, secret scan, compose config
```

DB and Redis tests skip, and say so, if the services are down.

## Deploy (demo, not yet done)

See [docs/runbook.md](docs/runbook.md), section 12. In short: `bash scripts/init_secrets.sh`, put the Deriv demo token in `secrets/deriv_pat_demo`, set `DERIV_APP_ID_DEMO` and `DERIV_ACCOUNT_ID_DEMO` in `.env`, then `docker compose up -d --build`. Risk limits live in [config/risk.json](config/risk.json). Secrets are never committed.

## Documents

[STATE.md](STATE.md) | [CLAUDE.md](CLAUDE.md) (rules for agents) | [docs/HANDBOOK.md](docs/HANDBOOK.md) | [docs/runbook.md](docs/runbook.md) | [docs/api-notes.md](docs/api-notes.md) | [docs/kill-criterion.md](docs/kill-criterion.md) | [docs/review-risk-executor.md](docs/review-risk-executor.md) | [docs/review-security.md](docs/review-security.md)

## Disclaimer

Research and engineering project. Trading carries risk, and a bot passing its tests says nothing about profit. Automated trading terms for your account type are the owner's responsibility to check.
