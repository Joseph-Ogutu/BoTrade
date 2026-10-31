"""Executor (HANDBOOK 6.1, 7): the only component that sends buy. One intent, one in-flight buy, never a retry.

Lifecycle: INTENT_CREATED -> risk (recorded) -> proposal -> rule 8 -> state BUY_SENT is COMMITTED before the buy message
is written -> buy by proposal id with a price cap -> OPEN (contract id recorded) -> settlement via
proposal_open_contract. Anything short of a confirmed buy reply becomes BUY_UNKNOWN and is resolved by looking at the
exchange, never by buying again.

UNVERIFIED (demo behaviour): the shape of buy errors, whether a lost reply means a placed contract, OTP lifetime,
whether subscriptions survive a reconnect, settlement field names, clock agreement between us and Deriv. Each assumption
is a tagged comment where it is used.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import Any

from sqlalchemy import Connection, Engine

from derivbot.common.redaction import redact_text
from derivbot.db import repo
from derivbot.db.audit import append_audit
from derivbot.deriv import messages as m
from derivbot.deriv.errors import DerivApiError
from derivbot.deriv.session import ConnectionLost, DerivSession, RequestTimeout
from derivbot.execution.snapshot import ReconcileSnapshot, fetch_snapshot
from derivbot.execution.state_machine import Event, State
from derivbot.execution.tracker import ContractTracker, Settlement
from derivbot.killswitch.switch import KillSwitch
from derivbot.kv.store import KV, IntentLock
from derivbot.risk import state as risk_state
from derivbot.risk.engine import IntentRequest, RiskContext, RiskEngine
from derivbot.risk.persist import record_decision
from derivbot.risk.stake import compute_stake

log = logging.getLogger(__name__)
PLACEHOLDER_STAKE = Decimal("0.01")       # order_intents.stake must be positive even when no stake could be computed


@dataclass(frozen=True)
class ConnInfo:
    endpoint_mode: str | None        # "demo" / "real" / None (not determined)
    account_is_demo: bool | None
    balance: Decimal | None
    currency: str | None = None
    loginid: str | None = None


@dataclass(frozen=True)
class ExecutionResult:
    intent_id: str | None
    state: State | None
    reason: str
    contract_id: int | None = None
    profit: Decimal | None = None


@dataclass
class ExecutorDeps:
    engine: Engine
    risk: RiskEngine
    config_version_id: int
    kill_switch: KillSwitch
    kv: KV
    tracker: ContractTracker
    get_session: Callable[[], DerivSession | None]
    get_conn_info: Callable[[], ConnInfo]
    last_tick_age: Callable[[], float | None]
    last_reconcile_age: Callable[[], float | None]
    api_min_stake: Callable[[str], Decimal | None]        # UNVERIFIED for the demo account
    account_id: str
    clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc)
    on_alert: Callable[[str, str], None] = lambda level, text: None
    configured_mode: str = "demo"
    request_timeout: float = 10.0
    buy_unknown_window_s: float = 60.0        # how long after BUY_SENT "not found" may be believed
    clock_skew_tolerance_s: int = 10          # UNVERIFIED: how far apart our clock and Deriv's purchase_time may be


def _cents_down(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_DOWN)


class Executor:
    def __init__(self, deps: ExecutorDeps) -> None:
        self.d = deps
        self._tasks: set[asyncio.Task] = set()
        self._lock = IntentLock(deps.kv)

    # ------------------------------------------------------------------ small helpers
    async def _db(self, fn: Callable[..., Any], *args: Any, **kw: Any) -> Any:
        def run() -> Any:
            with self.d.engine.begin() as conn:
                return fn(conn, *args, **kw)

        return await asyncio.to_thread(run)

    def _alert(self, level: str, text: str) -> None:
        try:
            self.d.on_alert(level, redact_text(text))
        except Exception:  # noqa: BLE001 - an alert transport problem must never stop trading logic
            log.exception("alert transport failed")

    async def _audit(self, event_type: str, payload: dict[str, Any], ref_id: str | None = None, req_id: int | None = None) -> None:
        await self._db(append_audit, "executor", event_type, payload, ref_id, req_id)

    async def _set(self, intent_id: str, expected: State, event: Event, payload: dict[str, Any] | None = None) -> State:
        return await self._db(repo.transition, intent_id, expected, event, payload)

    # ------------------------------------------------------------------ the public entry point
    async def submit(self, req: IntentRequest, duration: int = 5, duration_unit: str = "t",
                     wait_settlement: bool = True, settlement_timeout: float = 120.0) -> ExecutionResult:
        """Run one intent end to end. An unexpected exception engages the kill switch and alerts (HANDBOOK 10.5)."""
        token = self._lock.acquire(req.idempotency_key)
        if token is None:
            return ExecutionResult(None, None, "duplicate: another submit holds this idempotency key")
        try:
            return await self._submit(req, duration, duration_unit, wait_settlement, settlement_timeout)
        except Exception as exc:  # noqa: BLE001
            self.d.kill_switch.engage("executor_exception", f"{type(exc).__name__} in submit")
            self._alert("critical", f"executor exception, kill switch engaged: {type(exc).__name__}")
            log.exception("executor exception")
            return ExecutionResult(None, None, f"error: {type(exc).__name__}")
        finally:
            self._lock.release(req.idempotency_key, token)

    async def _build_context(self, req: IntentRequest, intent_id: str, info: ConnInfo, balance: Decimal | None,
                             api_min: Decimal | None) -> RiskContext:
        cfg, now = self.d.risk.cfg, self.d.clock()
        start = risk_state.day_start(now, cfg.daily_reset_utc_hour)

        def read(conn: Connection) -> dict[str, Any]:
            return {
                "open_positions": risk_state.open_positions(conn),
                "pnl": risk_state.realized_pnl_since(conn, start),
                "start_balance": risk_state.start_of_day_balance(conn, start),
                "losses": risk_state.consecutive_losses(conn),
                "trades": risk_state.trades_in_last_hour(conn, now),
                "dup": risk_state.identical_recent_or_in_flight(conn, intent_id, req.symbol, req.contract_type,
                                                                  req.account_id, cfg.recent_duplicate_window_s, now),
            }

        s = await self._db(read)
        session = self.d.get_session()
        return RiskContext(
            intent=req, configured_mode=self.d.configured_mode, endpoint_mode=info.endpoint_mode,
            account_is_demo=info.account_is_demo, kill_switch_engaged=self.d.kill_switch.is_engaged(),
            connection_healthy=session is not None and session.connected,
            last_reconcile_age_s=self.d.last_reconcile_age(), last_tick_age_s=self.d.last_tick_age(),
            balance=balance, api_min_stake=api_min, open_positions=s["open_positions"], realized_pnl_today=s["pnl"],
            start_of_day_balance=s["start_balance"], consecutive_losses=s["losses"], trades_last_hour=s["trades"],
            idempotency_key_used=False, identical_recent_or_in_flight=s["dup"],
        )

    async def _submit(self, req: IntentRequest, duration: int, unit: str, wait: bool, settle_timeout: float) -> ExecutionResult:
        cfg = self.d.risk.cfg
        info = self.d.get_conn_info()
        balance, api_min = info.balance, self.d.api_min_stake(req.symbol)
        pre = compute_stake(balance, cfg.risk_per_trade_pct, cfg.max_stake_abs, api_min, cfg.min_stake_abs)
        try:
            intent_id = await self._db(repo.create_intent, idempotency_key=req.idempotency_key, mode=req.mode,
                                       account_id=req.account_id, symbol=req.symbol, contract_type=req.contract_type,
                                       stake=pre.stake or PLACEHOLDER_STAKE, duration=duration, duration_unit=unit)
        except repo.DuplicateIntent:
            return ExecutionResult(None, None, "duplicate: idempotency key already stored")
        ctx = await self._build_context(req, intent_id, info, balance, api_min)
        decision = self.d.risk.evaluate(ctx)
        await self._db(record_decision, intent_id, decision, self.d.config_version_id)
        if not decision.approved or decision.stake is None:
            self._alert("info", f"intent rejected by risk: {decision.failed}")
            return ExecutionResult(intent_id, State.RISK_REJECTED, f"risk rejected: {decision.failed}")
        stake = decision.stake

        # ---- proposal
        await self._set(intent_id, State.RISK_APPROVED, Event.PROPOSAL_REQUEST)
        session = self.d.get_session()
        if session is None or not session.connected:
            return await self._proposal_failed(intent_id, "no connected session", None)
        sub_id: str | None = None
        try:
            raw = await session.request(
                m.req_proposal(req.symbol, req.contract_type, stake, duration, unit,
                               info.currency or "USD", session.next_req_id()), self.d.request_timeout)
            msg = m.parse_message(raw)
            if not isinstance(msg, m.ProposalMsg):
                raise ValueError("unexpected reply to proposal")
            sub_id = msg.subscription_id
        except (DerivApiError, RequestTimeout, ConnectionLost, ValueError, KeyError) as exc:
            return await self._proposal_failed(intent_id, f"proposal failed: {type(exc).__name__}", None)
        prop = msg.proposal
        ok, results = self.d.risk.check_proposal(stake, prop.ask_price, prop.payout)
        await self._audit("RISK_PROPOSAL", {"approved": ok, "rules": [r.as_dict() for r in results]}, intent_id)
        if not ok:
            return await self._proposal_failed(intent_id, "proposal not acceptable (rule 8)", sub_id, session)
        # last checks immediately before money moves: kill switch and demo-ness are re-read, not remembered
        if self.d.kill_switch.is_engaged():
            return await self._proposal_failed(intent_id, "kill switch engaged before buy", sub_id, session)
        info2 = self.d.get_conn_info()
        if self.d.configured_mode != "demo" or info2.endpoint_mode != "demo" or info2.account_is_demo is not True:
            return await self._proposal_failed(intent_id, "demo mode not confirmed before buy", sub_id, session)
        if not session.connected:
            return await self._proposal_failed(intent_id, "connection lost before buy", sub_id, session)

        # ---- buy: commit BUY_SENT first, then write the message
        cap = _cents_down(stake * (1 + cfg.max_proposal_price_slippage))
        await self._set(intent_id, State.PROPOSAL_REQUESTED, Event.SEND_BUY,
                        {"proposal_id": prop.id, "ask_price": str(prop.ask_price), "price_cap": str(cap)})
        await self._audit("BUY_SENT", {"price_cap": str(cap), "stake": str(stake)}, intent_id)
        confirmed: m.BuyResult | None = None
        why = ""
        try:
            # buy by proposal id with a price cap; a parameter buy is refused by req_buy. No retry, whatever happens.
            buy_raw = await session.request(m.req_buy(prop.id, cap, session.next_req_id()), self.d.request_timeout)
            bmsg = m.parse_message(buy_raw)       # UNVERIFIED-ERR: error shape for buy on the demo endpoint
            if isinstance(bmsg, m.BuyMsg):
                confirmed = bmsg.buy
            else:
                why = "unexpected reply type to buy"
        except DerivApiError as exc:
            why = f"buy answered with an error ({exc.code})"
        except RequestTimeout:
            why = "buy reply timed out"
        except ConnectionLost:
            why = "connection lost during buy"
        except (ValueError, KeyError):
            why = "buy reply could not be parsed"
        finally:
            await self._forget(session, sub_id)

        if confirmed is None:
            return await self._buy_unconfirmed(intent_id, why)
        return await self._buy_confirmed(intent_id, req, confirmed, info, wait, settle_timeout)

    async def _forget(self, session: DerivSession, sub_id: str | None) -> None:
        if not sub_id:
            return
        try:
            await session.send(m.req_forget(sub_id, session.next_req_id()))
        except (ConnectionLost, ValueError):
            pass

    async def _proposal_failed(self, intent_id: str, reason: str, sub_id: str | None,
                               session: DerivSession | None = None) -> ExecutionResult:
        if session is not None:
            await self._forget(session, sub_id)
        await self._set(intent_id, State.PROPOSAL_REQUESTED, Event.PROPOSAL_BAD, {"reason": reason})
        self._alert("info", f"proposal rejected: {reason}")
        return ExecutionResult(intent_id, State.PROPOSAL_REJECTED, reason)

    async def _buy_unconfirmed(self, intent_id: str, why: str) -> ExecutionResult:
        await self._set(intent_id, State.BUY_SENT, Event.BUY_NO_REPLY, {"why": why})
        await self._audit("BUY_UNKNOWN", {"why": why}, intent_id)
        self._alert("warning", f"buy not confirmed ({why}); resolving by reconciliation, never by retrying")
        resolved = await self._try_resolve_now()
        state = resolved.get(intent_id, State.BUY_UNKNOWN)
        return ExecutionResult(intent_id, state, f"buy not confirmed: {why}")

    async def _buy_confirmed(self, intent_id: str, req: IntentRequest, buy: m.BuyResult, info: ConnInfo,
                             wait: bool, settle_timeout: float) -> ExecutionResult:
        def write(conn: Connection) -> None:
            repo.transition(conn, intent_id, State.BUY_SENT, Event.BUY_CONFIRMED, {"contract_id": buy.contract_id})
            repo.open_order(conn, intent_id, contract_id=buy.contract_id, transaction_id=buy.transaction_id,
                            buy_price=buy.buy_price, payout=buy.payout, mode=req.mode, account_id=req.account_id)
            repo.record_balance(conn, mode=req.mode, account_id=req.account_id, balance=buy.balance_after,
                                currency=info.currency or "USD")
            append_audit(conn, "executor", "BUY_CONFIRMED", {"contract_id": buy.contract_id, "buy_price": str(buy.buy_price)},
                         ref_id=intent_id)

        await self._db(write)
        task = self._start_settlement(intent_id, buy.contract_id)
        if not wait:
            return ExecutionResult(intent_id, State.OPEN, "open", buy.contract_id)
        try:
            return await asyncio.wait_for(asyncio.shield(task), settle_timeout)
        except asyncio.TimeoutError:
            return ExecutionResult(intent_id, State.OPEN, "open: settlement not seen yet", buy.contract_id)

    # ------------------------------------------------------------------ settlement
    def _start_settlement(self, intent_id: str, contract_id: int) -> asyncio.Task:
        task = asyncio.create_task(self._settle_when_ready(intent_id, contract_id), name=f"settle-{contract_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _settle_when_ready(self, intent_id: str, contract_id: int) -> ExecutionResult:
        session = self.d.get_session()
        try:
            fut = self.d.tracker.register(contract_id)
            if session is not None and session.connected:
                await self.d.tracker.subscribe(session, contract_id)
        except ConnectionLost:
            pass              # the tracker resubscribes after the next reconnect
        s: Settlement = await fut
        return await self.finalize(intent_id, s)

    async def finalize(self, intent_id: str, s: Settlement) -> ExecutionResult:
        def write(conn: Connection) -> None:
            repo.transition(conn, intent_id, State.OPEN, Event.EXPIRY, {"profit": str(s.profit)})
            repo.settle_order(conn, intent_id, profit=s.profit)
            append_audit(conn, "executor", "SETTLED", {"contract_id": s.contract_id, "profit": str(s.profit)}, ref_id=intent_id)

        try:
            await self._db(write)
        except repo.StaleState:
            return ExecutionResult(intent_id, None, "already settled", s.contract_id, s.profit)   # a duplicate message
        self.d.tracker.forget(s.contract_id)
        return ExecutionResult(intent_id, State.SETTLED, "settled", s.contract_id, s.profit)

    async def shutdown(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # ------------------------------------------------------------------ BUY_UNKNOWN resolution
    async def _try_resolve_now(self) -> dict[str, State]:
        session = self.d.get_session()
        if session is None or not session.connected:
            return {}
        try:
            snap = await fetch_snapshot(session, int(self.d.clock().timestamp()), self.d.request_timeout)
        except (DerivApiError, RequestTimeout, ConnectionLost, ValueError, KeyError):
            return {}
        return await self.resolve_unknown(snap)

    def _matches(self, row: Any, sent_epoch: int, snap: ReconcileSnapshot) -> list[tuple[int, Decimal, Decimal, Decimal | None]]:
        """Candidate contracts for an unknown buy: (contract_id, buy_price, payout, sell_price or None if still open)."""
        slip = self.d.risk.cfg.max_proposal_price_slippage
        stake = Decimal(row.stake)
        lo, hi = stake * (1 - slip) - Decimal("0.01"), stake * (1 + slip) + Decimal("0.01")
        tol = self.d.clock_skew_tolerance_s
        out = []
        for c in snap.portfolio:
            if (c.underlying_symbol == row.symbol and c.contract_type == row.contract_type and c.contract_id is not None
                    and c.buy_price is not None and lo <= c.buy_price <= hi
                    and c.purchase_time is not None and sent_epoch - tol <= c.purchase_time <= snap.fetched_at_epoch + tol):
                out.append((c.contract_id, c.buy_price, c.payout or Decimal(0), None))
        for t in snap.settled:
            if (t.underlying_symbol == row.symbol and t.contract_type == row.contract_type and t.contract_id is not None
                    and lo <= t.buy_price <= hi and t.purchase_time is not None
                    and sent_epoch - tol <= t.purchase_time <= snap.fetched_at_epoch + tol):
                out.append((t.contract_id, t.buy_price, t.payout or Decimal(0), t.sell_price if t.sell_price is not None else Decimal(0)))
        seen, uniq = set(), []
        for item in out:
            if item[0] not in seen:
                seen.add(item[0])
                uniq.append(item)
        return uniq

    async def resolve_unknown(self, snap: ReconcileSnapshot) -> dict[str, State]:
        """Resolve every BUY_UNKNOWN intent against a remote snapshot. Never buys. Returns intent_id -> new state."""
        rows = await self._db(repo.intents_in_state, State.BUY_UNKNOWN)
        if not rows:
            return {}
        now = self.d.clock()

        def sent_times(conn: Connection) -> dict[str, dt.datetime]:
            from sqlalchemy import text
            q = text("SELECT intent_id, max(ts) FROM order_events WHERE to_state = 'BUY_SENT' AND intent_id = ANY(:ids) GROUP BY 1")
            return {str(i): ts for i, ts in conn.execute(q, {"ids": [r.id for r in rows]})}

        sent = await self._db(sent_times)
        cands: dict[str, list[tuple[int, Decimal, Decimal, Decimal | None]]] = {}
        for r in rows:
            sent_at = sent.get(str(r.id), r.created_at)
            cands[str(r.id)] = self._matches(r, int(sent_at.timestamp()), snap)
        # Two unknown intents that could own the same contract cannot be told apart: both go to manual review.
        owners: dict[int, list[str]] = {}
        for iid, lst in cands.items():
            for c in lst:
                owners.setdefault(c[0], []).append(iid)
        contested = {iid for ids in owners.values() if len(ids) > 1 for iid in ids}
        result: dict[str, State] = {}
        for r in rows:
            iid = str(r.id)
            lst = [c for c in cands[iid]]
            attributed = []
            for c in lst:
                row = await self._db(repo.order_for_contract, c[0])
                if row is not None:
                    attributed.append(c[0])
            lst = [c for c in lst if c[0] not in attributed]
            if iid in contested or len(lst) > 1:
                result[iid] = await self._to_manual_review(iid, f"{len(lst)} matching contracts / contested")
            elif len(lst) == 1:
                result[iid] = await self._adopt(r, lst[0])
            else:
                waited = (now - sent.get(iid, r.created_at)).total_seconds()
                if waited >= self.d.buy_unknown_window_s:
                    await self._set(iid, State.BUY_UNKNOWN, Event.NOT_FOUND_AFTER_WINDOW, {"waited_s": waited})
                    await self._audit("BUY_UNKNOWN", {"resolved": "NOT_PLACED", "waited_s": waited}, iid)
                    result[iid] = State.NOT_PLACED
        return result

    async def _to_manual_review(self, iid: str, why: str) -> State:
        await self._set(iid, State.BUY_UNKNOWN, Event.AMBIGUOUS, {"why": why})
        await self._audit("BUY_UNKNOWN", {"resolved": "MANUAL_REVIEW", "why": why}, iid)
        self.d.kill_switch.engage("reconciler", f"ambiguous BUY_UNKNOWN {iid}")
        self._alert("critical", f"BUY_UNKNOWN {iid} is ambiguous ({why}): MANUAL_REVIEW, new trading paused")
        return State.MANUAL_REVIEW

    async def _adopt(self, row: Any, cand: tuple[int, Decimal, Decimal, Decimal | None]) -> State:
        contract_id, buy_price, payout, sell_price = cand
        iid = str(row.id)

        def write(conn: Connection) -> None:
            repo.transition(conn, iid, State.BUY_UNKNOWN, Event.FOUND_IN_PORTFOLIO, {"contract_id": contract_id})
            repo.open_order(conn, iid, contract_id=contract_id, transaction_id=None, buy_price=buy_price, payout=payout,
                            mode="demo", account_id=row.account_id)
            append_audit(conn, "executor", "BUY_UNKNOWN", {"resolved": "OPEN", "contract_id": contract_id}, ref_id=iid)

        await self._db(write)
        if sell_price is not None:      # it had already settled: record the result from profit_table, flagged in audit
            profit = sell_price - buy_price
            settled = await self.finalize(iid, Settlement(contract_id, profit, sell_price))
            await self._audit("SETTLED", {"source": "profit_table", "contract_id": contract_id, "profit": str(profit)}, iid)
            return settled.state or State.SETTLED
        self._start_settlement(iid, contract_id)
        return State.OPEN
