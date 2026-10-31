# Deriv Autonomous Trading Bot: Engineering Handbook

Audience: coding agents (Claude Code) and the owner.
Status: Phase 0 and 1 research complete. Nothing is built yet.
Date of research: 30 Oct 2026. Deriv's API is changing quickly, so re-verify before building.

## 0. How to use this document

- Sections 1 to 4 are the "why". Read them before writing any code.
- Sections 5 to 11 are the specification.
- Section 12 is the step-by-step setup.
- Section 13 is the phase roadmap with gates. Follow it in order.
- Anything marked `UNVERIFIED` must be checked against the official docs or by running code against the DEMO endpoint before you rely on it.

---

## 1. Mission and non-negotiables

Build an automated system for the owner's own Deriv account that monitors markets, generates signals, validates risk, executes trades, reconciles state, and reports via Telegram, running 24/7 on a Linux VPS.

Non-negotiables (also in CLAUDE.md):

- Demo by default. Real money disabled.
- Tokens never exposed. The OTP WebSocket URL is a secret.
- Every trade is risk-validated.
- Uncertain orders are never blindly retried.
- No martingale or stake doubling.
- Every critical action is logged to an append-only audit trail.
- Every component has automated tests.
- No fabricated results. No claims of profitability without evidence.
- The bot never moves money. Funding is manual via Deriv's cashier.

---

## 2. Verified facts about the Deriv API (researched 30 Oct 2026)

Source: https://developers.deriv.com (docs), including Workflows, Limits, API overview, Product updates.

### 2.1 Architecture

- Two components: REST (account setup, OTP generation, health) and WebSocket (trading, market data, subscriptions).
- REST base: `https://api.derivws.com`
- WebSocket endpoints:
  - Public (no auth): `wss://api.derivws.com/trading/v1/options/ws/public`
  - Demo (auth via OTP): `wss://api.derivws.com/trading/v1/options/ws/demo?otp=...`
  - Real (auth via OTP): `wss://api.derivws.com/trading/v1/options/ws/real?otp=...`
- The old endpoint `wss://ws.binaryws.com/websockets/v3` still appears in some docs pages. Treat it as legacy. `UNVERIFIED`: its shutdown date. Do not build on it.
- Old (legacy) App IDs do not work with the new APIs. A new App ID must be registered at developers.deriv.com.
- If developers.deriv.com redirects a login to a trading account, it is a legacy account. Sign up with a different email.
- Schemas for all messages are published as `schemas.zip` at https://github.com/deriv-com/deriv-api-schemas/releases/latest/download/schemas.zip. Use them to generate typed models and to validate every message in tests.

### 2.2 Authentication

- Register an application of type **PAT** (right for a server-side CLI/bot) in the developer dashboard. This yields an App ID.
- Create a Personal Access Token in the dashboard's API tokens section and choose scopes. The token is shown once. It cannot be viewed again.
- REST calls need headers: `Authorization: Bearer <PAT>`, `Deriv-App-ID: <app id>`, `Content-Type: application/json`.
- Get an authenticated WebSocket URL: `POST /trading/v1/options/accounts/{accountId}/otp`. The JSON response has `data.url`. The URL points at demo or real depending on the account ID, and has the OTP embedded.
- Users get a default demo account on signup. A demo account can also be created via `POST /trading/v1/options/accounts` with `{currency, group, account_type: "demo"}`.
- Errors: 401 means missing/invalid token, legacy App ID, or token/app type mismatch. 403 means missing scope.
- `UNVERIFIED`: whether an OTP is single-use and its lifetime. Assume single-use and short-lived: fetch a fresh OTP on every (re)connect.

### 2.3 WebSocket message cheat sheet (from the docs' examples)

Requests are JSON objects. Use a unique `req_id` per request and match responses on it. Responses carry `msg_type`.

| Purpose | Request shape | Notes |
|---|---|---|
| Balance stream | `{"balance":1,"subscribe":1}` | `msg_type: balance` |
| Tick stream | `{"ticks":"1HZ100V","subscribe":1}` | `msg_type: tick`, field `tick.quote` |
| Tick history | `{"ticks_history":"1HZ100V","count":100,"end":"latest","style":"ticks"}` | `msg_type: history` |
| Symbols | `{"active_symbols":"brief"}` | Each symbol supports only certain trade types |
| Contracts per symbol | `contracts_for` | Source of truth for allowed contract types, durations |
| Proposal | `{"proposal":1,"amount":10,"basis":"stake","contract_type":"...","currency":"USD","duration_unit":"s","underlying_symbol":"...","subscribe":1}` | Docs example uses `MULTDOWN` with `multiplier`. Other types and required fields `UNVERIFIED`: read `contracts_for` and the proposal schema |
| Buy | `{"buy":"<proposal_id>","price":<max price>}` | Docs also say parameters can be passed directly. Prefer proposal id with price cap |
| Track contract | `{"proposal_open_contract":1,"contract_id":<id>,"subscribe":1}` | |
| Sell early | `sell` | Only where the contract supports it |
| Portfolio | `portfolio` | Open positions, used in reconciliation |
| Other | `profit_table`, `statement`, `transaction`, `forget`, `forget_all`, `ping`, `time`, `trading_times` | |

The docs list a trading operation set: proposal, buy, sell, proposal_open_contract, contract_update, contract_update_history, cancel, bulk_purchase.

### 2.4 Rate limits (WebSocket budgets are shared across the calls in a row)

| Calls counted together | Per minute | Per hour |
|---|---|---|
| `proposal`, `proposal_open_contract`, `buy`, `sell` | 360 | 14,400 |
| `balance`, `statement` | 100 | 2,000 |
| `portfolio`, `profit_table` | 30 | 1,500 |
| All other calls | 220 | 14,400 |

- `ping` is outside budgets: 10 per second per connection.
- REST: 300 per minute and 1,000 per 10 minutes per IP, and 80 per minute per logged-in user.
- Deriv reserves the right to change limits. Make limits configuration, not constants.
- Guidance from Deriv: subscribe instead of polling, reuse one connection, pace bursts, back off after rejection.

### 2.5 Recent changes that matter

- 25 Jun 2026: server-side Automation API (`auto_start`, `auto_get`, `auto_pause`, `auto_resume`, `auto_stop`, `auto_list`, `auto_list_strategies`). Pause halts new purchases and holds the run. Stop ends the run, and open contracts continue to expiry.
- 10 Aug 2026: new synthetic indices (Volatility 15/30/90 (1s), more Crash/Boom ranges, Range Break 100 and 200).
- 20 Aug 2026: rate limits documented on one page.
- Decision: we do NOT use the Automation API for live decisions. Our decisions must be deterministic and auditable in our own code. We may inspect it for ideas.

### 2.6 Open-source foundation decision (summary of Phase 2)

| Project | Licence | Fit for Deriv | Decision |
|---|---|---|---|
| NautilusTrader | LGPL-3.0 | No Deriv adapter found. Its model is orders and order books, Deriv Options are proposal then buy with fixed expiry. Adapter cost exceeds benefit. | Do not adopt. Idea source only |
| Freqtrade | GPL-3.0 (UNVERIFIED, from training) | Crypto exchanges via CCXT | Reference for metrics and backtest reporting only. Do not copy code |
| QuantConnect LEAN | Apache-2.0 (UNVERIFIED) | C#, brokerage-oriented | Do not adopt |
| Hummingbot | Apache-2.0 (UNVERIFIED) | Crypto market making | Do not adopt |

Decision: build from scratch as a thin asyncio Python service. Reuse libraries, not frameworks.

---

## 3. Items agents must verify in Phase 0 (do these before Phase 3 code)

Write findings into `docs/api-notes.md` with the date, the request you ran, and the response (with secrets redacted).

1. Which contract types, durations, and min/max stakes `contracts_for` returns on the DEMO account for the chosen symbols.
2. The payout ratio actually offered by `proposal` for each candidate contract. This is the single most important number for break-even maths.
3. Whether `buy` supports any idempotency token or client reference. If not, assume it does not.
4. How `proposal_open_contract` reports settlement and what happens to subscriptions after a reconnect.
5. `ticks_history` maximum `count` and pagination behaviour for backfill.
6. OTP single-use and lifetime behaviour.
7. Which fields `portfolio` and `profit_table` return, to design reconciliation.
8. Whether the demo balance can be reset (docs list "Reset demo balance" under Options Setup).
9. Whether the legacy `ws.binaryws.com` endpoint is deprecated, and when.
10. Terms of use and any restrictions on automated trading in the owner's jurisdiction and account type. This is a legal/compliance check for the owner, not for code.

---

## 4. The honest premise: payouts, edge, and the kill criterion

### 4.1 Payout maths

Deriv Options with a fixed payout are priced so the house keeps an edge. Let `r = (payout - stake) / stake` be profit per unit stake on a win.

- Break-even win probability: `p_be = 1 / (1 + r)`
- Expectancy per unit stake: `E = p * r - (1 - p)`
- Example only, not a Deriv figure: if `r = 0.85`, then `p_be = 1/1.85 = 54.05%`.

The real `r` must come from `proposal` responses (section 3, item 2). Never hard-code it.

### 4.2 Synthetic indices

Deriv's synthetic indices are simulated markets. Our working assumption: their price paths are random by design, so technical indicators have no genuine predictive edge on them. `UNVERIFIED` against Deriv's own published description of how synthetics are generated. Agents must read that description and record it.

Consequence: any "edge" found on synthetics is much more likely overfitting. The harness must prove it can say NO (section 9.5, control experiment).

### 4.3 The kill criterion (pre-registered, binding)

Write this into `docs/kill-criterion.md` and commit it BEFORE any strategy is tested, so it cannot be edited after seeing results.

A strategy passes the edge gate only if ALL hold on a locked, never-touched out-of-sample set and then on forward demo trading:

1. Number of independent trades `n >= n_min`, where `n_min` comes from a power calculation committed in advance.
2. The one-sided 95% lower confidence bound (Wilson) of the win rate exceeds `p_be` computed from the payouts actually offered, after Holm or Bonferroni correction for the number of strategy variants tried.
3. Expectancy per unit stake is positive net of the real payout.
4. Maximum drawdown is within the risk spec.
5. Results hold across at least two distinct market regimes or time blocks.

Illustrative power calculation (not a result): to detect a win rate 2 percentage points above a break-even of about 54%, with one-sided alpha 0.05 and power 0.80, `n ≈ (1.645 + 0.842)^2 * p(1-p) / 0.02^2 ≈ 3,800` independent trades. Small edges need large samples. Agents must recompute with the real `p_be`.

If no strategy passes, the project STOPS before live readiness. Record the negative result honestly. A documented "no edge" is a successful outcome of the research.

---

## 5. Decisions and open decisions

### 5.1 Stack (decided)

Python 3.12, asyncio, `websockets` or `aiohttp` for the WebSocket client, `httpx` for REST, FastAPI (status and kill switch only, bound to localhost), PostgreSQL 16, Redis 7, Docker Compose, pandas, NumPy, `pandas-ta` or `ta` for indicators (pin versions, check maintenance), SQLAlchemy 2 with Alembic, pydantic v2 (models generated from Deriv schemas where practical), `python-telegram-bot`, pytest, hypothesis, ruff, mypy, structlog.

Rust: not needed. Revisit only if profiling shows a hot path.

### 5.2 Reuse, modify, build

| Area | Approach |
|---|---|
| Deriv adapter (REST + WS client, reconnect, rate limiter) | Build |
| Tick collector and backfill | Build |
| Backtester (contract-level, payout-aware) | Build. Use pandas/NumPy |
| Indicators | Reuse library, wrap behind our own interface |
| Performance metrics | Build, using Freqtrade as a definitions reference |
| Risk engine, order state machine, reconciliation | Build |
| Telegram | Reuse `python-telegram-bot` |
| Process supervision | Reuse Docker restart policies and healthchecks |

### 5.3 OPEN DECISIONS (ask the owner, do not guess)

1. **Instruments.** Synthetic indices, or non-synthetic markets if the demo account offers them, or both with synthetics as the control. Default plan until decided: collect `1HZ100V` as a control plus whatever non-synthetic symbols `active_symbols` returns for the account.
2. **Contract family.** Fixed-payout Rise/Fall style, multipliers, or others. Decided after Phase 0 item 1 and 2.
3. **Funding level.** Owner decides a maximum amount to lose entirely, in USD. Default: none (demo only).
4. **Telegram operators.** Chat IDs allowed to command the bot.

---

## 6. System architecture

```mermaid
flowchart LR
  subgraph Deriv
    REST[REST api.derivws.com]
    WSP[WS public]
    WSD[WS demo / real]
  end

  subgraph Bot[Bot on Oracle VM, Docker Compose]
    COL[Collector]
    SIG[Signal engine]
    RISK[Risk engine]
    EXE[Executor]
    REC[Reconciler]
    NOT[Notifier]
    API[Admin API, localhost only]
  end

  PG[(PostgreSQL)]
  RD[(Redis)]
  TG[Telegram]

  REST --> EXE
  WSP --> COL
  WSD <--> EXE
  COL --> PG
  COL --> RD
  RD --> SIG
  SIG --> RISK
  RISK -->|approved intent| EXE
  EXE --> PG
  REC <--> WSD
  REC --> PG
  RISK <--> RD
  NOT --> TG
  API --> RD
  EXE --> NOT
  REC --> NOT
  RISK --> NOT
```

### 6.1 Component responsibilities

- **Collector**: subscribes to ticks on the public endpoint, writes to Postgres, publishes the latest tick and heartbeat to Redis. Detects gaps and backfills with `ticks_history`.
- **Signal engine**: pure functions from market data to a signal. Deterministic. No network calls. Versioned strategies (`strategy_id`, `strategy_version`).
- **Risk engine**: the only component allowed to turn a signal into an approved intent. Reads limits from versioned config, state from Postgres and Redis. Writes every decision, approve or reject, with the rule results.
- **Executor**: owns the authenticated connection, the order state machine, and the rate limiter. The only component that can call `buy` or `sell`.
- **Reconciler**: periodically and after every reconnect compares local state with `portfolio`, `profit_table` and `statement`. Flags discrepancies, never silently "fixes" them.
- **Notifier**: Telegram alerts, daily report, heartbeat.
- **Admin API**: read-only status plus kill switch endpoints, authenticated, bound to 127.0.0.1.

### 6.2 Repository layout

```
.
├── CLAUDE.md
├── docs/
│   ├── HANDBOOK.md
│   ├── api-notes.md
│   ├── kill-criterion.md
│   └── runbook.md
├── .claude/skills/            # created in Phase 2, see section 15
├── src/derivbot/
│   ├── config/                # typed settings, versioned risk config
│   ├── deriv/                 # rest.py, ws.py, ratelimit.py, models.py
│   ├── collector/
│   ├── signals/
│   ├── backtest/
│   ├── risk/
│   ├── execution/             # state machine, executor
│   ├── reconcile/
│   ├── notify/
│   ├── api/
│   ├── db/                    # models, alembic migrations
│   └── common/                # logging, redaction, clock
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fakes/                 # fake Deriv server with failure injection
├── docker/
├── docker-compose.yml
├── Makefile
└── .env.example
```

---

## 7. Order lifecycle and idempotency

Deriv's `buy` has no documented idempotency key (`UNVERIFIED`, section 3 item 3). So WE enforce safety.

```mermaid
stateDiagram-v2
  [*] --> INTENT_CREATED
  INTENT_CREATED --> RISK_REJECTED: risk fails
  INTENT_CREATED --> RISK_APPROVED: risk passes
  RISK_APPROVED --> PROPOSAL_REQUESTED
  PROPOSAL_REQUESTED --> PROPOSAL_REJECTED: price moved or invalid
  PROPOSAL_REQUESTED --> BUY_SENT: price within cap
  BUY_SENT --> OPEN: buy confirmed with contract_id
  BUY_SENT --> BUY_UNKNOWN: timeout or disconnect
  BUY_UNKNOWN --> OPEN: found in portfolio
  BUY_UNKNOWN --> NOT_PLACED: not found after reconcile window
  BUY_UNKNOWN --> MANUAL_REVIEW: ambiguous
  OPEN --> SETTLED: expiry
  OPEN --> SOLD: early sell
  SETTLED --> [*]
  SOLD --> [*]
  RISK_REJECTED --> [*]
  PROPOSAL_REJECTED --> [*]
  NOT_PLACED --> [*]
  MANUAL_REVIEW --> [*]
```

```mermaid
sequenceDiagram
  participant S as Signal
  participant R as Risk engine
  participant E as Executor
  participant D as Deriv WS
  participant DB as Postgres (audit)
  S->>R: signal
  R->>DB: risk_decision (approve/reject)
  R->>E: approved intent (idempotency_key)
  E->>DB: intent + state INTENT_CREATED
  E->>D: proposal
  D-->>E: proposal id + price
  E->>DB: PROPOSAL_REQUESTED
  E->>D: buy(proposal id, price cap)
  E->>DB: BUY_SENT
  alt confirmed
    D-->>E: buy response with contract_id
    E->>DB: OPEN
  else timeout or disconnect
    E->>DB: BUY_UNKNOWN
    E->>D: portfolio (after reconnect)
    E->>DB: OPEN or NOT_PLACED
  end
```

Rules:

1. One in-flight `buy` per intent. The intent's `idempotency_key` has a unique constraint in Postgres and a Redis lock.
2. `BUY_UNKNOWN` never retries. It reconciles: query `portfolio`, then `statement` or `profit_table` if needed, matching on symbol, contract type, stake, and timestamp window. Found means `OPEN`. Not found after the window means `NOT_PLACED`. Ambiguous means `MANUAL_REVIEW` and an alert, and new trading pauses.
3. A fresh intent after `NOT_PLACED` needs a fresh risk decision. It is never an automatic replay.
4. Always send a `price` cap on `buy` so slippage cannot exceed it.
5. The state machine is a pure module with exhaustive tests, including property-based tests that no illegal transition is possible.

---

## 8. Database architecture (PostgreSQL)

Principles: append-only audit, strict constraints, timestamps in UTC `timestamptz`, money as `numeric`, every trading row stamped with `mode` (`demo`/`real`) and `account_id`.

```mermaid
erDiagram
  signals ||--o{ order_intents : triggers
  order_intents ||--|| risk_decisions : evaluated_by
  order_intents ||--o| orders : becomes
  orders ||--o{ order_events : logs
  config_versions ||--o{ risk_decisions : applied
  backtest_runs ||--o{ backtest_trades : contains
```

```sql
-- Market data
CREATE TABLE ticks (
  symbol      text        NOT NULL,
  epoch       bigint      NOT NULL,
  quote       numeric     NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (symbol, epoch)
);

CREATE TABLE candles (
  symbol text NOT NULL, granularity_s int NOT NULL, open_epoch bigint NOT NULL,
  o numeric NOT NULL, h numeric NOT NULL, l numeric NOT NULL, c numeric NOT NULL,
  PRIMARY KEY (symbol, granularity_s, open_epoch)
);

-- Config snapshots so every decision is reproducible
CREATE TABLE config_versions (
  id bigserial PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now(),
  kind text NOT NULL,                 -- 'risk' | 'strategy' | 'system'
  content jsonb NOT NULL,
  content_hash text NOT NULL UNIQUE
);

-- Decisions
CREATE TABLE signals (
  id uuid PRIMARY KEY,
  created_at timestamptz NOT NULL DEFAULT now(),
  strategy_id text NOT NULL, strategy_version text NOT NULL,
  symbol text NOT NULL, direction text NOT NULL,
  features jsonb NOT NULL,            -- inputs used, for audit and replay
  data_cutoff_epoch bigint NOT NULL   -- proves no look-ahead
);

CREATE TABLE order_intents (
  id uuid PRIMARY KEY,
  idempotency_key text NOT NULL UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now(),
  signal_id uuid REFERENCES signals(id),
  mode text NOT NULL CHECK (mode IN ('demo','real')),
  account_id text NOT NULL,
  symbol text NOT NULL, contract_type text NOT NULL,
  stake numeric NOT NULL CHECK (stake > 0),
  duration int, duration_unit text,
  state text NOT NULL
);

CREATE TABLE risk_decisions (
  id bigserial PRIMARY KEY,
  intent_id uuid NOT NULL UNIQUE REFERENCES order_intents(id),
  decided_at timestamptz NOT NULL DEFAULT now(),
  approved boolean NOT NULL,
  rule_results jsonb NOT NULL,        -- every rule, pass/fail, observed value, limit
  state_snapshot jsonb NOT NULL,      -- open positions, daily pnl, loss streak, balance
  config_version_id bigint NOT NULL REFERENCES config_versions(id)
);

-- Execution
CREATE TABLE orders (
  id uuid PRIMARY KEY,
  intent_id uuid NOT NULL UNIQUE REFERENCES order_intents(id),
  deriv_contract_id bigint UNIQUE,
  deriv_transaction_id bigint,
  buy_price numeric, payout numeric,
  state text NOT NULL,
  opened_at timestamptz, settled_at timestamptz,
  profit numeric,
  mode text NOT NULL, account_id text NOT NULL
);

-- Append-only audit log (no UPDATE or DELETE allowed)
CREATE TABLE audit_events (
  id bigserial PRIMARY KEY,
  ts timestamptz NOT NULL DEFAULT now(),
  actor text NOT NULL,                -- component name
  event_type text NOT NULL,           -- INTENT, RISK, BUY_SENT, BUY_CONFIRMED, SETTLED, KILL_SWITCH, RECONNECT, RECONCILE, ...
  ref_id text,                        -- intent/order id
  req_id bigint,
  payload jsonb NOT NULL              -- secrets redacted before insert
);

CREATE FUNCTION audit_events_immutable() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'audit_events is append-only'; END; $$ LANGUAGE plpgsql;
CREATE TRIGGER audit_events_no_update BEFORE UPDATE OR DELETE ON audit_events
  FOR EACH ROW EXECUTE FUNCTION audit_events_immutable();

-- Operations
CREATE TABLE balance_snapshots (
  id bigserial PRIMARY KEY, ts timestamptz NOT NULL DEFAULT now(),
  mode text NOT NULL, account_id text NOT NULL, balance numeric NOT NULL, currency text NOT NULL
);

CREATE TABLE reconciliation_runs (
  id bigserial PRIMARY KEY, ts timestamptz NOT NULL DEFAULT now(),
  trigger text NOT NULL,              -- 'periodic' | 'reconnect' | 'startup' | 'manual'
  local_open int, remote_open int,
  status text NOT NULL,               -- 'clean' | 'discrepancy'
  discrepancies jsonb
);

CREATE TABLE kill_switch_events (
  id bigserial PRIMARY KEY, ts timestamptz NOT NULL DEFAULT now(),
  action text NOT NULL CHECK (action IN ('engage','release')),
  source text NOT NULL, reason text
);

-- Research
CREATE TABLE backtest_runs (
  id uuid PRIMARY KEY, created_at timestamptz NOT NULL DEFAULT now(),
  strategy_id text NOT NULL, strategy_version text NOT NULL,
  params jsonb NOT NULL, data_range jsonb NOT NULL,
  split text NOT NULL,                -- 'train' | 'validation' | 'holdout' | 'forward'
  payout_model jsonb NOT NULL,
  git_commit text NOT NULL,
  metrics jsonb NOT NULL
);

CREATE TABLE backtest_trades (
  run_id uuid NOT NULL REFERENCES backtest_runs(id),
  seq int NOT NULL, entry_epoch bigint NOT NULL, exit_epoch bigint NOT NULL,
  direction text NOT NULL, stake numeric NOT NULL, win boolean NOT NULL, profit numeric NOT NULL,
  PRIMARY KEY (run_id, seq)
);
```

Redis keys (ephemeral, never the source of truth for money):

- `kill_switch` (string `0`/`1`), mirrored to Postgres on change
- `lock:intent:{idempotency_key}`
- `ratelimit:{bucket}` counters
- `heartbeat:{component}` with TTL
- `tick:last:{symbol}`

---

## 9. Strategy research plan and backtest methodology

### 9.1 Strategies to test (each is a hypothesis, registered before testing)

1. EMA trend following.
2. RSI plus Bollinger Band mean reversion.
3. Volatility breakout.
4. Market regime detection (for example, volatility and trend-strength filters).
5. Adaptive strategy selection (only after 1 to 4 are individually understood).

Each hypothesis file in `docs/hypotheses/` states: the rule, the contract it maps to, the parameter grid (finite, small), and the expected direction of effect, committed before any run.

### 9.2 Backtester requirements

- Contract-level, not price-level: simulate buying a specific contract at signal time, settling at expiry, using the REAL payout ratio from collected `proposal` quotes or a conservative fixed `r` stored in `payout_model`.
- No look-ahead: a signal at time `t` may use only ticks with `epoch <= t`. Entry price is the first tick after `t + latency`, where latency is measured from demo executions, not guessed. Tests must include a deliberate look-ahead strategy that the harness must catch.
- Overlapping trades: if contracts overlap in time, adjust the effective sample size or forbid overlap. Document the choice.
- Costs: payout haircut is the cost. Include it. No separate "free" gross results.

### 9.3 Data splits

```mermaid
flowchart LR
  A[All collected data] --> B[Train: explore, tune]
  A --> C[Validation: select]
  A --> D[Holdout: LOCKED, touched once per final candidate]
  D --> E[Forward demo trading]
```

- Time-ordered splits only. No random shuffling of time series.
- The holdout is locked by hash and a CI check. Opening it is logged.
- Walk-forward evaluation for parameterised strategies.

### 9.4 Metrics

Win rate with Wilson interval, average win and loss, risk/reward, expectancy per unit stake, profit factor, maximum drawdown and duration, longest loss streak, trades per day, performance by regime and by time block, sensitivity to payout ratio (what happens if `r` is 0.05 lower), and number of strategy variants tried (for multiple-testing correction).

### 9.5 Control experiment (mandatory)

Run the entire pipeline on (a) shuffled tick returns and (b) simulated random walks. The harness must report NO edge at the expected false-positive rate. If it finds "edge" in random data, the harness is broken: fix it before trusting any result.

### 9.6 Forward testing

Demo execution of the best candidates, logged in the same schema, compared with backtest expectations. Large unexplained gaps mean the backtest is wrong.

---

## 10. Risk management specification

All values are configuration, versioned in `config_versions`. The numbers below are placeholders for the owner to set, not recommendations.

| Parameter | Meaning | Placeholder |
|---|---|---|
| `risk_per_trade_pct` | Stake as a percentage of current balance | `SET_BY_OWNER` |
| `max_stake_abs` | Hard stake ceiling | `SET_BY_OWNER` |
| `min_stake_abs` | Respect Deriv minimums from `contracts_for` | from API |
| `max_daily_loss_abs` | Stop opening new positions at this realised daily loss | `SET_BY_OWNER` |
| `max_consecutive_losses` | Stop opening new positions after this streak | `SET_BY_OWNER` |
| `max_open_positions` | Concurrent contracts cap | `SET_BY_OWNER` |
| `max_trades_per_hour` | Throttle | `SET_BY_OWNER` |
| `max_proposal_price_slippage` | Reject if buy price exceeds proposal by more than this | `SET_BY_OWNER` |
| `stale_data_max_age_s` | Reject if the latest tick is older than this | `SET_BY_OWNER` |
| `max_daily_loss_pct_of_start_balance` | Alternative to absolute | `SET_BY_OWNER` |

### 10.1 Pre-trade checks (all must pass, all recorded in `risk_decisions.rule_results`)

1. Kill switch not engaged.
2. Mode and account consistent: configured mode matches the connected endpoint (demo vs real) and the account ID type. Real requires the extra unlock in section 11.4.
3. Connection healthy and last successful reconciliation is recent.
4. Market data fresh.
5. Within `max_open_positions`, daily loss, loss streak, and trade rate limits.
6. Stake computed by the fixed-fraction rule and within min and max.
7. Duplicate protection: no in-flight or recent identical intent (idempotency key unique).
8. Proposal price and payout still acceptable.
9. Strategy is enabled and its version is the approved one.

### 10.2 Stake sizing

- Fixed fraction of current balance, capped. Stake must be the same or smaller after a loss. Never larger. A property test enforces `stake(after_loss) <= stake(before)` for every sequence.
- No martingale, no anti-martingale scaling on streaks, no averaging into losers.

### 10.3 When a limit trips

1. Risk engine rejects all new intents.
2. Existing contracts continue to be monitored, settled, and reconciled. Do not force-close unless the owner issues a command and the contract supports `sell`.
3. Telegram alert with the reason and current state.
4. Release requires an explicit operator action, logged in `kill_switch_events`. Daily loss limits reset at a defined UTC time by config, not automatically on restart.

### 10.4 Kill switch

- Engage paths: Telegram command from an allowed chat, authenticated admin API call, or a flag file on the host. All write `kill_switch=1` to Redis and to Postgres.
- Engaged state survives restarts (loaded from Postgres at boot).
- Engaging never needs the exchange connection to succeed.

### 10.5 Failure protection

- Connection down: no new orders. On reconnect, fresh OTP, re-subscribe, reconcile, and only then re-enable.
- Clock skew check against Deriv `time` at startup and periodically.
- Any unhandled exception in the executor engages the kill switch and alerts.

---

## 11. Security assessment and controls

### 11.1 Threats and controls

| Threat | Control |
|---|---|
| Token leak via logs, git, screenshots | Redaction filter on all logs and audit payloads. Pre-commit secret scanning. Test that fake tokens never appear in log output. OTP URL redacted |
| Over-privileged token | PAT scopes limited to what trading needs. Start with `trade` only. Do not grant `account_manage` unless a specific verified need exists. No payment or wallet scopes. Separate PATs for demo and real |
| Accidental live trading | Layered guard in 11.4 |
| Compromised VPS | SSH keys only, no inbound ports except SSH from the owner's IP, non-root containers, minimal image, unattended security updates |
| Exposed admin surface | API binds 127.0.0.1, reached via SSH tunnel or private VPN. Admin bearer token required |
| Telegram takeover | Allowlist of chat IDs, commands limited to status, pause, kill. No command can enable real trading |
| Supply chain | Pinned dependencies with hashes, `pip-audit` in CI, pinned base image digests, review new dependencies |
| Data loss | Daily `pg_dump` to off-host storage, restore tested |
| Silent failure | Heartbeats and an external dead-man's-switch monitor that alerts when heartbeats stop |

### 11.2 Secrets handling

- Local dev: `.env` (git-ignored), permissions 600.
- Oracle VM: root-owned env file with permissions 600 read by the container runtime, or Oracle's secrets service if the owner wants it (verify current options in Oracle docs).
- Never in Docker image layers, never in compose files, never in CI logs.
- Rotation procedure documented in `docs/runbook.md`: revoke PAT in the dashboard, create a new one, update the env file, restart.

### 11.3 Logging

- Structured JSON logs via structlog. Fields: ts, level, component, event, ref_id, req_id.
- A redaction processor strips keys named like `token`, `pat`, `authorization`, `otp`, and URL query strings.

### 11.4 Real-money guard (all must be true; none is enough alone)

1. `TRADING_MODE=real` in config.
2. `ALLOW_REAL_TRADING=true` in env.
3. A signed-off file `REAL_TRADING_APPROVED` containing the owner's approval date and the git commit hash, checked at boot.
4. Real credentials present, with an account ID that the OTP endpoint maps to the real WebSocket endpoint.
5. Live readiness checklist (Phase 11) passed.

Agents must NEVER set items 1 to 3 themselves.

### 11.5 Legal and account safety

The owner must confirm that automated trading is permitted for their Deriv account type, and understand the tax and regulatory position in Kenya. This handbook is not legal or financial advice.

---

## 12. Step-by-step setup

### Step A: Deriv developer access (owner, about 15 minutes)

1. Go to https://developers.deriv.com and sign in or create an account. If it redirects you to your trading account, sign up with a different email.
2. In the Dashboard, register a new application, type **PAT**. Note the App ID.
3. In the API tokens section, create a PAT. Select the minimum scope (`trade` to start). Copy it once into your password manager.
4. Find your demo account ID: call `GET /trading/v1/options/accounts` with the PAT and App ID headers (or create a demo account via `POST` with `account_type: "demo"`).
5. Do NOT create or use a real-account token yet.

### Step B: Local development (agent-executable)

```bash
git init derivbot && cd derivbot
# agent creates the layout in section 6.2
cp .env.example .env        # owner fills demo values only
make dev                    # postgres + redis via docker compose
make test
```

`.env.example`:

```
TRADING_MODE=demo
ALLOW_REAL_TRADING=false

DERIV_APP_ID_DEMO=
DERIV_PAT_DEMO=
DERIV_ACCOUNT_ID_DEMO=

# Real credentials stay EMPTY until Phase 11 is signed off
DERIV_APP_ID_REAL=
DERIV_PAT_REAL=
DERIV_ACCOUNT_ID_REAL=

DATABASE_URL=postgresql+asyncpg://bot:CHANGE_ME@postgres:5432/derivbot
REDIS_URL=redis://redis:6379/0

TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_CHAT_IDS=
ADMIN_API_TOKEN=
LOG_LEVEL=INFO
```

### Step C: First smoke test (Phase 3 entry)

Agent writes `scripts/smoke_demo.py`:

1. REST: POST the OTP endpoint for the demo account.
2. Connect to the returned URL (do not log it).
3. Subscribe to `balance` and one `ticks` stream.
4. Print balance and 5 ticks, then `forget_all`, then close.

Pass criterion: runs against DEMO, prints values, logs contain no secrets.

### Step D: Oracle Cloud VM (when Phase 9 starts)

Verify current Always Free shapes and limits in Oracle's docs, since they change.

1. Create a VM (Ubuntu LTS image recommended for simpler tooling). Add your SSH public key.
2. In the VCN security list, allow inbound TCP 22 only from your own IP (`/32`). No other inbound rules. The bot only makes outbound connections.
3. SSH in. Create a non-root `deploy` user. Disable password login and root login in `sshd_config`.
4. Enable unattended security upgrades and the host firewall.
5. Install Docker and the compose plugin.
6. Clone the repo. Create `/opt/derivbot/.env` with permissions 600, owned by the deploy user. Fill DEMO values only.
7. `docker compose up -d`. Services use `restart: unless-stopped` and healthchecks.
8. Confirm: `docker compose ps` healthy, Telegram startup message received, heartbeat visible.
9. Reach the admin API only through an SSH tunnel to localhost.
10. Schedule the daily `pg_dump` to off-host storage. Perform one restore test.
11. Register the external dead-man's-switch monitor on the heartbeat.
12. Practice: kill switch from Telegram, safe stop (`docker compose stop` after kill switch), restart, confirm reconciliation runs cleanly.

### Step E: Funding (owner, only after the edge gate passes)

Manual, outside the bot, through Deriv's cashier. Prefer methods Deriv itself processes. Avoid payment agents. Check the withdrawal options for your country before depositing. Deposit the minimum, withdraw a little back to prove the round trip, finish identity verification first. Fund only an amount you can lose entirely.

---

## 13. Phased roadmap with gates

```mermaid
flowchart TD
  P0[Phase 0: API verification] --> P3
  P3[Phase 3: Deriv adapter] --> P4[Phase 4: Data collection]
  P4 --> G1{Gate 1: Edge test on holdout}
  G1 -->|fail| STOP[Stop. Write negative result]
  G1 -->|pass| P5[Phase 5-6: Strategies, backtests]
  P5 --> P7[Phase 7: Risk engine]
  P7 --> P8[Phase 8: Demo execution]
  P8 --> G2{Gate 2: Demo matches backtest}
  G2 -->|fail| P5
  G2 -->|pass| P9[Phase 9: VPS deployment]
  P9 --> P10[Phase 10: Independent evaluation]
  P10 --> G3{Gate 3: Live readiness}
  G3 -->|owner signs off| LIVE[Optional tiny live trial]
```

Note on ordering: the edge test (Gate 1) runs on collected data as early as possible, BEFORE building the full strategy library, execution engine, and deployment. This is deliberate. It prevents building infrastructure for a strategy that does not exist.

Each gate is signed off by the owner in `docs/gates.md`.

| Phase | Deliverables | Acceptance criteria |
|---|---|---|
| 0 Verification | `docs/api-notes.md` answering section 3 items | Every item answered with evidence or marked UNVERIFIED with reason |
| 3 Deriv adapter | REST client, WS client, OTP flow, reconnect with backoff, rate limiter, typed models, fake Deriv server | Smoke test passes on DEMO. Fake-server tests cover drop mid-stream, drop mid-`buy`, 401, rate-limit rejection. No secrets in logs |
| 4 Data | Collector, backfill, gap detection, Postgres tables, candles builder | N days of continuous ticks for chosen symbols, gap report, deterministic candle rebuild test |
| Gate 1 | `docs/kill-criterion.md` committed first, power calc, edge test harness, control experiment, holdout lock | Harness finds no edge in random controls. Result recorded honestly |
| 5 and 6 Strategies and backtests | Strategy modules, contract-level backtester, reports | Look-ahead canary caught. Reproducible runs (git commit, params, data range stored) |
| 7 Risk | Risk engine, kill switch, state machine, reconciler | 100 percent branch coverage on risk rules. Property tests for stake monotonicity and state machine. Chaos tests |
| 8 Demo execution | Executor on demo, audit log, Telegram | Weeks of demo with zero unreconciled discrepancies, every order has a full audit trail |
| Gate 2 | Demo vs backtest comparison | Differences explained |
| 9 Deployment | Compose, healthchecks, backups, runbook, daily report | Section 12 step D checklist complete, restart and kill-switch drills passed |
| 10 Evaluation | Independent review of results and code, security audit | Reviewer not the author of the code. Findings closed |
| 11 Live readiness | Checklist, written risk limits, real-guard review | Owner signs. Even then, the first live stage is a trial with an amount the owner can lose entirely |

---

## 14. Testing requirements

- Unit tests for every pure module (signals, risk rules, state machine, stake sizing, metrics, backtester).
- Property tests (hypothesis): state machine legality, stake never increases after a loss, risk engine never approves when a limit is breached.
- Integration tests against a fake Deriv server that can inject: disconnects mid-buy, delayed responses, duplicate messages, out-of-order messages, 401/403, rate-limit errors.
- Reconciliation tests: local and remote disagree in each possible way.
- Security tests: fake token strings never appear in logs, audit payloads, or exceptions. Real mode cannot be enabled by any single flag.
- Backtester tests: look-ahead canary, random-data control, deterministic reproduction.
- CI: ruff, mypy, pytest, coverage threshold, `pip-audit`, secret scan.
- A failing or skipped test blocks merge.

---

## 15. Agent working rules and skills

### 15.1 Rules

1. Follow CLAUDE.md. Follow the phase order.
2. Research and code review are the right uses of Claude. Live decisions are deterministic code. No LLM in the trading loop.
3. Mark anything unchecked as `UNVERIFIED`. Prefer placeholders over invented facts.
4. Report honestly: failing tests, negative results, and unknowns go in the PR description.
5. Do not widen scope. If a task suggests a new feature, write it in `docs/ideas.md` and ask.

### 15.2 Project skills (create in `.claude/skills/<name>/SKILL.md` at the start of Phase 3, expand as phases land)

Each skill needs: when to use it, instructions, links to the relevant official docs, validation steps, and at least one worked example.

| Skill | Must cover |
|---|---|
| `deriv-api` | Endpoints, OTP flow, message shapes, schemas, rate limits, known quirks from `api-notes.md` |
| `market-analysis` | Data handling, resampling, indicator wrappers, regime features |
| `strategy-research` | Hypothesis template, pre-registration, multiple-testing correction |
| `backtesting` | Contract-level simulation, look-ahead rules, splits, controls, metrics |
| `risk-management` | The section 10 rules, config schema, how to add a rule with tests |
| `trading-execution` | State machine, idempotency, reconciliation procedure |
| `monitoring` | Heartbeats, alerts, daily report, health endpoints |
| `security-audit` | Checklist from section 11, secret scanning, real-guard review |

---

## 16. Questions for the owner before Phase 3

1. Which instruments (section 5.3 item 1)?
2. Telegram chat ID(s) allowed to control the bot?
3. Maximum total amount you could lose entirely, if you ever go live (can be "none, demo only")?
4. Who will independently review the work in Phase 10? It should not be the same agent session that wrote it.
