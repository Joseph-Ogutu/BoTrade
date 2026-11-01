"""Open-contract tracking via proposal_open_contract (HANDBOOK 7).

Settlement is recorded only when the message says the contract is sold or expired AND carries a profit figure. Which
flag Deriv sets first, and whether a settled message always has profit, is UNVERIFIED (api-notes item 4); a message
that says settled but lacks profit is NOT treated as a settlement, so a result is never invented.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal

from derivbot.deriv import messages as m
from derivbot.deriv.errors import DerivApiError
from derivbot.deriv.session import ConnectionLost, DerivSession

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settlement:
    contract_id: int
    profit: Decimal
    sell_price: Decimal | None


class ContractTracker:
    def __init__(self) -> None:
        self._futures: dict[int, asyncio.Future] = {}
        self.tracked: set[int] = set()

    def register(self, contract_id: int) -> asyncio.Future:
        fut = self._futures.get(contract_id)
        if fut is None or fut.done():
            fut = asyncio.get_running_loop().create_future()
            self._futures[contract_id] = fut
        self.tracked.add(contract_id)
        return fut

    def forget(self, contract_id: int) -> None:
        self.tracked.discard(contract_id)
        self._futures.pop(contract_id, None)

    def handle(self, raw: dict) -> None:
        """Session handler for unsolicited proposal_open_contract messages."""
        try:
            msg = m.parse_message(raw)
        except DerivApiError as exc:
            log.warning("proposal_open_contract error: %s", exc.code)    # UNVERIFIED-ERR: shape on the demo endpoint
            return
        if not isinstance(msg, m.OpenContractMsg):
            return
        c = msg.contract
        fut = self._futures.get(c.contract_id)
        if fut is None or fut.done() or not c.is_settled or c.profit is None:
            return
        fut.set_result(Settlement(c.contract_id, c.profit, c.sell_price))

    async def subscribe(self, session: DerivSession, contract_id: int) -> asyncio.Future:
        fut = self.register(contract_id)
        await session.send({"proposal_open_contract": 1, "contract_id": contract_id, "subscribe": 1,
                            "req_id": session.next_req_id()})
        return fut

    async def resubscribe_all(self, session: DerivSession) -> None:
        """After every reconnect. Whether subscriptions survive is UNVERIFIED, so always assume they did not."""
        for cid in sorted(self.tracked):
            fut = self._futures.get(cid)
            if fut is not None and not fut.done():
                try:
                    await session.send({"proposal_open_contract": 1, "contract_id": cid, "subscribe": 1,
                                        "req_id": session.next_req_id()})
                except ConnectionLost:
                    return

    async def wait(self, contract_id: int, timeout: float) -> Settlement | None:
        fut = self._futures.get(contract_id)
        if fut is None:
            return None
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout)
        except TimeoutError:
            return None
