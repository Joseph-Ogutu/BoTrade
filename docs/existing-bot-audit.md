# Existing Bot Audit

Read-only audit of the Deriv digit-trading bot in this repository. Nothing was modified, the bot was not started, no real or demo account was contacted, and no environment or token file was opened. The only exception to "nothing modified" is this file.

Audit date: 2026-11-01. Branch: master. HEAD: ddcb213.

Conventions: UNVERIFIED marks anything that could not be confirmed from the code or from files on disk. All secrets are shown as [REDACTED] or omitted. Line numbers are per file.

Important framing: the working tree differs from HEAD (see section I, item 1). Where behaviour differs, both states are stated. Section C describes the working tree because that is what would run if it were started now.

---

## A. Inventory

### A1. Repo tree (3 levels, excluding .git, venvs, caches)

```
.
├── backtest_results.json
├── bot/
│   ├── __main__.py, main.py, config.py, scheduler.py, logging_config.py
│   ├── backtest/    bias_guard.py loader.py metrics.py runner.py simulator.py __main__.py
│   ├── connection/  client.py state.py
│   ├── control/     pause_watcher.py
│   ├── data/        db.py extractor.py subscriber.py writer.py schema.sql schema_sqlite.sql
│   ├── executor/    errors.py executor.py logger.py monitor.py proposal.py reconciler.py
│   ├── risk/        circuit_breakers.py client.py counters.py kelly.py manager.py reconciler.py
│   ├── strategy/    base.py chi_filter.py even_odd.py factory.py matches_differs.py
│   │                orchestrator.py over_under.py window.py
│   └── tests/       26 test files + conftest.py
├── tests/risk/      6 test files + conftest.py
├── dashboard/       Django 4.2 + DRF app (api/, dashboard/, ui/, templates/, manage.py)
├── nginx/dashboard.conf
├── scripts/         smoke_test.py smoke_test.sh vps_setup.sh
├── Dockerfile.bot, Dockerfile.dashboard, docker-compose.yml
├── DEPLOYMENT.md, .env.example, .env.production.template
├── pyproject.toml, requirements.txt, requirements.bot.txt
├── deriv_bot_dev.db            (untracked SQLite dev DB)
├── Ogutu Bot API TOKEN pat.txt (untracked, contents not opened, see H)
├── .planning/                  (GSD planning docs: PROJECT, ROADMAP, STATE, phases/, research/)
└── .claude/worktrees/          (4 agent worktrees, ignored by git)
```

No `docs/` directory existed before this audit.

### A2. Language, framework, dependencies

Python. Local venv interpreter is 3.12.3; Dockerfile.bot uses `python:3.11-slim`. Async design (asyncio + websockets). Dashboard is Django plus Django REST Framework.

Bot, as pinned in `requirements.bot.txt` (working tree) with installed versions from `.venv`:

| Package | Spec in requirements.bot.txt | Installed in .venv |
|---|---|---|
| websockets | ==12.0 | 12.0 |
| pydantic-settings | >=2.13,<3 | 2.13.1 |
| APScheduler | >=3.11,<4 | 3.11.2 |
| asyncpg | >=0.31,<1 | 0.31.0 |
| aiosqlite | >=0.20,<1 | 0.22.1 |
| scipy | >=1.11,<2 | 1.17.1 |
| pandas | >=2.0,<3 | 3.0.2 |
| numpy | >=1.26,<3 | 2.4.4 |
| redis | >=5.0,<6 | 5.3.1 |

`requirements.txt` (dev) is the same list plus `pytest>=9.0` (installed 9.0.2) and `pytest-asyncio>=1.0` (installed 1.3.0), minus pandas, numpy, redis.

`anthropic>=0.40,<1` was present in both requirements files at HEAD and is removed in the working tree (still installed in .venv as 0.125.0).

Dashboard (`dashboard/requirements.txt`): Django==4.2.15, djangorestframework==3.15.2, psycopg2-binary==2.9.9, redis==5.0.8, python-dotenv==1.0.1, django-filter==24.3, fakeredis==2.23.3, scipy>=1.11.

Infra images (docker-compose.yml): postgres:16-alpine, redis:7-alpine (appendonly yes).

### A3. Last 20 commits

```
ddcb213 2026-04-11 fix(executor+strategy): one trade per signal; pause on insufficient balance
fa7f172 2026-04-11 fix(executor+strategy): fix Kelly zero-stake and non-JSON Claude response
509c1e4 2026-04-11 fix(strategy): lower confidence threshold 0.65 → 0.60; sharpen prompt
79eecb2 2026-04-11 feat(market): switch from R_100 to Volatility 10 Index (V10)
751f927 2026-04-10 fix(strategy): lower confidence threshold 0.75 → 0.65
ffdcc13 2026-04-10 fix(deps): add anthropic package to requirements.bot.txt
853f5b1 2026-04-10 feat(strategy): replace chi-square with Claude Sonnet AI strategy
65f216a 2026-04-10 fix(strategy): raise signal confidence bar to reduce noise trades
567b65e 2026-04-10 fix(executor): reject low-payout trades; disable over_under strategy
6073300 2026-04-10 fix(bot): sync risk:peak_balance from real Deriv balance on connect
c09836c 2026-04-10 fix(strategy): window=20, threshold=0.20, min_sample=20 for R_100
b3759dd 2026-04-10 fix(strategy): reduce window size from 200 to 50 ticks
5de9641 2026-04-10 fix(dashboard): fix pause button auth and add mobile responsiveness
3235a3a 2026-04-10 fix(strategy): raise p-value threshold from 0.05 to 0.10
a2e6c2c 2026-04-08 fix(bot): log account loginid, currency and balance on authorization
f8ae258 2026-04-08 feat(bot): subscribe to Deriv balance feed and sync real balance to Redis
8c83a38 2026-04-08 fix(dashboard): fix logout and session expiry
88eb68d 2026-04-08 fix(dashboard): map Trade.timestamp to settled_at DB column
56f35c6 2026-04-08 chore(08-01): cherry-pick STATIC_ROOT, backtest_results.json, and Phase 8 SUMMARY
37ae01f 2026-04-08 feat(07): publish bot state to Redis on connect and per tick for dashboard status
```

177 commits across all refs; first commit is 4459d65 (2026-04-01). Remote: `origin` on GitHub (SSH URL). Branches: master, remotes/origin/master, and four `worktree-agent-*` branches.

Observation from the log: the strategy was tuned by repeatedly changing thresholds (0.05 to 0.10, window 200 to 50 to 20, confidence 0.75 to 0.65 to 0.60) over 2026-04-10 and 04-11, after which the strategy was replaced with an LLM call. The pattern suggests live or demo results were being reacted to, but no result files exist to confirm it (see F).

### A4. How it is run and where it was deployed

- Entrypoint: `python -m bot.main` (`bot/__main__.py` calls `bot.main.run()`, which does `asyncio.run(main())`). Backtest: `python -m bot.backtest`.
- Docker: `Dockerfile.bot` ends with `CMD ["python", "-m", "bot.main"]`, runs as non-root `botuser`. `docker-compose.yml` defines four services: postgres, redis, bot, dashboard. All have `restart: unless-stopped`. The dashboard (gunicorn, port 8000) is bound to 127.0.0.1 and fronted by host Nginx (`nginx/dashboard.conf`).
- systemd: no unit files. The only systemd use is `scripts/vps_setup.sh` enabling `docker.service` and `nginx`.
- cron: none. In-process APScheduler is created in `bot/scheduler.py` (32 lines) and started in `bot/main.py`; no jobs were found registered in main.py. UNVERIFIED whether `build_scheduler()` registers any job (not read in detail).
- Deployment target: a Linux VPS using Ubuntu apt repos. `scripts/vps_setup.sh` mentions OCI console security-list rules, so the intended host is Oracle Cloud (OCI) Ubuntu. Whether it was actually deployed there: UNVERIFIED. No deploy logs, IPs, or hostnames are recorded in git. Phase 8 ("Docker + VPS deployment") is in `.planning/`, and `scripts/smoke_test.sh`, `DEPLOYMENT.md`, `docker-compose.yml`, `nginx/` and `vps_setup.sh` are all untracked/uncommitted, so the Docker/VPS deployment files in this tree have never been committed.
- Local dev: SQLite (`sqlite:///./deriv_bot_dev.db`) is the default database, Redis at localhost is still required for the risk manager.

---

## B. Deriv integration

### B1. Endpoint and App ID handling

- WebSocket URL: setting `deriv_ws_url`, env var `DERIV_WS_URL`, default `wss://ws.binaryws.com/websockets/v3` (`bot/config.py:27`). This is the legacy Deriv/Binary.com WebSocket API (v3), not the new `api.derivws.com` REST/OTP flow.
- Connection URI is built as `f"{ws_url}?app_id={app_id}"` (`bot/connection/client.py:46`).
- App ID: env var `APP_ID` into `Settings.app_id: int` (`bot/config.py:25`). It is required, with no default. It is logged at startup (`bot/main.py:181`, "Config: app_id=%d, ws_url=%s") and the full URI including app_id is logged at `client.py:47`. An app ID is a public identifier, not a secret. Value not shown here.
- `.env.production.template` sets an APP_ID that is not an obvious placeholder (4 characters). Value withheld.
- Token: env var `DERIV_API_TOKEN` into `Settings.deriv_api_token: str` (`config.py:26`), required, no default.

### B2. Auth method

Old token flow. After connecting, the bot sends `{"authorize": "<token>"}` as the first message (`client.py:60`) and reads the response. It then:

- raises `RuntimeError` on an `error` key;
- requires an `authorize` object with a `scopes` list;
- logs loginid, currency, balance, and scopes (`client.py` around lines 80-90);
- calls `SystemExit` if `"trade"` is not in scopes, so a read-only token cannot trade.

No OTP, no REST call, no OAuth. The token travels only in the WebSocket `authorize` message. Authorization is re-sent on every reconnect (`connect_forever` calls `connect_and_authorize` again).

### B3. Every outbound message type

| Message | Where | Notes |
|---|---|---|
| `authorize` | `client.py:60` | token string |
| `ticks` (subscribe) | `bot/data/subscriber.py:15` | symbol `1HZ10V` from `bot/main.py` |
| `balance` (subscribe, account=current) | `bot/main.py:69` | |
| `portfolio` | `bot/executor/reconciler.py:21` | open-contract check on connect |
| `proposal` (subscribe=1) | `bot/executor/proposal.py:38` | basis=stake, currency USD |
| `buy` | `proposal.py:67` | `{"buy": proposal_id, "price": ask_price}` |
| `forget` | `proposal.py:76` | after buy, and after a payout rejection |
| `proposal_open_contract` (subscribe) | `bot/executor/monitor.py:60` | by contract_id |

Not used anywhere: `sell`, `cancel`, `ping` (keepalive is the websockets library `ping_interval=30, ping_timeout=10`), `forget_all`, `contracts_for`, `active_symbols`, `authorize` with account switching.

Inbound handling: `req_id` responses are matched to futures in `executor.response_registry`; `proposal_open_contract` goes to the monitor; `balance` writes Redis keys; `tick` goes to the writer queue and the strategy orchestrator.

---

## C. Trading logic

### C1. Instruments and contract types

- Single instrument: `SYMBOL = "1HZ10V"` (Volatility 10 (1s) Index), hard-coded at `bot/executor/executor.py:25`. Commit 79eecb2 switched it from `R_100`. Note that `bot/backtest/__main__.py` still defaults to `--symbol R_100`, `subscribe_ticks` and `request_proposal` still default `R_100`, and the stored `backtest_results.json` is for `R_100`.
- Contract types available: DIGITEVEN, DIGITODD, DIGITOVER, DIGITUNDER, DIGITMATCH, DIGITDIFF (`bot/strategy/base.py`).
- Duration: `EXECUTOR_DURATION=5`, `EXECUTOR_DURATION_UNIT=t` (5 ticks), from `bot/config.py:32-33`.
- Selection: 9 strategy instances are built by `bot/strategy/factory.py` (3 kinds x window sizes 50, 200, 500): `even_odd_N`, `over_under_N`, `matches_differs_N`. `StrategyOrchestrator.process_tick` queries every enabled strategy each tick; the executor takes the first actionable signal in dict order and drops all others while a trade is in flight (`executor.py:139-152`). Dict order is even_odd_50, over_under_50, matches_differs_50, then 200, then 500, so shorter even_odd windows get priority.
- Which strategies are enabled: `load_enabled_strategies` (`bot/backtest/runner.py:278`). A strategy is enabled only if `backtest_results.json` has `symbol == "1HZ10V"`, `trade_count >= 200` (`MIN_LIVE_TRADE_SAMPLE`, line 275), and `expected_value_per_trade > 0`. The checked-in file is for `R_100`, so with the current tree every strategy is disabled and the bot would run in data-collection-only mode. This logic exists only in the working tree (uncommitted).
- At HEAD (committed), the live strategy is `AIStrategy`, which asks Claude (`claude-sonnet-4-6`) for DIGITEVEN, DIGITODD, or NO_TRADE every 30 ticks over the last 100 digits, with a confidence threshold of 0.60. See I1 for the uncommitted removal.

### C2. Signal rules, indicators, parameters (rule-based strategies, working tree)

All three use a rolling window of last digits (`bot/strategy/window.py`) and a chi-square test (`scipy.stats.chisquare`). No price indicators (no MA, RSI, etc.). Entry is gated on `freq.total >= window_size`.

- EvenOdd (`even_odd.py`): even count vs odd count. Requires dominant side >= 60% (`MIN_IMBALANCE_RATIO = 0.60`, line 25) and 2-category chi-square p < 0.05 (`chi_filter.py:17`, `P_VALUE_THRESHOLD`). Emits DIGITEVEN if evens dominate, else DIGITODD (it bets WITH the dominant side, i.e. assumes momentum / non-randomness).
- MatchesDiffers (`matches_differs.py`): 10-category chi-square p < 0.05. Takes the digit with the largest absolute deviation. Over-represented gives DIGITMATCH with that barrier; under-represented gives DIGITDIFF with that barrier.
- OverUnder (`over_under.py`): scans DIGITOVER barriers 0-8 and DIGITUNDER barriers 1-9, 2-cell chi-square with non-uniform expected frequency, p < 0.05 and observed above expected; picks the lowest p.
- `chi_filter.MIN_SAMPLE_SIZE = 50`.
- Pre-trade payout filter: net payout rate `(payout - ask_price)/ask_price` must be >= 0.80 (`executor.py:31`, `MIN_NET_PAYOUT_RATE`), else the proposal is forgotten and the trade skipped. This effectively blocks most Over/Under and Matches/Differs contracts.
- Backtest assumption: payout is 0.95x stake on a win (`bot/backtest/simulator.py`, `PAYOUT_MULTIPLIER = 0.95`). Live code accepts anything >= 0.80. The AI prompt at HEAD states "88% net". These three payout numbers disagree (see I).
- Statistical note: the digits of a Deriv synthetic index are designed to be independent and uniform. A chi-square p < 0.05 fires by chance about 1 time in 20 per window per tick. With strategies evaluated on every tick against overlapping windows, many signals will be false positives. That is why the working tree adds the 200-trade, same-symbol, positive-EV gate.

### C3. Stake sizing

`bot/risk/kelly.py:13-44`:

```python
def calculate_half_kelly_stake(
    win_rate: float,
    payout: float,
    balance: float,
    min_stake: float,
    max_stake_fraction: float = 0.10,
) -> float:
    if balance <= 0 or payout <= 0:
        return 0.0

    q = 1.0 - win_rate
    full_kelly = (win_rate * payout - q) / payout
    half_k = full_kelly / 2.0
    stake = half_k * balance

    if stake <= 0:
        return 0.0

    max_stake = balance * max_stake_fraction
    return max(min_stake, min(stake, max_stake))
```

Called from `RiskManager.pre_trade_check` (`bot/risk/manager.py:114`) with `balance` read from Redis key `risk:current_balance`, `min_stake=0.35`, `max_stake_fraction=0.02` (config defaults).

Does stake ever increase after a loss? No martingale or recovery logic exists. Stake is a fraction of current balance, so it shrinks after a loss and grows after a win. There is one exception in effect: the `max(min_stake, ...)` floor means that when 2% of balance is below 0.35 (balance under about 17.50), stake is forced up to 0.35, which exceeds the 2% cap. This is not loss-chasing, but it breaks the stated cap on small balances.

Defects in how the function is used (verified by reading the code, not by running):

1. The `payout` argument is documented as net multiplier (0.95 means 95%), but the executor passes the gross ratio `payout / ask_price` (about 1.95) at `executor.py` (the call to `pre_trade_check(win_rate, payout_ratio)`, around line 238). With a gross ratio, even `win_rate = 0.5` yields a positive Kelly stake: (0.5*1.95-0.5)/1.95 = 0.244, half = 0.122, capped to 2% of balance. The comment at `executor.py:184-192` claims the 0.5 default "makes half-Kelly compute a zero stake", which holds only for a net payout. So the fail-safe for unknown strategy names does not work as documented. The first (probe) call passes `payout=1.0`, which gives `full_kelly = win_rate - q`, so it is positive for any win_rate above 0.5.
2. The refined stake from the second `pre_trade_check` is only compared to the probe stake and logged (`executor.py:247-254`). The proposal and the buy use the probe stake. So the "refined" calculation has no effect on what is bought.
3. `win_rate` comes from `backtest_results.json` (`load_win_rates`), and in the checked-in file that is a round-number figure (0.52 for even_odd), see F.

### C4. Exit logic

- No stop-loss, take-profit, or `sell`. Each contract is a fixed 5-tick digit contract that expires on its own.
- The bot waits for settlement with `monitor.wait_for_settlement(contract_id)` and no timeout (`executor.py:271`; `timeout` defaults to None in `monitor.py`).
- One trade at a time: `_in_flight` flag, set at the start of `_execute_trade` and cleared in `finally`.
- Win/loss is derived from `profit >= 0` on the `is_expired == 1` message (`monitor.py:87-91`). A zero-profit result counts as a win.

---

## D. Safety

### D1. Risk controls

Defaults are in `bot/config.py:36-44`; they are overridable by env vars.

| Control | Default | Where | State |
|---|---|---|---|
| Trade scope required | n/a | `client.py` (SystemExit if "trade" not in scopes) | works |
| Daily loss limit (net P&L) | $50 | `bot/risk/counters.py` `check_daily_loss_limit`; called at `manager.py:92` and after settlement `manager.py:150` | works; hard-halts via `halt_event` |
| Daily trade count limit | 100 | `counters.py` `check_trade_count_limit`; `manager.py:99` | works |
| Consecutive-loss soft pause | 5 losses, 1800 s | `circuit_breakers.py:66-` `record_loss`; `manager.py:84` | defeated in practice, see D1a |
| Max drawdown hard halt | 20% | `circuit_breakers.py:86-` `check_drawdown`; `manager.py:104` | defeated in practice, see D1b |
| Max stake fraction | 2% of balance | `kelly.py:43-44` | floor at min_stake can exceed it |
| Min stake | 0.35 | `kelly.py:44` | |
| Minimum net payout | 80% | `executor.py:31`, check around `executor.py:219-234` | works |
| One position at a time | n/a | `executor.py` `_in_flight` | see D2 |
| Remote pause | Redis `bot:control:pause` | `bot/control/pause_watcher.py`; dashboard `PauseView` | works for remote pause |
| Auto-pause on InsufficientBalance | n/a | `executor.py:311-318` sets `bot:control:pause` | works |
| Data-collection-only gate | 200 trades, +EV, same symbol | `runner.py:278` | uncommitted |
| Cold-start reconciliation of daily counters from DB | n/a | `bot/risk/reconciler.py`, `main.py:224` | works with Postgres; SQLite just seeds zero |
| Portfolio reconciliation on connect | n/a | `executor.py` `reconcile`, `reconciler.py` | partial, see D2 |
| Graceful shutdown on SIGINT/SIGTERM | n/a | `bot/main.py:199-204` | works |

Not present ("none"):
- Per-trade or per-day stake cap in absolute dollars: none (only the 2% fraction).
- Max concurrent exposure beyond one trade: n/a (one at a time).
- Kill switch independent of Redis: none (pause relies on Redis; if Redis errors, the pause watcher logs and keeps its previous state).
- Time-of-day or volatility filters: none.
- Real-money confirmation or account-type check: none (see D3).
- Slippage guard on proposals other than Deriv's own price check on `buy`: none.
- Alerting (email, Telegram, etc.) on halt or error: none found.
- Rate limiting of API calls: none found.

**D1a. Consecutive-loss pause is cancelled by the pause watcher (verified by code reading).**
`CircuitBreakers._trigger_soft_pause` sets `_pause_event` (`circuit_breakers.py`, `_trigger_soft_pause`) but does not write the Redis `bot:control:pause` key. `pause_watcher_task` shares that same event and, every second, clears it whenever the Redis key is absent (`pause_watcher.py:58-60`: `elif not should_pause and pause_event.is_set(): pause_event.clear()`). So within about one second of a 5-loss streak the pause is lifted. The 30-minute pause then has no effect. UNVERIFIED at runtime; derived from code only, and I did not find a test that runs both together.

**D1b. Drawdown halt is neutralised by the balance sync (verified by code reading).**
Every Deriv `balance` message calls `_sync_balance`, which unconditionally sets `risk:peak_balance` to the current balance (`bot/main.py:83`). Drawdown is `(peak - current)/peak`, so peak and current are equal after each sync and the 20% threshold can never be exceeded, except in the short window between a settlement and the next balance message. Also, the hard halt sets `halt_event`, which ends the process; Docker `restart: unless-stopped` then restarts it, which clears the halt. The daily loss limit survives because it is re-seeded from Postgres, but the drawdown halt does not. Additionally `seed_balance(1000.0)` runs at every start (`main.py:239`), so for an account with a smaller real balance, peak is seeded at $1000 until the first balance message overwrites it (commit 6073300 says this caused a false 99% drawdown).

### D2. Timeout, disconnect, or error during buy

The buy path, `bot/executor/proposal.py:15-27` and 62-69:

```python
async def _await_response(
    ws, request: Dict[str, Any], registry: ResponseRegistry, req_id: int, timeout: float = 10.0
) -> Dict[str, Any]:
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    registry[req_id] = fut
    try:
        await ws.send(json.dumps(request))
        response = await asyncio.wait_for(fut, timeout=timeout)
    finally:
        registry.pop(req_id, None)
    check_error_field(response)
    return response

async def execute_buy(ws, proposal_id, ask_price, registry, req_id) -> int:
    req = {"buy": proposal_id, "price": ask_price, "req_id": req_id}
    response = await _await_response(ws, req, registry, req_id)
    return int(response["buy"]["contract_id"])
```

and the handler, `bot/executor/executor.py:311-325`:

```python
        except DerivAPIError as exc:
            if "InsufficientBalance" in str(exc):
                logger.error("Insufficient balance - pausing bot to prevent retry loop")
                try:
                    await self.redis.set("bot:control:pause", 1)
                except Exception:
                    pass
            else:
                logger.error("Deriv API error during trade: %s", exc)
        except Exception as exc:
            logger.exception("Unexpected error during trade: %s", exc)
        finally:
            self._in_flight = False
            if contract_id is not None:
                self._open_contracts.discard(contract_id)
```

Behaviour:
- Buy times out after 10 s: `asyncio.TimeoutError` falls to `except Exception`, logged with a traceback, `_in_flight` reset to False. No retry (good: no duplicate buy). But if the server did accept the buy and only the reply was lost, the contract is open, untracked, never settled in the DB or risk counters, and the bot can place another trade on the next signal. Nothing re-checks the portfolio until the next reconnect.
- Disconnect during buy: `ws.send` or the future raises, same path. On reconnect `on_ready` calls `executor.reconcile()` before resubscribing ticks, which fetches open contracts and resubscribes them. This is the only duplicate-buy guard.
- Disconnect while waiting for settlement: the old `_execute_trade` coroutine keeps awaiting a future with no timeout. If the contract is still open on reconnect, `monitor.subscribe` reuses the pending future and it resolves normally. If the contract expired during the outage, `reconcile_portfolio` filters on `is_expired == 0` (`reconciler.py`), the contract is not found, no `proposal_open_contract` result arrives, and the coroutine never completes. That trade is never logged to the DB or recorded in risk counters (the P&L is lost to the daily-loss limit). `reconcile` sets `_in_flight = bool(open_ids)`, so new trades can start while the stale coroutine leaks. UNVERIFIED by test; derived from code.
- API error on proposal or buy: `check_error_field` raises `DerivAPIError`, logged, no retry. `InsufficientBalance` sets the Redis pause flag.
- Failure to insert the trade row (`log_trade` raises) skips `record_result` because both are inside the same try block, so risk counters miss that trade.
- The buy price is the proposal ask price, so Deriv rejects the buy if the price moved.

### D3. Demo vs real

- There is no demo/real switch in code or config. The account type is determined only by which API token is supplied. The code logs `loginid` and balance at authorization (`client.py`), but never checks whether the loginid starts with `VRTC` (demo) or `CR` (real), and never refuses to trade on a real account.
- Default: no default; `DERIV_API_TOKEN` is required. `.env.example` and `DEPLOYMENT.md` instruct to "use a DEMO account token first". The app_id and ws_url are the same for both account types.
- Whether real mode was ever used: UNVERIFIED. Evidence that cannot settle it: commit a2e6c2c ("log account loginid, currency and balance on authorization"), commit f8ae258 ("sync real balance"), the "InsufficientBalance" handling (fa7f172, ddcb213), and a comment saying a seeded $1000 "causes 99% drawdown" (main.py:82) all imply the bot ran against an account whose balance was far below 1000, which is consistent with either a small real account or a demo account that was not topped up. There are no logs, no trade history and no recorded loginid in the repo. The user must confirm.

---

## E. Data and logs

### E1. Storage

Two backends, selected by `DATABASE_URL`: PostgreSQL via asyncpg (production), SQLite via aiosqlite (dev default). Redis for risk state and dashboard state.

Tables (`bot/data/schema.sql`; SQLite mirror in `schema_sqlite.sql`):

```
ticks(id BIGSERIAL PK, symbol VARCHAR(20), quote NUMERIC(18,8), last_digit SMALLINT CHECK 0..9,
      epoch BIGINT, received_at TIMESTAMPTZ DEFAULT NOW())
      indexes: (symbol, epoch DESC), (received_at DESC)
trades(id BIGSERIAL PK, symbol VARCHAR(20), strategy VARCHAR(50), contract_type VARCHAR(30),
       barrier SMALLINT NULL, stake NUMERIC(10,2), payout NUMERIC(10,2) NULL,
       profit NUMERIC(10,2), status VARCHAR(10) CHECK IN ('won','lost'),
       duration INTEGER, settled_at TIMESTAMPTZ DEFAULT NOW())
       index: (settled_at DESC)
```

The `trades` table has no contract_id, no loginid / account id, no entry or exit tick, no proposal id, and no field marking demo vs real. So trade rows cannot be traced back to Deriv's statement or tied to an account type. `settled_at` is the insert time, not Deriv's settlement time.

Redis keys used (from code): `risk:current_balance`, `risk:peak_balance`, `risk:consecutive_losses`, `risk:daily_loss:YYYYMMDD`, `risk:daily_trades:YYYYMMDD`, `bot:control:pause`, `bot:balance:current`, `bot:balance:session_start`, `bot:state:websocket`, `bot:state:running`, `bot:state:last_tick_epoch`. Redis persistence is `appendonly yes` in compose.

Backtest output: `backtest_results.json` in the repo root (copied into the bot image by `Dockerfile.bot`).

### E2. Logging

- Format: `%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s` to stdout, level from `LOG_LEVEL` (`bot/logging_config.py`). No log files, no rotation configured in the code; Docker's default json-file driver applies (no `logging:` limits in compose, UNVERIFIED impact).
- Logged: connection URI with app_id, loginid, currency, balance, token scopes, per-trade outcome lines, Kelly stake shifts, risk-limit events, errors with tracebacks, and, at DEBUG, the message type of every WebSocket message.
- Token in logs: I found no `logger` call that formats `api_token` or `settings`. The token is only passed to `ws.send` (`client.py:60`). Possible leak paths: (a) `bot/main.py:174` prints the pydantic `ValidationError` to stderr on bad config; pydantic v2 errors can include the offending input value, which could include a malformed token or app_id; (b) `ws_url` is logged and is not expected to hold credentials. UNVERIFIED at runtime.
- OTP URLs: not applicable, the bot does not use the OTP/REST flow, so none can appear.
- `DerivAPIError` messages include the full error object (`errors.py`: `full={err}`); Deriv error bodies do not echo the token as far as I know. UNVERIFIED.
- The balance and loginid are logged in clear text, which matters if logs are shared.

---

## F. Evidence

Files present:

| File | Content | Count | Date range |
|---|---|---|---|
| `backtest_results.json` (tracked, added in 56f35c6, file mtime 2026-04-08) | 1 backtest summary, symbol R_100, 2 strategies | 5,000 ticks listed | 2026-04-01 to 2026-04-07 (as stated in the file) |
| `deriv_bot_dev.db` (untracked SQLite) | table `ticks` | 1,160 rows, 1 symbol (R_100) | epoch 1775338384 to 1775582392 = 2026-04-04 21:33:04 UTC to 2026-04-07 17:19:52 UTC |
| `deriv_bot_dev.db` | table `trades` | 0 rows | none |

Trade history: **none exists in this repository or on this machine.** There are no CSV or log files, no Postgres dump, and the dev SQLite `trades` table is empty. Any Postgres on the VPS (`postgres_data` volume) was not accessible from here, so whether trades exist there is UNVERIFIED.

Computation for the requested statistics. The script below reads the `trades` table read-only and prints trade count, win rate, average win, average loss, total profit, and max drawdown of cumulative P&L (peak-to-trough, in account currency), plus the date range. Run it against any SQLite copy:

```python
# trade_stats.py  usage: python trade_stats.py path/to/db.sqlite
import sqlite3, sys
db = sys.argv[1]
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
rows = c.execute("SELECT profit, settled_at FROM trades ORDER BY settled_at, id").fetchall()
n = len(rows)
if n == 0:
    print("trades: 0 (no trade history)")
    sys.exit(0)
wins = [p for p, _ in rows if p >= 0]
losses = [p for p, _ in rows if p < 0]
cum = peak = dd = 0.0
for p, _ in rows:
    cum += p
    peak = max(peak, cum)
    dd = min(dd, cum - peak)
print(f"trades={n} win_rate={len(wins)/n:.4f} avg_win={sum(wins)/len(wins) if wins else 0:.4f} "
      f"avg_loss={sum(losses)/len(losses) if losses else 0:.4f} total_profit={cum:.4f} max_drawdown={dd:.4f} "
      f"range={rows[0][1]}..{rows[-1][1]}")
```

Actual output on this machine:

```
$ python trade_stats.py deriv_bot_dev.db
trades: 0 (no trade history)
```

For Postgres, the equivalent aggregate is:
`SELECT count(*), avg((status='won')::int), avg(profit) FILTER (WHERE profit>=0), avg(profit) FILTER (WHERE profit<0), sum(profit), min(settled_at), max(settled_at) FROM trades;`
(max drawdown needs a window function over cumulative profit, or the Python script above after exporting rows.)

Backtest file quality: `backtest_results.json` reports even_odd 1,200 trades at 52% win rate and over_under 980 trades at 51%, from 5,000 ticks with 200 warmup. Strategy names are `even_odd` and `over_under`, but the live and backtest factory names are `even_odd_50`, `even_odd_200`, etc. The field is `total_pnl`, but `bot/backtest/metrics.py` and `runner.py` emit `total_profit`. The values are round numbers, `generated_at` is exactly `12:00:00+00:00`, and 1,200 signals from 4,800 post-warmup ticks is a 25% signal rate that the current strategies would not plausibly produce at p<0.05. These facts suggest the file was written by hand or by an earlier or placeholder generator, not produced by the current `python -m bot.backtest`. UNVERIFIED how it was created. Treat it as having no evidentiary value. Also, a 52% win rate at a 0.95 payout gives expected value of about +0.014 per unit, with a sample of 1,200 trades the standard error on win rate is about 1.4 points, so 52% is not distinguishable from the 51.3% break-even rate.

No live, demo, or paper trade results of any kind are available for review.

---

## G. Tests

Test files (32): `bot/tests/`: test_backtest_bias_guard, test_backtest_ev_gate, test_backtest_loader, test_backtest_metrics, test_backtest_runner, test_backtest_simulator, test_chi_filter, test_client, test_config, test_even_odd, test_executor_logger, test_executor_monitor, test_executor_orchestration, test_executor_proposal, test_executor_reconciler, test_extractor, test_main, test_matches_differs, test_orchestrator, test_over_under, test_pause_watcher, test_reconnect, test_state, test_subscriber, test_window, test_writer (all `.py`) plus `conftest.py`. `tests/risk/`: test_circuit_breakers, test_counters, test_kelly, test_manager, test_reconciliation, test_redis_client plus `conftest.py`. Dashboard tests live in `dashboard/api/tests/`.

Config: `pyproject.toml` sets `asyncio_mode = "auto"` and `testpaths = ["bot/tests", "tests"]`. Tests use mock WebSockets (`wss://test`) and a stubbed `.env` written by `bot/tests/conftest.py`; they do not connect to Deriv.

Command (from repo root, using the project venv, no network access needed):

```
.venv/bin/python -m pytest -q -p no:cacheprovider
```

Real output (tail):

```
........................................................................ [ 24%]
........................................................................ [ 48%]
........................................................................ [ 72%]
........................................................................ [ 96%]
.........                                                                [100%]
=============================== warnings summary ===============================
bot/tests/test_backtest_simulator.py::test_digitover_loses_when_digit_lte_barrier[0]
  RuntimeWarning: coroutine '_async_load' was never awaited
bot/tests/test_client.py::test_state_transitions_during_connect
  RuntimeWarning: coroutine '_async_load' was never awaited
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
297 passed, 2 warnings in 224.51s (0:03:44)
```

Notes: this ran against the working tree (uncommitted changes included), not HEAD. The dashboard tests in `dashboard/api/tests/` are outside `testpaths` and were not run. The 224 s runtime is long for a mock-based suite; the cause was not investigated. The "coroutine '_async_load' was never awaited" warnings name a helper in `bot/backtest/runner.py`; the cause (likely a test mock) was not investigated, UNVERIFIED.

Coverage gaps noticed: no test combines `pause_watcher` with the consecutive-loss breaker (D1a); no test covers `_sync_balance` against `check_drawdown` (D1b); no test covers the buy-timeout-but-server-accepted case or the expired-during-disconnect case (D2).

---

## H. Secrets scan

Scope: git history on all refs (177 commits), tracked files in the working tree, and file names on disk. I did not open `.env` or `Ogutu Bot API TOKEN pat.txt`, and nothing below contains a secret value.

| Check | Result |
|---|---|
| Key-format patterns (Anthropic `sk-ant-`, AWS `AKIA`, GitHub `ghp_` / `github_pat_`, Slack `xox`, PEM private keys) in tracked files | no matches |
| Same patterns anywhere in history (`git log --all -G`) | no matches |
| Literal quoted values assigned to names like API_TOKEN, SECRET_KEY, PASSWORD, ADMIN_TOKEN, APP_ID in tracked non-markdown files | no matches |
| Real env file committed in history | never; the only env-named file in history is `.env.example`, introduced in 0a3c3ff, 8eb9fa4, fb3414d |
| Token-named files committed in history | none |

Files on disk that need attention:

| Path | Tracked | Ignored by .gitignore | Note |
|---|---|---|---|
| `.env` | no | yes (`.gitignore`) | holds live config; not opened |
| `Ogutu Bot API TOKEN pat.txt` | no | **no** | name suggests a personal access token; sitting unignored in the repo root, so `git add .` or `git add -A` would commit it. Contents not inspected, so what kind of token it is is UNVERIFIED. |
| `deriv_bot_dev.db` | no | **no** (`.gitignore` has no `*.db` rule) | contains tick data only (trades empty) |
| `.env.production.template` | no (untracked) | no | contains non-empty values for DERIV_API_TOKEN, POSTGRES_PASSWORD, DJANGO_SECRET_KEY, ADMIN_TOKEN, APP_ID. All but APP_ID look like placeholders (by length and by keywords such as "your"/"change"; values withheld). Check by eye before committing. |
| `.env.example` | yes | no | DERIV_API_TOKEN and APP_ID have non-empty values that look like placeholders (19 and 16 characters, containing placeholder keywords). Tracked in 3 commits. |
| `.claude/worktrees/*/.env.example` | no | yes (`.claude/` ignored) | 4 copies of the example file |

Hard-coded fallbacks in code (not secrets in git, but unsafe defaults):
- `dashboard/dashboard/settings.py:27`: `SECRET_KEY` falls back to a known insecure dev string if `DJANGO_SECRET_KEY` is unset.
- `dashboard/dashboard/settings.py:89`: database password defaults to an empty string.

Recommendation (not performed): if `Ogutu Bot API TOKEN pat.txt` holds a live token of any kind, move it outside the repo and add a rule to `.gitignore`. If it was ever pushed or pasted anywhere, rotate it. Nothing in git history shows that it was committed.

---

## I. Known problems

1. **Working tree differs from HEAD.** 10 files are modified or deleted and several are untracked. `bot/strategy/ai_strategy.py` is deleted (staged) and `anthropic` is dropped from the requirements. `bot/main.py` is rewritten to use the new `bot/strategy/factory.py` and `load_enabled_strategies`, and `docker-compose.yml`, `DEPLOYMENT.md`, `nginx/`, `scripts/` are all uncommitted. The committed HEAD is the AI-driven bot; the working tree is a rule-based, data-collection-first bot. What actually ran in April is HEAD or earlier; UNVERIFIED which version, if any, ran against a live or demo account for how long.
2. **Consecutive-loss pause is nullified by `pause_watcher`** (D1a).
3. **Drawdown halt is nullified by `_sync_balance` overwriting the peak**, and a hard halt is undone by Docker restart (D1b).
4. **Kelly gets a gross payout ratio where it expects a net one**, so the "unknown strategy has zero stake" fail-safe does not hold (C3, item 1).
5. **The refined stake is never used**; the buy uses the probe stake (C3, item 2).
6. **`min_stake` floor can exceed the 2% stake cap** for balances under about $17.50 (C3).
7. **Settlement wait has no timeout**, so a contract that expires while disconnected leaves a leaked coroutine and an unrecorded trade (D2). Buy timeout can leave an untracked open contract (D2).
8. **Trade rows lack contract_id and account/loginid** and use insert time as `settled_at` (E1).
9. **Dashboard admin auth reads env var `ADMIN_TOKEN`** (`dashboard/api/auth.py:33`) while its docstrings and comments, and `PauseView`, name `DASHBOARD_ADMIN_TOKEN`. If only the documented name is set, all pause/resume calls return 503. UNVERIFIED which name `.env.production.template` and `docker-compose.yml` use (compose passes through `.env`).
10. **Dashboard `SECRET_KEY` has an insecure default** and the DB password defaults to empty (H).
11. **Stale symbol defaults.** `R_100` remains the default in `backtest/__main__.py`, `subscribe_ticks`, `request_proposal`, `.planning/PROJECT.md` and the checked-in `backtest_results.json`, while live trading targets `1HZ10V`. Running `python -m bot.backtest` without `--symbol` would produce results the live gate rejects.
12. **Payout assumptions disagree**: backtest 0.95, live minimum 0.80, AI prompt "88% net". The break-even win rate differs for each (51.3%, 55.6%, 53.2%).
13. **`backtest_results.json` appears synthetic or stale** (F). Window sizes 50/200/500 in the factory vs commit history setting windows to 20 and 50; bias_guard changes are uncommitted.
14. **Zero-profit settlement counts as a win** (`monitor.py:90`, `profit >= 0`); harmless for digits in practice but wrong in principle.
15. **`APScheduler` is started but no jobs were found**, so it is dead weight. UNVERIFIED.
16. **`.gitignore` gaps**: no rule for `*.db`, `Ogutu Bot API TOKEN pat.txt`, `*.txt` tokens, `.env.*` variants (only `.env` is listed). A `.env.production` file would not be ignored.
17. **Deployment files never committed.** The whole Docker/VPS setup exists only in the working tree. If the machine is lost, so are they.
18. **Dockerfile.bot copies `backtest_results.json` at build time**, so the live enabled-strategy set is frozen in the image; re-running the backtest in the container writes a new file that is lost on container recreation unless a volume is added (no volume is defined). UNVERIFIED impact.
19. **Edge**: nothing in the repo shows a statistical edge. Deriv synthetic index digits are generated to be uniformly random, and the strategies are chi-square pattern detectors on a random sequence. The code's own newest design (EV gate with a 200-trade minimum, same-symbol rule) concedes that no edge has been demonstrated.
20. **Commit hygiene**: AI-related text still exists in `docker-compose.yml` comment ("rule-based only, no AI/LLM") while HEAD ran an LLM strategy; fine in the working tree, misleading against HEAD.

Things I could not verify (all UNVERIFIED):
- Whether any trade was ever placed, on demo or real. No trade history exists locally.
- Whether the VPS deployment ever ran, and its Postgres/Redis contents or logs.
- Whether `.env` points at a demo or real token (deliberately not opened).
- What `Ogutu Bot API TOKEN pat.txt` contains.
- Whether `build_scheduler()` schedules anything.
- Runtime behaviour of D1a, D1b and the D2 edge cases; findings are from code reading only.
- Whether Deriv returns `balance_after` on `proposal_open_contract` messages (the code falls back to the Redis balance if not; Deriv's documented fields may not include it).
