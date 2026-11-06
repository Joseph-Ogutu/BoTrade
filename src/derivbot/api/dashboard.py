"""Read-only data for the dashboard. Every query is a plain SELECT with bound parameters; nothing here can write.

What is deliberately NOT returned: account ids, audit payloads, contract ids, tokens, URLs. The page shows money, states,
counts and short reasons only.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, text

TRADE_LIMIT = 15
EVENT_LIMIT = 12
CURVE_POINTS = 120


def _num(v: Any) -> float | None:
    return None if v is None else float(Decimal(str(v)))


def _iso(v: dt.datetime | None) -> str | None:
    if v is None:
        return None
    return v.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_summary(conn: Connection, now: dt.datetime, mode: str = "demo") -> dict[str, Any]:
    day_start = now.astimezone(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    p = {"s": day_start, "m": mode}                         # every number below is for the CURRENT mode only: demo and real never mix

    bal = conn.execute(text("SELECT balance, currency, ts FROM balance_snapshots WHERE mode = :m ORDER BY ts DESC, id DESC LIMIT 1"), p).first()
    first = conn.execute(text("SELECT balance FROM balance_snapshots WHERE ts >= :s AND mode = :m ORDER BY ts, id LIMIT 1"), p).scalar()
    curve = [_num(r[0]) for r in conn.execute(text(
        "SELECT balance FROM (SELECT balance, ts, id FROM balance_snapshots WHERE mode = :m ORDER BY ts DESC, id DESC LIMIT :n) t "
        "ORDER BY ts, id"), {"n": CURVE_POINTS, "m": mode})]

    row = conn.execute(text(
        "SELECT count(*), count(*) FILTER (WHERE profit > 0), count(*) FILTER (WHERE profit < 0), COALESCE(sum(profit), 0) "
        "FROM orders WHERE settled_at >= :s AND profit IS NOT NULL AND mode = :m"), p).one()
    settled, wins, losses, pnl = int(row[0]), int(row[1]), int(row[2]), Decimal(str(row[3]))
    exposure = int(conn.execute(text(
        "SELECT count(*) FROM order_intents WHERE mode = :m AND state IN ('BUY_SENT','BUY_UNKNOWN','OPEN','MANUAL_REVIEW')"), p).scalar() or 0)
    states = {r[0]: int(r[1]) for r in conn.execute(text(
        "SELECT state, count(*) FROM order_intents WHERE created_at >= :s AND mode = :m GROUP BY 1 ORDER BY 1"), p)}

    trades = [{"time": _iso(r[0]), "symbol": r[1], "type": r[2], "stake": _num(r[3]), "state": r[4], "profit": _num(r[5])}
              for r in conn.execute(text(
                  "SELECT i.created_at, i.symbol, i.contract_type, i.stake, i.state, o.profit FROM order_intents i "
                  "LEFT JOIN orders o ON o.intent_id = i.id WHERE i.mode = :m ORDER BY i.created_at DESC LIMIT :n"), {"n": TRADE_LIMIT, "m": mode})]

    rejections: dict[str, int] = {}
    for (rule_results,) in conn.execute(text(
            "SELECT r.rule_results FROM risk_decisions r JOIN order_intents i ON i.id = r.intent_id "
            "WHERE r.approved = false AND r.decided_at >= :s AND i.mode = :m"), p):
        for rr in (rule_results if isinstance(rule_results, list) else []):
            if isinstance(rr, dict) and rr.get("passed") is False and isinstance(rr.get("name"), str):
                rejections[rr["name"]] = rejections.get(rr["name"], 0) + 1

    recon = conn.execute(text("SELECT ts, status, trigger FROM reconciliation_runs ORDER BY id DESC LIMIT 1")).first()
    events = [{"time": _iso(r[0]), "kind": "kill_switch", "text": f"{r[1]} by {r[2]}" + (f": {r[3]}" if r[3] else "")}
              for r in conn.execute(text("SELECT ts, action, source, reason FROM kill_switch_events ORDER BY id DESC LIMIT :n"),
                                    {"n": EVENT_LIMIT})]

    return {
        "as_of": _iso(now),
        "balance": None if bal is None else {"value": _num(bal[0]), "currency": bal[1], "at": _iso(bal[2])},
        "balance_day_start": _num(first),
        "equity_curve": curve,
        "today": {"settled": settled, "wins": wins, "losses": losses, "pnl": float(pnl),
                  "win_rate": (wins / settled) if settled else None, "intent_states": states},
        "open_exposure": exposure,
        "recent_trades": trades,
        "risk_rejections_today": dict(sorted(rejections.items(), key=lambda kv: -kv[1])),
        "last_reconciliation": None if recon is None else {"at": _iso(recon[0]), "status": recon[1], "trigger": recon[2]},
        "recent_events": events,
    }


PAGE_SIZES = (10, 15, 25, 50)


def trades_page(conn: Connection, page: int, size: int, mode: str = "demo") -> dict[str, Any]:
    """One page of trades, newest first. The size is clamped to a known set and the page to the existing range, so a hostile
    or stale request can neither ask for the whole table nor fall off the end."""
    size = size if size in PAGE_SIZES else TRADE_LIMIT
    total = int(conn.execute(text("SELECT count(*) FROM order_intents WHERE mode = :m"), {"m": mode}).scalar() or 0)
    pages = max(1, -(-total // size))
    page = min(max(1, page), pages)
    rows = conn.execute(text(
        "SELECT i.created_at, i.symbol, i.contract_type, i.stake, i.state, o.profit FROM order_intents i "
        "LEFT JOIN orders o ON o.intent_id = i.id WHERE i.mode = :m ORDER BY i.created_at DESC, i.id DESC LIMIT :n OFFSET :o"),
        {"n": size, "o": (page - 1) * size, "m": mode}).all()
    return {"page": page, "size": size, "total": total, "pages": pages,
            "items": [{"time": _iso(r[0]), "symbol": r[1], "type": r[2], "stake": _num(r[3]), "state": r[4], "profit": _num(r[5])}
                      for r in rows]}
