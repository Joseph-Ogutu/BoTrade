"""Reconciler (HANDBOOK 6.1, 7): compares local state with the exchange on startup, on every reconnect and
periodically. Discrepancies are recorded, alerted and PAUSE new trading through the kill switch. It never edits an order
to make the numbers agree. The only fixes are the two explicit operator methods at the bottom, each leaving an audit row
that names the operator.

Defined resolutions it does perform (they are edges of the HANDBOOK 7 state machine, not silent repairs):
- a BUY_SENT intent left behind by a crash becomes BUY_UNKNOWN (BUY_NO_REPLY), then follows the normal resolution;
- BUY_UNKNOWN intents are resolved against the snapshot (Executor.resolve_unknown).

Rate budget: portfolio and profit_table share 30 calls per minute (docs/limits); one run uses two, so the interval is
bounded below.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection

from derivbot.db import repo
from derivbot.db.audit import append_audit
from derivbot.deriv.errors import DerivApiError
from derivbot.deriv.session import ConnectionLost, DerivSession, RequestTimeout
from derivbot.execution.executor import Executor
from derivbot.execution.snapshot import ReconcileSnapshot, fetch_snapshot
from derivbot.execution.state_machine import Event, State
from derivbot.execution.tracker import Settlement

log = logging.getLogger(__name__)
MIN_INTERVAL_S = 20.0
TOLERANCE = Decimal("0.01")


class Reconciler:
    def __init__(self, executor: Executor, interval_s: float = 60.0,
                 clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.UTC),
                 stuck_buy_after_s: float = 15.0) -> None:
        if interval_s < MIN_INTERVAL_S:
            raise ValueError(f"reconcile interval must be at least {MIN_INTERVAL_S}s to respect the portfolio rate budget")
        self.ex, self.interval_s, self.clock, self.stuck_after = executor, interval_s, clock, stuck_buy_after_s
        self.last_ok: dt.datetime | None = None
        self.last_discrepancies: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    def age_s(self) -> float | None:
        return None if self.last_ok is None else (self.clock() - self.last_ok).total_seconds()

    # ------------------------------------------------------------------ hooks
    async def on_connected(self, session: DerivSession) -> None:
        trigger = "startup" if self.last_ok is None and not self.last_discrepancies else "reconnect"
        await self.run(trigger, session)
        await self.ex.d.tracker.resubscribe_all(session)

    async def run_periodic(self, halt: asyncio.Event) -> None:
        while not halt.is_set():
            try:
                await asyncio.wait_for(halt.wait(), timeout=self.interval_s)
                return
            except TimeoutError:
                pass
            try:
                await self.run("periodic")
            except Exception:  # noqa: BLE001
                log.exception("periodic reconciliation failed")

    # ------------------------------------------------------------------ the run
    async def run(self, trigger: str, session: DerivSession | None = None) -> list[dict[str, Any]]:
        async with self._lock:
            return await self._run(trigger, session)

    async def _record(self, trigger: str, local_open: int, remote_open: int, disc: list[dict[str, Any]],
                      resolved: dict[str, str]) -> None:
        status = "discrepancy" if disc else "clean"
        payload = disc + ([{"kind": "resolved_unknown_buys", "detail": resolved}] if resolved else [])

        def write(conn: Connection) -> None:
            repo.record_reconciliation(conn, trigger=trigger, local_open=local_open, remote_open=remote_open,
                                       status=status, discrepancies=payload)
            append_audit(conn, "reconciler", "RECONCILE", {"trigger": trigger, "status": status,
                                                           "discrepancies": [d["kind"] for d in disc],
                                                           "resolved": resolved})

        await self.ex._db(write)

    async def _run(self, trigger: str, session: DerivSession | None) -> list[dict[str, Any]]:
        session = session or self.ex.d.get_session()
        now = self.clock()
        if session is None or not session.connected:
            return await self._failed(trigger, "no connected session")
        try:
            if trigger in ("startup", "reconnect"):
                await self._recover_stuck_buys()
            snap = await fetch_snapshot(session, int(now.timestamp()), self.ex.d.request_timeout)
        except (DerivApiError, RequestTimeout, ConnectionLost, ValueError, KeyError) as exc:
            return await self._failed(trigger, f"snapshot failed: {type(exc).__name__}")   # UNVERIFIED-ERR: shapes
        resolved = {k: v.value for k, v in (await self.ex.resolve_unknown(snap)).items()}
        disc, local_open = await self._compare(snap)
        await self._record(trigger, local_open, len(snap.portfolio), disc, resolved)
        self.last_discrepancies = disc
        if disc:
            kinds = sorted({d["kind"] for d in disc})
            self.ex.d.kill_switch.engage("reconciler", f"reconciliation discrepancy: {', '.join(kinds)}")
            self.ex._alert("critical", f"reconciliation found {len(disc)} discrepancy(ies): {kinds}. New trading is paused; "
                                       "nothing was changed automatically.")
        else:
            self.last_ok = now
        if trigger in ("startup", "reconnect"):
            await self.resume_tracking()
        return disc

    async def _failed(self, trigger: str, why: str) -> list[dict[str, Any]]:
        disc = [{"kind": "snapshot_failed", "detail": {"why": why}}]
        await self._record(trigger, 0, 0, disc, {})
        self.ex._alert("warning", f"reconciliation could not run ({why}); trading stops by itself when the last good run is too old")
        return disc

    # ------------------------------------------------------------------ defined resolutions
    async def _recover_stuck_buys(self) -> None:
        rows = await self.ex._db(repo.stuck_buy_sent, self.stuck_after)
        for (iid,) in rows:
            iid = str(iid)
            if iid in self.ex.inflight:
                continue                                             # a live submit() is still waiting for its reply
            await self.ex._set(iid, State.BUY_SENT, Event.BUY_NO_REPLY, {"why": "process ended while the buy was in flight"})
            await self.ex._audit("BUY_UNKNOWN", {"why": "recovered after restart"}, iid)

    async def resume_tracking(self) -> None:
        """After a restart, OPEN orders have no running settlement task. Start one for each (no state change)."""
        rows = await self.ex._db(lambda c: [tuple(r) for r in repo.local_orders(c)])
        for iid, cid, st, *_ in rows:
            if st == State.OPEN.value and int(cid) not in self.ex.d.tracker.tracked:
                self.ex._start_settlement(str(iid), int(cid))

    # ------------------------------------------------------------------ comparison (read only)
    async def _compare(self, snap: ReconcileSnapshot) -> tuple[list[dict[str, Any]], int]:
        rows = await self.ex._db(lambda c: [tuple(r) for r in repo.local_orders(c)])
        local = {int(cid): (str(iid), st, bp, po, pr) for iid, cid, st, bp, po, pr in rows}
        remote_open = {c.contract_id: c for c in snap.portfolio}
        remote_settled = {t.contract_id: t for t in snap.settled if t.contract_id is not None}
        acked = {cid for cid in remote_open if self.ex.d.kv.get(f"reconcile:ack:{cid}") == "1"}
        disc: list[dict[str, Any]] = []
        for cid, c in remote_open.items():
            if cid not in local and cid not in acked:
                disc.append({"kind": "remote_open_missing_locally", "ref": cid,
                             "detail": {"symbol": c.underlying_symbol, "contract_type": c.contract_type,
                                        "buy_price": str(c.buy_price)}})
        for cid, (iid, st, bp, _po, pr) in local.items():
            if st == State.OPEN.value:
                if cid in remote_open:
                    rbp = remote_open[cid].buy_price
                    if rbp is not None and bp is not None and abs(Decimal(bp) - rbp) > TOLERANCE:
                        disc.append({"kind": "buy_price_mismatch", "ref": cid, "detail": {"local": str(bp), "remote": str(rbp)}})
                elif cid in remote_settled:
                    t = remote_settled[cid]
                    profit = (t.sell_price if t.sell_price is not None else Decimal(0)) - t.buy_price
                    disc.append({"kind": "settled_while_untracked", "ref": cid, "intent_id": iid,
                                 "detail": {"profit_from_profit_table": str(profit)}})
                else:
                    disc.append({"kind": "vanished", "ref": cid, "intent_id": iid,
                                 "detail": {"note": "neither in portfolio nor in the recent profit_table (limit 50, UNVERIFIED paging)"}})
            elif st in (State.SETTLED.value, State.SOLD.value) and cid in remote_settled and pr is not None:
                t = remote_settled[cid]
                remote_profit = (t.sell_price if t.sell_price is not None else Decimal(0)) - t.buy_price
                if abs(Decimal(pr) - remote_profit) > TOLERANCE:
                    disc.append({"kind": "profit_mismatch", "ref": cid, "intent_id": iid,
                                 "detail": {"local": str(pr), "remote": str(remote_profit)}})
        return disc, sum(1 for v in local.values() if v[1] == State.OPEN.value)

    # ------------------------------------------------------------------ explicit operator actions (audited)
    async def resolve_settled_while_untracked(self, intent_id: str, operator: str, note: str) -> Decimal:
        """Record a settlement found only in profit_table. The operator names themselves; nothing runs this on a timer."""
        if not operator.strip() or len(note.strip()) < 3:
            raise ValueError("an operator name and a note are required")
        disc = next((d for d in self.last_discrepancies if d.get("intent_id") == intent_id and d["kind"] == "settled_while_untracked"), None)
        if disc is None:
            raise LookupError("no such discrepancy in the last run")
        profit = Decimal(disc["detail"]["profit_from_profit_table"])
        res = await self.ex.finalize(intent_id, Settlement(int(disc["ref"]), profit, None))
        await self.ex._audit("RECONCILE", {"action": "operator_resolved_settled_while_untracked", "operator": operator,
                                           "note": note, "profit": str(profit)}, intent_id)
        self.last_discrepancies = [d for d in self.last_discrepancies if d is not disc]
        return res.profit or profit

    async def acknowledge_foreign(self, contract_id: int, operator: str, note: str, ttl_s: float = 86400.0) -> None:
        """Accept that an open contract we did not place exists (for example a manual trade). Stops it re-pausing trading."""
        if not operator.strip() or len(note.strip()) < 3:
            raise ValueError("an operator name and a note are required")
        self.ex.d.kv.set(f"reconcile:ack:{contract_id}", "1", ttl_s)
        await self.ex._audit("RECONCILE", {"action": "operator_acknowledged_foreign_contract", "operator": operator,
                                           "note": note, "contract_id": contract_id})
