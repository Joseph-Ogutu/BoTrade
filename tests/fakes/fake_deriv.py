"""Fake Deriv WebSocket server with failure injection.

Speaks just enough of the protocol for the adapter tests: ticks, ping, balance,
proposal, buy (by proposal id), portfolio, proposal_open_contract, forget.
Error codes used for injected failures are INVENTED for testing. The real codes
are UNVERIFIED (see docs/api-notes.md); tests must not hard-code them as facts.

Contracts live on the server, not on the connection, so a purchase made on a
connection that was then dropped is still visible to portfolio on reconnect.
This reproduces the dangerous BUY_UNKNOWN case from HANDBOOK 7.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass, field
from typing import Any

import websockets
from websockets.http import Headers


@dataclass
class Fault:
    """One injected failure. Applies to the (skip + 1)th matching message, once."""

    msg_type: str
    action: str  # drop_before | drop_after | delay | duplicate | error | hold
    skip: int = 0
    seconds: float = 0.0
    # UNVERIFIED-ERR: "FakeError" and every other Fake* code below is INVENTED for tests, not a real Deriv code.
    code: str = "FakeError"
    message: str = "injected failure"
    _seen: int = field(default=0, repr=False)


@dataclass
class Contract:
    contract_id: int
    proposal_id: str
    buy_price: float
    payout: float
    contract_type: str
    symbol: str
    opened_at: float
    settled: bool = False
    profit: float = 0.0


class FakeDeriv:
    def __init__(self, r: float = 0.95, tick_interval: float = 0.01, settle_after: float = 0.05,
                 symbol: str = "1HZ100V", balance: float = 10_000.0, win: bool = True) -> None:
        self.r = r
        self.tick_interval = tick_interval
        self.settle_after = settle_after
        self.symbol = symbol
        self.balance = balance
        self.win = win
        self.faults: list[Fault] = []
        self.received: list[dict[str, Any]] = []
        self.proposals: dict[str, dict[str, Any]] = {}
        self.bought: set[str] = set()
        self.contracts: dict[int, Contract] = {}
        self.connections = 0
        # UNVERIFIED-ERR: that Deriv rejects bad auth at the WebSocket handshake with an HTTP status (assumed 401).
        self.reject_handshake_status: int | None = None
        self.drop_ticks_after: int | None = None
        self.closed_symbols: set[str] = set()
        self._server: websockets.WebSocketServer | None = None
        self._next_contract = 1000
        self._epoch = 1_700_000_000
        self._held: list[tuple[Any, str]] = []

    # ------------------------------------------------------------ lifecycle
    async def start(self) -> "FakeDeriv":
        async def process_request(path, request_headers):
            if self.reject_handshake_status:
                return self.reject_handshake_status, Headers(), b"unauthorized"
            return None

        self._server = await websockets.serve(self._handler, "127.0.0.1", 0, process_request=process_request)
        return self

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def __aenter__(self) -> "FakeDeriv":
        return await self.start()

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    @property
    def url(self) -> str:
        port = self._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]
        return f"ws://127.0.0.1:{port}/trading/v1/options/ws/public"

    def inject(self, fault: Fault) -> None:
        self.faults.append(fault)

    # ------------------------------------------------------------ protocol
    def _take_fault(self, msg_type: str) -> Fault | None:
        for f in self.faults:
            if f.msg_type == msg_type:
                if f._seen >= f.skip:
                    self.faults.remove(f)
                    return f
                f._seen += 1
        return None

    async def _handler(self, ws) -> None:
        self.connections += 1
        tasks: list[asyncio.Task] = []
        try:
            async for raw in ws:
                req = json.loads(raw)
                self.received.append(req)
                msg_type = next((k for k in _TYPES if k in req), "unknown")
                fault = self._take_fault(msg_type)
                if fault and fault.action == "drop_before":
                    await ws.close()
                    return
                if fault and fault.action == "delay":
                    await asyncio.sleep(fault.seconds)
                if fault and fault.action == "error":
                    await ws.send(json.dumps(self._envelope(req, msg_type, error={
                        "code": fault.code, "message": fault.message})))
                    continue
                replies = await self._process(ws, req, msg_type, tasks)
                if fault and fault.action == "drop_after":
                    await ws.close()
                    return
                if fault and fault.action == "hold":
                    self._held.extend((ws, json.dumps(r)) for r in replies)
                    continue
                for r in replies:
                    await ws.send(json.dumps(r))
                    if fault and fault.action == "duplicate":
                        await ws.send(json.dumps(r))
                while self._held and not (fault and fault.action == "hold"):
                    held_ws, held_msg = self._held.pop(0)
                    await held_ws.send(held_msg)
        except websockets.ConnectionClosed:
            pass
        finally:
            for t in tasks:
                t.cancel()

    def _envelope(self, req: dict, msg_type: str, **extra: Any) -> dict[str, Any]:
        out = {"echo_req": req, "msg_type": msg_type, **extra}
        if "req_id" in req:
            out["req_id"] = req["req_id"]
        return out

    async def _process(self, ws, req: dict, msg_type: str, tasks: list[asyncio.Task]) -> list[dict]:
        if msg_type == "ping":
            return [self._envelope(req, "ping", ping="pong")]
        if msg_type == "balance":
            return [self._envelope(req, "balance", balance={
                "balance": self.balance, "currency": "USD", "loginid": "VRTC0000001", "id": _sid()},
                subscription={"id": _sid()})]
        if msg_type == "ticks":
            if req["ticks"] in self.closed_symbols:
                # OBSERVED-ERR: code MarketIsClosed, msg_type ticks, seen live on the public endpoint 2026-11-01.
                # Message text is paraphrased.
                return [self._envelope(req, "ticks", error={
                    "code": "MarketIsClosed", "message": "This market is presently closed."})]
            tasks.append(asyncio.create_task(self._stream_ticks(ws, req)))
            return []
        if msg_type == "proposal":
            pid, amount = _sid(), float(req.get("amount", 1))
            self.proposals[pid] = {"amount": amount, "req": req}
            return [self._envelope(req, "proposal", proposal={
                "id": pid, "ask_price": amount, "payout": round(amount * (1 + self.r), 2),
                "spot": 100.0, "spot_time": self._epoch, "date_start": self._epoch, "longcode": "fake"},
                subscription={"id": _sid()})]
        if msg_type == "buy":
            pid = req["buy"]
            prop = self.proposals.get(pid)
            if prop is None or pid in self.bought:
                # UNVERIFIED-ERR: real code and shape for buying an unknown or already used proposal id.
                return [self._envelope(req, "buy", error={
                    "code": "FakeInvalidProposal", "message": "unknown or already used proposal"})]
            self.bought.add(pid)
            cid, self._next_contract = self._next_contract, self._next_contract + 1
            price = min(prop["amount"], float(req["price"]))
            payout = round(prop["amount"] * (1 + self.r), 2)
            c = Contract(cid, pid, price, payout, prop["req"]["contract_type"], prop["req"]["underlying_symbol"],
                         asyncio.get_running_loop().time())
            self.contracts[cid] = c
            self.balance -= price
            asyncio.get_running_loop().call_later(self.settle_after, self._settle, cid)
            return [self._envelope(req, "buy", buy={
                "balance_after": self.balance, "buy_price": price, "contract_id": cid, "longcode": "fake",
                "payout": payout, "purchase_time": self._epoch, "shortcode": "fake", "start_time": self._epoch,
                "transaction_id": cid + 5000})]
        if msg_type == "portfolio":
            open_ = [c for c in self.contracts.values() if not c.settled]
            return [self._envelope(req, "portfolio", portfolio={"contracts": [{
                "contract_id": c.contract_id, "buy_price": c.buy_price, "payout": c.payout,
                "contract_type": c.contract_type, "underlying_symbol": c.symbol, "currency": "USD",
                "date_start": self._epoch, "expiry_time": self._epoch + 5, "longcode": "fake",
                "purchase_time": self._epoch, "shortcode": "fake", "transaction_id": c.contract_id + 5000}
                for c in open_]})]
        if msg_type == "proposal_open_contract":
            tasks.append(asyncio.create_task(self._stream_contract(ws, req)))
            return []
        if msg_type == "forget":
            return [self._envelope(req, "forget", forget=1)]
        # UNVERIFIED-ERR: invented code for an unsupported request.
        return [self._envelope(req, msg_type, error={"code": "FakeUnsupported", "message": "not implemented"})]

    def _settle(self, cid: int) -> None:
        c = self.contracts[cid]
        c.settled = True
        c.profit = round(c.payout - c.buy_price, 2) if self.win else -c.buy_price
        self.balance += c.payout if self.win else 0.0

    async def _stream_ticks(self, ws, req: dict) -> None:
        n = 0
        symbol = req["ticks"]
        try:
            while True:
                await asyncio.sleep(self.tick_interval)
                self._epoch += 1
                n += 1
                quote = 100.0 + (n % 17) * 0.01
                await ws.send(json.dumps(self._envelope(req, "tick", tick={
                    "ask": quote + 0.01, "bid": quote - 0.01, "epoch": self._epoch, "id": _sid(),
                    "pip_size": 2, "quote": quote, "symbol": symbol}, subscription={"id": _sid()})))
                if self.drop_ticks_after is not None and n >= self.drop_ticks_after:
                    self.drop_ticks_after = None
                    await ws.close()
                    return
        except (websockets.ConnectionClosed, asyncio.CancelledError):
            return

    async def _stream_contract(self, ws, req: dict) -> None:
        cid = req.get("contract_id")
        try:
            while True:
                c = self.contracts.get(cid)
                if c is None:
                    # UNVERIFIED-ERR: real code and shape for an unknown contract_id.
                    await ws.send(json.dumps(self._envelope(req, "proposal_open_contract", error={
                        "code": "FakeContractNotFound", "message": "no such contract"})))
                    return
                body = {"contract_id": c.contract_id, "is_sold": int(c.settled), "is_expired": int(c.settled),
                        "buy_price": f"{c.buy_price:.2f}", "payout": f"{c.payout:.2f}",
                        "underlying_symbol": c.symbol, "contract_type": c.contract_type,
                        "profit": f"{c.profit:.2f}"}
                if c.settled:
                    body.update(status="won" if c.profit > 0 else "lost",
                                sell_price=f"{c.payout if c.profit > 0 else 0:.2f}")
                await ws.send(json.dumps(self._envelope(req, "proposal_open_contract",
                                                        proposal_open_contract=body)))
                if c.settled:
                    return
                await asyncio.sleep(self.settle_after / 4 or 0.01)
        except (websockets.ConnectionClosed, asyncio.CancelledError):
            return


_TYPES = ("ticks", "ping", "balance", "proposal_open_contract", "proposal", "buy", "portfolio", "forget")


def _sid() -> str:
    return secrets.token_hex(16)  # 32 chars, matches the schema id pattern
