"""Run the accuracy audit against the live demo account and the bot's own database. READ ONLY.

    docker compose exec -T bot python -m derivbot.audit            # on the VM
    python -m derivbot.audit --since 2026-11-05T19:30:00Z          # only trades created since then

It opens its own demo session (OTP) and asks the exchange for its profit table and each contract's entry and exit spot. It
sends no buy, no sell, no write. Exit code 0 only if every check passed.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import os
import sys
from decimal import Decimal
from functools import partial
from pathlib import Path

from sqlalchemy import text

from derivbot.audit.accuracy import AuditData, ContractFacts, ExchangeTrade, LocalTrade, render, run_checks
from derivbot.common.timeutil import parse_utc
from derivbot.config.risk import load_risk_config
from derivbot.config.settings import load_settings, parse_env_file
from derivbot.db.engine import make_engine
from derivbot.deriv import messages as m
from derivbot.deriv.session import DerivSession
from derivbot.guard.endpoint import PROD_POLICY, classify_url
from derivbot.guard.otp import HttpOtpProvider

ROOT = Path(__file__).resolve().parents[3]
LOCAL_SQL = text("""
SELECT i.id::text, i.contract_type, i.stake, i.state, o.deriv_contract_id, o.buy_price, o.payout, o.profit, o.opened_at,
       i.created_at, rd.approved, rd.state_snapshot->>'balance',
       (SELECT b.balance FROM balance_snapshots b WHERE b.ts = o.opened_at ORDER BY b.id LIMIT 1),
       cv.content->>'max_stake_abs', cv.content->>'risk_per_trade_pct'
FROM order_intents i
LEFT JOIN orders o ON o.intent_id = i.id
LEFT JOIN risk_decisions rd ON rd.intent_id = i.id
LEFT JOIN config_versions cv ON cv.id = rd.config_version_id
WHERE i.created_at >= :since AND i.mode = :mode ORDER BY i.created_at, i.id""")


def _d(v: object) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def load_local(engine, since: dt.datetime, mode: str = "demo") -> tuple[list[LocalTrade], dict[str, list[tuple[str | None, str, str]]]]:
    with engine.connect() as c:
        rows = c.execute(LOCAL_SQL, {"since": since, "mode": mode}).all()
        evs = c.execute(text("SELECT e.intent_id::text, e.from_state, e.event, e.to_state FROM order_events e "
                             "JOIN order_intents i ON i.id = e.intent_id WHERE i.created_at >= :since AND i.mode = :mode ORDER BY e.id"),
                        {"since": since, "mode": mode}).all()
    local = [LocalTrade(r[0], r[1], Decimal(str(r[2])), r[3], r[4], _d(r[5]), _d(r[6]), _d(r[7]), r[8], _d(r[12]), _d(r[11]), r[10], r[9], _d(r[13]), _d(r[14]))
             for r in rows]
    events: dict[str, list[tuple[str | None, str, str]]] = {}
    for iid, frm, ev, to in evs:
        events.setdefault(iid, []).append((frm, ev, to))
    return local, events


FACT_KEY = "audit:fact:"
FACT_TTL_S = 30 * 86400
MAX_TRIES = 4
BACKOFF_S = 20.0


def _fact_to_json(f: ContractFacts) -> str:
    import json
    return json.dumps({"t": f.contract_type, "e": str(f.entry_spot), "x": str(f.exit_spot), "s": f.status})


def _fact_from_json(cid: int, raw: str) -> ContractFacts | None:
    import json
    try:
        j = json.loads(raw)
        return ContractFacts(cid, str(j["t"]), Decimal(j["e"]), Decimal(j["x"]), j.get("s"))
    except (ValueError, KeyError, TypeError, ArithmeticError):
        return None


async def _fetch_facts(session: DerivSession, cid: int, sleep) -> ContractFacts | None:
    """One contract's final prices. On a RateLimit reply it waits and tries again, so a burst never leaves silent gaps."""
    for attempt in range(MAX_TRIES):
        r = await session.request({"proposal_open_contract": 1, "contract_id": cid, "req_id": session.next_req_id()}, timeout=20)
        err = r.get("error")
        if err:
            if err.get("code") == "RateLimit" and attempt < MAX_TRIES - 1:
                await sleep(BACKOFF_S * (attempt + 1))
                continue
            return None
        p = r.get("proposal_open_contract") or {}
        entry, exit_, status = _d(p.get("entry_spot")), _d(p.get("exit_spot")), p.get("status")
        return ContractFacts(cid, str(p.get("contract_type", "")), entry, exit_, status)
    return None


async def load_exchange(session: DerivSession, contract_ids: list[int], kv=None, sleep=asyncio.sleep,
                        spacing_s: float = 1.2) -> tuple[list[ExchangeTrade], dict[int, ContractFacts]]:
    """The exchange's profit table plus each contract's entry and exit price. A settled contract never changes, so its prices
    are cached in the key value store: after the first run only NEW trades are asked about, which keeps the audit far inside
    the contract lookup budget (see deriv/ratelimit.py)."""
    reply = await session.request(m.req_profit_table(session.next_req_id(), limit=500), timeout=20)
    rows = (reply.get("profit_table") or {}).get("transactions") or []
    ex = [ExchangeTrade(int(r["contract_id"]), Decimal(str(r["buy_price"])), Decimal(str(r["payout"])),
                        Decimal(str(r["sell_price"])), int(r["purchase_time"])) for r in rows
          if all(k in r for k in ("contract_id", "buy_price", "payout", "sell_price", "purchase_time"))]
    facts: dict[int, ContractFacts] = {}
    for cid in contract_ids:
        raw = kv.get(FACT_KEY + str(cid)) if kv is not None else None
        cached = _fact_from_json(cid, raw) if raw else None
        if cached is not None:
            facts[cid] = cached
            continue
        f = await _fetch_facts(session, cid, sleep)
        if f is not None:
            facts[cid] = f
            if kv is not None and f.entry_spot is not None and f.exit_spot is not None and f.status in ("won", "lost"):
                kv.set(FACT_KEY + str(cid), _fact_to_json(f), FACT_TTL_S)         # final: safe to keep
        await sleep(spacing_s)                                                  # stay well inside the lookup budget
    return ex, facts


async def audit_once(engine, cfg, session: DerivSession, since: dt.datetime, kv=None, mode: str = "demo") -> tuple[list, int]:
    """Run every check with an already connected session. Returns (checks, number of trades audited). Read only."""
    local, events = load_local(engine, since, mode)          # only this account's trades: a demo trade is never expected on the real account
    if not local:
        return [], 0
    ids = [t.contract_id for t in local if t.contract_id is not None and t.state == "SETTLED"]
    exchange, facts = await load_exchange(session, ids, kv)
    starts = [int(t.opened_at.timestamp()) for t in local if t.opened_at]
    data = AuditData(local, exchange, facts, events, cfg.risk_per_trade_pct, cfg.max_stake_abs,
                     window_start_epoch=(min(starts) - 5) if starts else 0)
    return run_checks(data), len(local)


async def amain(since: dt.datetime, store: bool = False) -> int:
    env = dict(os.environ)
    if os.environ.get("ENV_FILE"):
        env = {**parse_env_file(os.environ["ENV_FILE"]), **env}
    settings = load_settings(env)
    cfg = load_risk_config(ROOT / "config" / "risk.json")
    engine = make_engine(settings.database_url.get_secret_value())
    import redis

    from derivbot.audit.loop import store_verdict
    from derivbot.deriv.ratelimit import DEFAULT_LIMITS, RateLimiter
    from derivbot.kv.store import RedisStore

    kv = RedisStore(redis.Redis.from_url(settings.redis_url.get_secret_value(), socket_connect_timeout=3))
    provider = HttpOtpProvider.from_settings(settings)
    session = DerivSession(provider.get_url, limiter=RateLimiter(DEFAULT_LIMITS),
                           endpoint_classifier=partial(classify_url, policy=PROD_POLICY))
    await session.connect()
    try:
        checks, n = await audit_once(engine, cfg, session, since, kv)
    finally:
        await session.close()
    if not n:
        print("no trades recorded since", since.isoformat(), "- nothing to audit yet")
        return 0
    print(render(checks, n))
    if store:
        store_verdict(kv, checks, n, dt.datetime.now(dt.UTC))
        print("verdict stored: the dashboard now shows this result")
    return 0 if all(c.passed for c in checks) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Accuracy audit of the bot's records against the exchange's (read only)")
    ap.add_argument("--since", default="1970-01-01T00:00:00Z", help="only trades created at or after this UTC time")
    ap.add_argument("--store", action="store_true", help="also save the verdict so the dashboard shows it (replaces the previous one)")
    args = ap.parse_args()
    return asyncio.run(amain(parse_utc(args.since), args.store))


if __name__ == "__main__":
    sys.exit(main())
