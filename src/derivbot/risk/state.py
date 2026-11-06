"""Read the persisted state the risk rules judge. Every query here is explicit about which states count as exposure,
what a loss is, and where the risk day starts, so a rule can never silently see a different world than the one recorded
in the snapshot."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import Connection, text

from derivbot.execution.state_machine import EXPOSURE

_EXPOSURE_SQL = ", ".join(f"'{s.value}'" for s in EXPOSURE)
_IGNORED_FOR_DUPLICATES = "'RISK_REJECTED', 'PROPOSAL_REJECTED', 'NOT_PLACED'"


def day_start(now: dt.datetime, reset_utc_hour: int) -> dt.datetime:
    """Most recent daily reset boundary at or before `now`, in UTC. Fixed by config, never by process restarts."""
    now = now.astimezone(dt.UTC)
    boundary = now.replace(hour=reset_utc_hour, minute=0, second=0, microsecond=0)
    return boundary if boundary <= now else boundary - dt.timedelta(days=1)


def open_positions(conn: Connection) -> int:
    """Intents that may hold a contract. BUY_UNKNOWN and MANUAL_REVIEW count: the contract may exist."""
    return int(conn.execute(text(f"SELECT count(*) FROM order_intents WHERE state IN ({_EXPOSURE_SQL})")).scalar_one())


def realized_pnl_since(conn: Connection, since: dt.datetime) -> Decimal:
    v = conn.execute(text("SELECT COALESCE(SUM(profit), 0) FROM orders WHERE state IN ('SETTLED', 'SOLD') "
                          "AND settled_at >= :s"), {"s": since}).scalar_one()
    return Decimal(v)


def consecutive_losses(conn: Connection, limit: int = 500) -> int:
    """Newest settled orders first; counts strictly negative profits until the first result that is not a loss."""
    rows = conn.execute(text("SELECT profit FROM orders WHERE state IN ('SETTLED', 'SOLD') AND profit IS NOT NULL "
                             "ORDER BY settled_at DESC, id DESC LIMIT :n"), {"n": limit}).all()
    n = 0
    for (profit,) in rows:
        if Decimal(profit) < 0:
            n += 1
        else:
            break
    return n


def trades_in_last_hour(conn: Connection, now: dt.datetime) -> int:
    """Buys sent in the last 60 minutes, counted from the order_events log (a buy sent is a trade, even if the reply
    was lost)."""
    return int(conn.execute(text("SELECT count(*) FROM order_events WHERE to_state = 'BUY_SENT' AND ts > :t"),
                            {"t": now - dt.timedelta(hours=1)}).scalar_one())


def trades_since(conn: Connection, since: dt.datetime) -> int:
    """Buys sent since the risk day began (a buy sent is a bet, even if its reply was lost)."""
    return int(conn.execute(text("SELECT count(*) FROM order_events WHERE to_state = 'BUY_SENT' AND ts >= :s"), {"s": since}).scalar_one())


def losses_since(conn: Connection, since: dt.datetime) -> int:
    """Settled bets that lost (strictly negative profit) since the risk day began."""
    return int(conn.execute(text("SELECT count(*) FROM orders WHERE state IN ('SETTLED', 'SOLD') AND profit < 0 AND settled_at >= :s"),
                            {"s": since}).scalar_one())


def consecutive_wins_since(conn: Connection, since: dt.datetime, limit: int = 500) -> int:
    """Newest settled bets of the risk day first; counts strictly positive profits until the first result that is not a win."""
    rows = conn.execute(text("SELECT profit FROM orders WHERE state IN ('SETTLED', 'SOLD') AND profit IS NOT NULL AND settled_at >= :s "
                             "ORDER BY settled_at DESC, id DESC LIMIT :n"), {"s": since, "n": limit}).all()
    n = 0
    for (profit,) in rows:
        if Decimal(profit) > 0:
            n += 1
        else:
            break
    return n


def identical_recent_or_in_flight(conn: Connection, intent_id: str, symbol: str, contract_type: str, account_id: str,
                                  window_s: int, now: dt.datetime) -> bool:
    row = conn.execute(text(
        "SELECT 1 FROM order_intents WHERE id <> :id AND symbol = :s AND contract_type = :c AND account_id = :a AND ("
        f"state IN ({_EXPOSURE_SQL}) OR state IN ('INTENT_CREATED', 'RISK_APPROVED', 'PROPOSAL_REQUESTED') OR "
        f"(created_at > :since AND state NOT IN ({_IGNORED_FOR_DUPLICATES}))) LIMIT 1"),
        {"id": intent_id, "s": symbol, "c": contract_type, "a": account_id,
         "since": now - dt.timedelta(seconds=window_s)}).first()
    return row is not None


def start_of_day_balance(conn: Connection, since: dt.datetime) -> Decimal | None:
    """Last snapshot before the risk day began, else the first one after it. None when there is no snapshot at all."""
    before = conn.execute(text("SELECT balance FROM balance_snapshots WHERE ts < :s ORDER BY ts DESC, id DESC LIMIT 1"),
                          {"s": since}).scalar()
    if before is not None:
        return Decimal(before)
    after = conn.execute(text("SELECT balance FROM balance_snapshots WHERE ts >= :s ORDER BY ts ASC, id ASC LIMIT 1"),
                         {"s": since}).scalar()
    return None if after is None else Decimal(after)


def latest_balance(conn: Connection, account_id: str) -> Decimal | None:
    v = conn.execute(text("SELECT balance FROM balance_snapshots WHERE account_id = :a ORDER BY ts DESC, id DESC LIMIT 1"),
                     {"a": account_id}).scalar()
    return None if v is None else Decimal(v)
