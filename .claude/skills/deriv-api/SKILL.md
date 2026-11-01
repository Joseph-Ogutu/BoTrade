---
name: deriv-api
description: Use when writing or changing anything that talks to Deriv (REST OTP, WebSocket messages, ticks, proposal, buy, portfolio, profit_table), or when reading Deriv responses. Covers endpoints, the OTP flow, message shapes, rate limits and the known quirks.
---

# deriv-api

## When to use
Any change under `src/derivbot/deriv/`, `src/derivbot/execution/snapshot.py`, `src/derivbot/guard/otp.py`, `src/derivbot/collector/`, or `tests/fakes/fake_deriv.py`. Also when you need to know "does the API do X".

## Ground rules
1. The source of truth for behavior is `docs/api-notes.md`. Every statement there carries an evidence level: DOCS, NOT FOUND, NEEDS DEMO, or measured on the public endpoint. Do not promote a claim past its level.
2. Nothing authenticated has ever run. OTP, balance, buy, portfolio and profit_table behavior is UNVERIFIED until the owner supplies demo credentials. Tag assumptions in code with `UNVERIFIED:` and error codes with `UNVERIFIED-ERR:` or `OBSERVED-ERR:`. `tests/unit/test_unverified_tags.py` fails if an error code literal is untagged or missing from STATE.md section 9b.
3. Never open `.env` or token files. Never log a PAT or an OTP URL (the URL is a secret). Use `derivbot.common.redaction`.

## Facts (see docs/api-notes.md for evidence)
- Public (no credentials): `wss://api.derivws.com/trading/v1/options/ws/public`. Ticks, `ticks_history`, `active_symbols`, `contracts_for`, `proposal` quotes work unauthenticated.
- Authenticated: `POST /trading/v1/options/accounts/{id}/otp` with `Authorization: Bearer <PAT>` and `Deriv-App-ID`; the answer carries `data.url`, a WebSocket URL for `.../ws/demo` (or `real`). The OTP is single use and lives about 120 s (DOCS). Always request a fresh URL per connection (`SessionSupervisor` does).
- `proposal` uses `underlying_symbol`. `buy` is by proposal id with a price cap. There is no idempotency token (DOCS): a lost buy reply must be resolved by looking at the exchange, never by buying again.
- `ticks_history` returns at most 1000 per call; retention is 12 to 24 h; asking for older data silently returns the latest ticks (check the epoch range you got).
- A closed market answers a ticks subscription with `MarketIsClosed` (OBSERVED). Resubscribe on a timer.
- Rate limits: `derivbot.deriv.ratelimit.RateLimiter` groups message types. The numbers are guesses until the limits page is confirmed; keep headroom.

## Instructions
1. Parse with the typed models in `derivbot.deriv.messages` (pydantic). Money and quotes are `Decimal`, never float.
2. Match replies by `req_id`. Handle `ConnectionLost` and `RequestTimeout` as "outcome unknown", not "failed".
3. New message type: add the model, add it to the fake server, add a test, tag unverified fields.
4. Never point a test at a real endpoint. Use `tests/fakes/fake_deriv.py` (failure injection: drop_before, drop_after, swallow, delay, duplicate, error, hold).

## Validation
- `.venv/bin/python -m pytest tests/unit tests/execution -o addopts="" -q`
- `grep -rn "UNVERIFIED" src | wc -l` should not shrink unless a real credentialed run produced evidence.

## Worked example: add a `balance` field the executor needs
1. Read the schema URL in docs/api-notes.md; note whether `loginid` appears on the demo endpoint (UNVERIFIED).
2. Extend the model in `deriv/messages.py` with the field optional, and add a comment `UNVERIFIED: present on demo balance`.
3. Teach `tests/fakes/fake_deriv.py` to send it; test that a missing field makes the consumer fail closed.
4. Add the assumption to STATE.md section 9.

Official docs: https://developers.deriv.com/docs/options/websocket/ , https://developers.deriv.com/docs/limits/ , https://developers.deriv.com/docs/best-practices/ , https://developers.deriv.com/docs/account/portfolio/ , https://developers.deriv.com/docs/account/profit-table/ , https://developers.deriv.com/docs/trading/proposal-open-contract/
