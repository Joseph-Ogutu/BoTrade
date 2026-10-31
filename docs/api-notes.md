# Deriv API notes (Phase 0)

Status: partially answered from official docs. Nothing has been run against the API yet, so every item is marked by its evidence level:

- DOCS: stated in official docs at the URL given, read on 2026-10-31 UTC, before 21:39Z, exact time not recorded (by a fetch of the page, quotes are short and verbatim). Not yet confirmed by a live request.
- NOT FOUND: the docs pages read do not say. Still UNVERIFIED.
- NEEDS DEMO: needs a live request with the owner's demo PAT.

Do not paste PATs, OTP URLs or account IDs here. Use [REDACTED].

## The ten items (HANDBOOK section 3)

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | Contract types, durations, min/max stakes from `contracts_for` on DEMO | PUBLIC VIEW DONE, DEMO still needed | Public endpoint probes below |
| 2 | Real payout ratio from `proposal` per candidate contract (sets p_be) | PUBLIC QUOTES DONE, DEMO still needed | Public endpoint probes below. Binding n_min still waits for the demo account's own quotes |
| 3 | Does `buy` have an idempotency token or client reference | DOCS: no | see 3 below |
| 4 | How `proposal_open_contract` reports settlement; behaviour after reconnect | SCHEMA for fields, NEEDS DEMO for reconnect | see 4 below and Schema findings |
| 5 | `ticks_history` max `count` and pagination | PUBLIC DONE | Public endpoint probes below |
| 6 | OTP single-use and lifetime | DOCS | see 6 below |
| 7 | Fields returned by `portfolio` and `profit_table` | SCHEMA | see Schema findings |
| 8 | Can the demo balance be reset | DOCS: yes | see 8 below |
| 9 | Is legacy `ws.binaryws.com` deprecated, and when | NOT FOUND | see 9 below |
| 10 | Terms on automated trading (Kenya, owner's account type) | NOT FOUND in docs | owner compliance check, not code |

Also from HANDBOOK 4.2: Deriv's own description of how synthetic indices are generated: NOT FOUND in the developer docs (the docs do not use the term and do not describe price generation). The working assumption that synthetic paths are random by design stays UNVERIFIED. Look in Deriv's trading-side help pages, not the developer docs.

## Findings

### 3. buy and duplicate protection (DOCS, read 2026-10-31 UTC)

Sources: https://developers.deriv.com/schemas/buy_request.schema.json and https://developers.deriv.com/docs/best-practices/

- Required request fields: `buy` and `price` ("Maximum price at which to purchase the contract"). Optional: `passthrough` (echoed in `echo_req`), `req_id` ("Used to map request to response"), `parameters` (when `buy` is `1`).
- No idempotency key or client reference field exists.
- Best practices: "A purchase has no request ID lookup: `req_id` only matches a response to its request." and "A proposal ID can't be bought twice, but a buy with parameters isn't deduplicated, so sending `buy: 1` again can open a second contract."
- The docs recommend checking `statement` before resending a purchase whose response was lost.

Design consequences (feeds HANDBOOK 7): always buy by proposal ID, never `buy: 1` with parameters. Keep the BUY_UNKNOWN reconcile-before-anything rule. A proposal-ID buy that is re-sent is rejected by Deriv, but the executor must still not rely on that; confirm the rejection behaviour on DEMO before using it as a safeguard (NEEDS DEMO).

### 4. proposal_open_contract (PARTIAL)

Source: https://developers.deriv.com/docs/trading/proposal-open-contract/ (response schema not rendered in the fetched HTML) and best-practices page.

- Subscribing "flips the `is_sold` and `is_expired` flags the moment the contract settles". A sell is allowed "only when `is_valid_to_sell` is true".
- Fields `sell_price`, `status`, `profit`, `balance_after`: NOT FOUND in the docs read. The old bot assumed `balance_after` exists; that is UNVERIFIED.
- Subscription behaviour after reconnect: NOT FOUND. NEEDS DEMO. Assume subscriptions are lost and re-subscribe after every reconnect.
- Fetch the JSON response schema from the deriv-api-schemas release (HANDBOOK 2.1) to settle the field list without a live call.

### 5. ticks_history (PARTIAL)

Source: https://developers.deriv.com/schemas/ticks_history_request.schema.json

- `count`: "An upper limit on ticks to receive", default 1000, no maximum stated.
- `end`: required, epoch or `latest`. `start`: optional epoch, defaults to 1 day ago for style ticks. `adjust_start_time` exists. `style`: `ticks` or `candles`.
- Maximum `count` and pagination: NOT FOUND. NEEDS DEMO (public endpoint, no auth needed). Test by requesting large counts and by walking `end` backwards.
- Rate-limit group not named; probably "All other calls" (220/min). UNVERIFIED.

### 6. OTP (DOCS)

Source: https://developers.deriv.com/docs/options/websocket/ and best-practices page.

- "The OTP is valid for 120 seconds and can be used only once." "Request a fresh OTP URL before every reconnect."
- Still to confirm on DEMO: behaviour when an expired or reused OTP is presented (error code), so the adapter can handle it.

### 7. portfolio and profit_table (PARTIAL)

Sources: https://developers.deriv.com/docs/account/portfolio/ and https://developers.deriv.com/docs/account/profit-table/

- `portfolio`: "returns only open (active) contracts". Response field names: NOT FOUND (schema tab not rendered).
- `profit_table`: paginated with `limit` and `offset`, default limit 50, maximum 500 per call. Response fields: NOT FOUND.
- Rate limit for both: 30 per minute, 1,500 per hour (shared). Reconcile on a schedule that respects this.
- Get the response schemas from deriv-api-schemas, then confirm one live response on DEMO.

### 8. Demo balance reset (DOCS)

Source: https://developers.deriv.com/docs/options/reset-demo-balance/

- REST `POST /trading/v1/options/accounts/{account_id}/reset-demo-balance` on `https://api.derivws.com`. Demo accounts only. Resets to $10,000 USD. 200 with no body on success. Needs the `trade` scope and `Deriv-App-ID` header for PATs.
- Frequency limit: NOT FOUND (general REST limits apply).
- Design consequence: the bot must never call this endpoint. A reset would corrupt balance-based sizing and drawdown tracking. Block it in the REST client by allow-list.

### 9. Legacy endpoint (NOT FOUND)

- No page in the developer docs mentions `wss://ws.binaryws.com/websockets/v3`, and no deprecation or shutdown date was found. All WebSocket URLs in the docs are on `api.derivws.com`.
- The docs' "legacy" section covers temporary read-only REST endpoints (migration status, accounts, statement) and says they will be removed "once all users have been fully migrated". That is a different thing from the old WebSocket API.
- Handbook claim that the old endpoint "still appears in some docs pages" was not reproduced. Decision stands: do not build on it.

### 10. Terms (NOT FOUND in developer docs)

- No terms-of-service page and no statement permitting or prohibiting bots. The API overview lists automated strategies as a capability of the platform's own Automation API. That is not permission for third-party bots on the owner's account type.
- Limits page: Deriv reserves the right to change limits and invites contact about application-specific limits.
- Owner action: check Deriv's terms and ask support, in writing, whether automated trading via PAT is allowed for the account type and Kenya.

## Schema findings (official deriv-api-schemas, vendored in schemas/deriv, downloaded 2026-10-31T21:50Z)

Source and checksum: schemas/deriv/SOURCE.md. These are the published JSON schemas, not live responses. Live confirmation on DEMO is still needed.

- Proposal request uses `underlying_symbol`, not `symbol` (required: `proposal`, `contract_type`, `currency`, `underlying_symbol`). The old bot used `symbol`. Requests have `additionalProperties: false`, so unknown fields are rejected by the schema.
- `buy` and `forget` ids must match `^[\w-]{32,128}$` (parameter buy is the literal `1`). The adapter refuses `1`.
- buy_response (`buy`) has `balance_after`, `buy_price`, `contract_id`, `payout`, `transaction_id`, `purchase_time`, `start_time`. So balance_after comes from the buy response.
- proposal_open_contract response has no `balance_after`. The old bot read it from there (a defect noted in docs/existing-bot-audit.md section I). It has `is_sold`, `is_expired`, `status`, `sell_price`, `profit`, `buy_price`, `payout`, `entry_spot`, `exit_spot`, `date_settlement`. Money fields there are STRINGS (`profit`, `payout`, `sell_price`, `buy_price`). The `status` description says it is `sold` if sold back early; the exact values at expiry (won/lost) are UNVERIFIED.
- portfolio contracts: `contract_id`, `buy_price`, `payout`, `contract_type`, `underlying_symbol`, `transaction_id`, `purchase_time`, `expiry_time`, `date_start`, `currency`, `longcode`, `shortcode`, `auto_run_id`. No `is_expired` field, so the old bot's reconcile filter (`is_expired == 0`) cannot work against this schema.
- profit_table transactions: `contract_id`, `buy_price`, `sell_price`, `payout`, `purchase_time`, `sell_time`, `transaction_id`, `underlying_symbol`, `contract_type`, `longcode`, `shortcode`, `duration_type`, plus multiplier fields. `count` is returned alongside.
- tick: `symbol`, `epoch`, `quote`, `pip_size` (required), `ask`, `bid`, `id`. balance: `balance`, `currency`, `loginid` (required), `id`.
- The error object shape is NOT in these schemas. The adapter assumes `{"error": {"code", "message"}}` as the old bot saw on the legacy API. UNVERIFIED for the new API. Error code strings used by the fake server are invented.
- ws_public request: "No authentication, OTP, or query parameters are required."
- OpenAPI file lists REST paths including `/trading/v1/options/accounts`, `.../accounts/{accountId}/otp`, `.../accounts/{account_id}/reset-demo-balance` and `bulk-purchase`. The bot must allow-list REST paths and never call reset-demo-balance, bulk-purchase, or anything outside accounts and otp.

## Live check: public endpoint (2026-10-31, about 21:57Z to 21:59Z, no credentials)

- `wss://api.derivws.com/trading/v1/options/ws/public` accepted a connection with no token, no OTP and no App ID, and streamed ticks for `1HZ100V` and `R_100` subscribed on one connection.
- Cadence: `1HZ100V` about 1 tick per second, `R_100` about 1 tick per 2 seconds. Epochs were contiguous with no gaps in two runs of about 15 to 25 seconds each (24 and 23 ticks). pip_size was 2 for both in the fake only; the live value was not recorded here.
- Connection setup took about 6 seconds in one run (DNS/TLS). Do not assume instant connects.
- Tooling finding: the `websockets` library logs the full request URL, including `?otp=...`, at DEBUG. `derivbot.common.logs.quiet_transport_loggers()` caps those loggers and is covered by a test. Any process that uses OTP URLs must call `setup_logging()`.

## Public endpoint probes (2026-10-31, captured 22:00:51Z to 22:17Z, no credentials)

Raw responses are committed in docs/evidence/. Probe scripts: scripts/probe_public.py, scripts/probe_public_batch.py. All requests were read-only (market data and proposal quotes). No buy/sell was sent. The public endpoint is `wss://api.derivws.com/trading/v1/options/ws/public`. Everything here is the PUBLIC view; the DEMO account may differ. UNVERIFIED for demo.

A first version of the batch probe matched replies by arrival order, not `req_id`, and one slow reply shifted every later answer onto the wrong request. It was fixed to match on `req_id` and rerun; only the corrected run is used. Lesson for the adapter: always match on `req_id` and never on order (the adapter's registry already does).

### active_symbols (item 1, part)

`{"active_symbols":"brief"}` works unauthenticated and returned 89 symbols. Full list grouped by market: docs/instruments.md (generated). Summary:

| market | count | non-synthetic? |
|---|---|---|
| synthetic_index | 46 | no (includes Volatility, 1s Volatility, Boom/Crash, Jump, Step, Range Break, Bull/Bear, and WLD* forex/commodity baskets, which Deriv also classes as synthetic) |
| forex | 25 | yes |
| indices (OTC_*) | 12 | yes by Deriv's market label. Whether the price feed is a real index or Deriv-simulated is UNVERIFIED |
| commodities | 4 | yes (metals) |
| cryptocurrency | 2 (BTC/USD, ETH/USD) | yes |

At query time (2026-10-31T22:00:51Z: a Saturday in UTC, Sunday 2026-11-01 01:00 in Nairobi) forex, OTC indices, metals and the baskets showed `exchange_is_open: 0`; crypto and the synthetic indices were open. No symbol was suspended.

### contracts_for (item 1, part)

`{"contracts_for":"<symbol>"}` works unauthenticated. 1HZ100V and R_100 each return 65 contract entries; cryBTCUSD 2 (multipliers only); frxEURUSD 20; OTC_SPC 14.

- Synthetic (1HZ100V, R_100): digits (DIGITEVEN/ODD/OVER/UNDER/MATCH/DIFF, 1 to 10 ticks), call/put and higher/lower including tick durations (1t to 10t for CALL/PUT, 5t to 10t for HIGHER/LOWER), asian, touch/no-touch, reset, runs, tick high/low, multipliers, accumulators, turbos, vanilla, ends/stays in-out. Intraday from 15s, daily 1d to 365d.
- Non-synthetic have NO short fixed-payout contracts: crypto offers only multipliers (MULTUP/MULTDOWN); forex offers call/put and higher/lower from 15 minutes (intraday) and daily, plus multipliers, touch, ends/stays; OTC index offers 15 minutes to 1 hour call/put and daily and longer contracts. Consequence: a contract-level backtest on non-synthetics must use 15m+ or daily horizons, so far fewer independent trades per day than the 5-tick synthetics.
- `contracts_for` entries carry `default_stake` (2) but not min/max stake. Min stake comes from proposal errors (below).

### proposal quotes without auth (item 2)

Works on the public endpoint, no token and no App ID. Response has `proposal.id` (UUID, 36 chars with hyphens, matches the schema pattern), `ask_price`, `payout`, `spot`, `spot_time`, `date_start`, `date_expiry`, `longcode`, `contract_details.barrier`, and `validation_params` (stake and payout limits). Reply latency was 0.3 to 0.6 s. Subscribed proposals returned one update in a 6 s window (UNVERIFIED whether they stream updates for other contract types or when authenticated).

Quotes at stake 1.00 USD, 5 ticks, identical on 1HZ100V and R_100:

| contract | payout | r | p_be |
|---|---|---|---|
| DIGITEVEN | 1.95 | 0.95 | 0.5128 |
| DIGITODD | 1.95 | 0.95 | 0.5128 |
| DIGITUNDER barrier 5 | 1.95 | 0.95 | 0.5128 |
| CALL (rise) | 1.95 | 0.95 | 0.5128 |
| PUT (fall) | 1.95 | 0.95 | 0.5128 |
| DIGITOVER barrier 5 | 2.43 | 1.43 | 0.4115 |
| DIGITMATCH barrier 5 | 8.93 | 7.93 | 0.1120 |
| DIGITDIFF barrier 5 | 1.09 | 0.09 | 0.9174 |

- r depends on stake: at the minimum stake 0.35, DIGITEVEN paid 0.66 (r = 0.886, p_be = 0.5303) versus r = 0.95 at 1.00. At 2000 the implied payout was 3900.84 (r = 0.95). So r must be taken from the proposal for the stake actually used, never assumed (this reinforces the kill criterion).
- If last digits are uniform (an assumption, not tested here), P(even) = 0.5 and expectancy at r = 0.95 is 0.5 x 0.95 - 0.5 = -0.025 per unit stake: a 2.5% house edge on a fair coin. This is arithmetic on that assumption only.
- Limits seen in error messages: minimum stake 0.35, maximum payout 1000.00 (per contract).
- Planning table in docs/power-table.md for r = 0.95 (n_min 3857 for K=1) applies to the 1.00 stake quotes above. The binding value still needs the demo account's own quotes.

### Error objects actually observed (public endpoint)

Shape confirmed live: `{"echo_req": {...}, "error": {"code": str, "message": str, "details"?: {...}}, "msg_type": str, "req_id": int}`. `msg_type` names the request type that failed. Codes observed:

| trigger | code | notes |
|---|---|---|
| stake below 0.35 | `ContractBuyValidationError` | `details.field = "amount"` |
| payout above 1000 | `ContractBuyValidationError` | `details.field = "amount"` |
| unknown contract type | `ContractBuyValidationError` | message "Invalid contract type." |
| bad multiplier | `ContractBuyValidationError` | `details.field = "multiplier"`; message lists accepted values (50, 100, 150, 200, 300 for BTC) |
| forex short duration at night | `ContractBuyValidationError` | `details.field = "duration"`; "not available from 21:00:00 to 23:59:59" |
| unknown symbol | `InvalidInputAsset` | `details.field = "symbol"` |
| malformed proposal request | `InputValidationFailed` | `details` is a map of field to reason |
| balance or portfolio without auth | `AuthorizationRequired` | "Please log in." |

Not observed, so the code strings are UNVERIFIED: InsufficientBalance (the old bot's string), rate-limit rejection, buy of an already used proposal, expired or reused OTP, invalid or expired proposal id on buy, market closed on buy, contract not found.
The server tolerated an unknown extra field on `ping` (it replied normally), so the schemas' `additionalProperties: false` is not enforced by the server for at least ping. Do not rely on the server to reject malformed requests.

### ticks_history (item 5)

- Public endpoint: `count` of 5000 and 100000 were silently clamped to 1000 ticks, no error. Maximum per request therefore 1000 (public view; authenticated UNVERIFIED).
- Paging backwards works: a second request with `end = first_time - 1` returned the previous 1000 ticks, contiguous with the first page (no gap, no overlap).
- Retention: requests with `end` 1, 3, 6 and 12 hours in the past honoured `end` exactly. With `end` 24 hours or more in the past the server did NOT error: it returned the LATEST ticks instead. History depth is therefore between 12 and 24 hours on the public view (exact limit not pinned; UNVERIFIED if authenticated differs).
- Backfill rule that follows: after every `ticks_history` call, verify that every returned time is `<= end` and that the page is contiguous; otherwise discard it. A silent fall back to latest ticks would pollute the store with mislabelled data.
- Consequence for research: there is no deep history to download. All research data must be collected forward in time by the collector. `time` request works and returns server time.

## Rate limits (DOCS, https://developers.deriv.com/docs/limits/)

Matches HANDBOOK 2.4. WebSocket: proposal/proposal_open_contract/buy/sell 360 per minute and 14,400 per hour; balance/statement 100 and 2,000; portfolio/profit_table 30 and 1,500; all other 220 and 14,400; ping outside budgets. REST: 300 per minute and 1,000 per 10 minutes per IP, 80 per minute per user. Keep these as configuration.

## Next live checks (once the demo PAT is in .env)

1. `GET /trading/v1/options/accounts`: record the shape and how demo and real accounts are distinguished (needed for the fail-closed mode guard).
2. OTP request, connect, `balance` subscribe, then one `ticks` stream.
3. `active_symbols`, then `contracts_for` per candidate symbol (item 1).
4. `proposal` for each candidate contract; record `ask_price` and `payout` (item 2).
5. Public endpoint: `ticks_history` limits (item 5).
6. OTP reuse and expiry error codes (item 6).
7. Response schemas for items 4 and 7 from deriv-api-schemas.
