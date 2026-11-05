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
       (SELECT b.balance FROM balance_snapshots b WHERE b.ts = o.opened_at ORDER BY b.id LIMIT 1)
FROM order_intents i
LEFT JOIN orders o ON o.intent_id = i.id
LEFT JOIN risk_decisions rd ON rd.intent_id = i.id
WHERE i.created_at >= :since ORDER BY i.created_at, i.id""")


def _d(v: object) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def load_local(engine, since: dt.datetime) -> tuple[list[LocalTrade], dict[str, list[tuple[str | None, str, str]]]]:
    with engine.connect() as c:
        rows = c.execute(LOCAL_SQL, {"since": since}).all()
        evs = c.execute(text("SELECT e.intent_id::text, e.from_state, e.event, e.to_state FROM order_events e "
                             "JOIN order_intents i ON i.id = e.intent_id WHERE i.created_at >= :since ORDER BY e.id"),
                        {"since": since}).all()
    local = [LocalTrade(r[0], r[1], Decimal(str(r[2])), r[3], r[4], _d(r[5]), _d(r[6]), _d(r[7]), r[8], _d(r[12]), _d(r[11]), r[10], r[9])
             for r in rows]
    events: dict[str, list[tuple[str | None, str, str]]] = {}
    for iid, frm, ev, to in evs:
        events.setdefault(iid, []).append((frm, ev, to))
    return local, events


async def load_exchange(session: DerivSession, contract_ids: list[int]) -> tuple[list[ExchangeTrade], dict[int, ContractFacts]]:
    reply = await session.request(m.req_profit_table(session.next_req_id(), limit=500), timeout=20)
    rows = (reply.get("profit_table") or {}).get("transactions") or []
    ex = [ExchangeTrade(int(r["contract_id"]), Decimal(str(r["buy_price"])), Decimal(str(r["payout"])),
                        Decimal(str(r["sell_price"])), int(r["purchase_time"])) for r in rows
          if all(k in r for k in ("contract_id", "buy_price", "payout", "sell_price", "purchase_time"))]
    facts: dict[int, ContractFacts] = {}
    for cid in contract_ids:
        r = await session.request({"proposal_open_contract": 1, "contract_id": cid, "req_id": session.next_req_id()}, timeout=20)
        p = r.get("proposal_open_contract") or {}
        facts[cid] = ContractFacts(cid, str(p.get("contract_type", "")), _d(p.get("entry_spot")), _d(p.get("exit_spot")), p.get("status"))
        await asyncio.sleep(0.3)                                          # stay well inside the rate budget
    return ex, facts


async def amain(since: dt.datetime) -> int:
    env = dict(os.environ)
    if os.environ.get("ENV_FILE"):
        env = {**parse_env_file(os.environ["ENV_FILE"]), **env}
    settings = load_settings(env)
    cfg = load_risk_config(ROOT / "config" / "risk.json")
    engine = make_engine(settings.database_url.get_secret_value())
    local, events = load_local(engine, since)
    if not local:
        print("no trades recorded since", since.isoformat(), "- nothing to audit yet")
        return 0
    provider = HttpOtpProvider.from_settings(settings)
    session = DerivSession(provider.get_url, endpoint_classifier=partial(classify_url, policy=PROD_POLICY))
    await session.connect()
    try:
        ids = [t.contract_id for t in local if t.contract_id is not None and t.state == "SETTLED"]
        exchange, facts = await load_exchange(session, ids)
    finally:
        await session.close()
    starts = [int(t.opened_at.timestamp()) for t in local if t.opened_at]
    data = AuditData(local, exchange, facts, events, cfg.risk_per_trade_pct, cfg.max_stake_abs,
                     window_start_epoch=(min(starts) - 5) if starts else 0)
    checks = run_checks(data)
    print(render(checks, len(local)))
    return 0 if all(c.passed for c in checks) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Accuracy audit of the bot's records against the exchange's (read only)")
    ap.add_argument("--since", default="1970-01-01T00:00:00Z", help="only trades created at or after this UTC time")
    return asyncio.run(amain(parse_utc(ap.parse_args().since)))


if __name__ == "__main__":
    sys.exit(main())
