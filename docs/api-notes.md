# Deriv API notes (Phase 0)

Status: partially answered from official docs. Nothing has been run against the API yet, so every item is marked by its evidence level:

- DOCS: stated in official docs at the URL given, read on 2026-11-01 (by a fetch of the page, quotes are short and verbatim). Not yet confirmed by a live request.
- NOT FOUND: the docs pages read do not say. Still UNVERIFIED.
- NEEDS DEMO: needs a live request with the owner's demo PAT.

Do not paste PATs, OTP URLs or account IDs here. Use [REDACTED].

## The ten items (HANDBOOK section 3)

| # | Item | Status | Evidence |
|---|---|---|---|
| 1 | Contract types, durations, min/max stakes from `contracts_for` on DEMO | NEEDS DEMO | none yet |
| 2 | Real payout ratio from `proposal` per candidate contract (sets p_be) | NEEDS DEMO | none yet. Blocks n_min (see kill-criterion.md) |
| 3 | Does `buy` have an idempotency token or client reference | DOCS: no | see 3 below |
| 4 | How `proposal_open_contract` reports settlement; behaviour after reconnect | SCHEMA for fields, NEEDS DEMO for reconnect | see 4 below and Schema findings |
| 5 | `ticks_history` max `count` and pagination | PARTIAL, NOT FOUND for max | see 5 below |
| 6 | OTP single-use and lifetime | DOCS | see 6 below |
| 7 | Fields returned by `portfolio` and `profit_table` | SCHEMA | see Schema findings |
| 8 | Can the demo balance be reset | DOCS: yes | see 8 below |
| 9 | Is legacy `ws.binaryws.com` deprecated, and when | NOT FOUND | see 9 below |
| 10 | Terms on automated trading (Kenya, owner's account type) | NOT FOUND in docs | owner compliance check, not code |

Also from HANDBOOK 4.2: Deriv's own description of how synthetic indices are generated: NOT FOUND in the developer docs (the docs do not use the term and do not describe price generation). The working assumption that synthetic paths are random by design stays UNVERIFIED. Look in Deriv's trading-side help pages, not the developer docs.

## Findings

### 3. buy and duplicate protection (DOCS, 2026-11-01)

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

## Schema findings (official deriv-api-schemas, vendored in schemas/deriv, 2026-11-01)

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

## Live check: public endpoint (2026-11-01, no credentials)

- `wss://api.derivws.com/trading/v1/options/ws/public` accepted a connection with no token, no OTP and no App ID, and streamed ticks for `1HZ100V` and `R_100` subscribed on one connection.
- Cadence: `1HZ100V` about 1 tick per second, `R_100` about 1 tick per 2 seconds. Epochs were contiguous with no gaps in two runs of about 15 to 25 seconds each (24 and 23 ticks). pip_size was 2 for both in the fake only; the live value was not recorded here.
- Connection setup took about 6 seconds in one run (DNS/TLS). Do not assume instant connects.
- Tooling finding: the `websockets` library logs the full request URL, including `?otp=...`, at DEBUG. `derivbot.common.logs.quiet_transport_loggers()` caps those loggers and is covered by a test. Any process that uses OTP URLs must call `setup_logging()`.

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
