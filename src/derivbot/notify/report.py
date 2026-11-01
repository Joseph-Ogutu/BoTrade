"""Daily report built from the database (HANDBOOK 6.1). Read only."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import Connection, text


@dataclass
class DailyReport:
    day_start: dt.datetime
    day_end: dt.datetime
    intents_by_state: dict[str, int] = field(default_factory=dict)
    settled: int = 0
    wins: int = 0
    losses: int = 0
    realized_pnl: Decimal = Decimal(0)
    risk_rejections: dict[str, int] = field(default_factory=dict)
    kill_switch_events: list[tuple[str, str, str]] = field(default_factory=list)
    reconciliations: dict[str, int] = field(default_factory=dict)
    open_exposure: int = 0
    balance_first: Decimal | None = None
    balance_last: Decimal | None = None


def build_daily_report(conn: Connection, start: dt.datetime, end: dt.datetime) -> DailyReport:
    p = {"s": start, "e": end}
    r = DailyReport(start, end)
    r.intents_by_state = {k: int(v) for k, v in conn.execute(text(
        "SELECT state, count(*) FROM order_intents WHERE created_at >= :s AND created_at < :e GROUP BY 1 ORDER BY 1"), p)}
    row = conn.execute(text(
        "SELECT count(*), count(*) FILTER (WHERE profit > 0), count(*) FILTER (WHERE profit < 0), COALESCE(sum(profit), 0) "
        "FROM orders WHERE state IN ('SETTLED','SOLD') AND settled_at >= :s AND settled_at < :e"), p).one()
    r.settled, r.wins, r.losses, r.realized_pnl = int(row[0]), int(row[1]), int(row[2]), Decimal(row[3])
    for (rules,) in conn.execute(text(
            "SELECT rule_results FROM risk_decisions WHERE approved = false AND decided_at >= :s AND decided_at < :e"), p):
        for rr in rules:
            if not rr["passed"]:
                r.risk_rejections[rr["name"]] = r.risk_rejections.get(rr["name"], 0) + 1
    r.kill_switch_events = [(a, s, (why or "")) for a, s, why in conn.execute(text(
        "SELECT action, source, reason FROM kill_switch_events WHERE ts >= :s AND ts < :e ORDER BY id"), p)]
    r.reconciliations = {k: int(v) for k, v in conn.execute(text(
        "SELECT status, count(*) FROM reconciliation_runs WHERE ts >= :s AND ts < :e GROUP BY 1"), p)}
    r.open_exposure = int(conn.execute(text("SELECT count(*) FROM order_intents WHERE state IN "
                                            "('BUY_SENT','BUY_UNKNOWN','OPEN','MANUAL_REVIEW')")).scalar_one())
    for col, order in (("balance_first", "ASC"), ("balance_last", "DESC")):
        v = conn.execute(text(f"SELECT balance FROM balance_snapshots WHERE ts >= :s AND ts < :e ORDER BY ts {order}, id {order} LIMIT 1"), p).scalar()
        setattr(r, col, None if v is None else Decimal(v))
    return r


def format_report(r: DailyReport) -> str:
    lines = [f"Daily report {r.day_start:%Y-%m-%d %H:%MZ} to {r.day_end:%Y-%m-%d %H:%MZ} (demo)"]
    lines.append("Intents: " + (", ".join(f"{k}={v}" for k, v in r.intents_by_state.items()) or "none"))
    wl = f"{r.wins}/{r.losses}" if r.settled else "n/a"
    lines.append(f"Settled: {r.settled} (wins/losses {wl}), realised P&L {r.realized_pnl}")
    if r.risk_rejections:
        lines.append("Risk rejections by rule: " + ", ".join(f"{k}={v}" for k, v in sorted(r.risk_rejections.items())))
    lines.append(f"Balance: {r.balance_first if r.balance_first is not None else 'unknown'} -> "
                 f"{r.balance_last if r.balance_last is not None else 'unknown'}")
    lines.append(f"Open or unresolved exposure: {r.open_exposure}")
    lines.append("Reconciliation: " + (", ".join(f"{k}={v}" for k, v in sorted(r.reconciliations.items())) or "no runs"))
    if r.kill_switch_events:
        lines.append("Kill switch: " + "; ".join(f"{a} by {s}: {why}" for a, s, why in r.kill_switch_events))
    return "\n".join(lines)


class DailyReporter:
    """Sends the report once per UTC day at the configured hour. The 'last sent' day lives in the KV store so a restart
    does not send it twice."""

    def __init__(self, notifier, engine, kv, hour_utc: int = 0, clock=lambda: dt.datetime.now(dt.UTC)) -> None:
        self.n, self.engine, self.kv, self.hour, self.clock = notifier, engine, kv, hour_utc, clock

    async def tick(self) -> bool:
        import asyncio

        now = self.clock()
        due = now.replace(hour=self.hour, minute=0, second=0, microsecond=0)
        if now < due:
            return False
        day = due.strftime("%Y-%m-%d")
        if self.kv.get("report:last_sent") == day:
            return False
        start = due - dt.timedelta(days=1)

        def build() -> str:
            with self.engine.connect() as c:
                return format_report(build_daily_report(c, start, due))

        body = await asyncio.to_thread(build)
        sent = await self.n.broadcast(body)
        if sent:
            self.kv.set("report:last_sent", day)
        return sent
