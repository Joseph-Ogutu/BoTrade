"""Pipeline soak test: NOT a strategy. It exists to exercise the execution machinery on the DEMO account.

What it does: every interval it submits one CALL or PUT, picked by a coin flip, at the 1HZ100V index for 5 ticks, through the
normal executor path (risk engine, proposal, buy, reconcile, settlement). The stake is whatever the risk engine sizes under
the owner's limits.

What it is not: it uses no price data, no signal and no statistic, so it cannot look ahead, cannot overfit and says nothing
about any edge. Its results must never be used to choose, tune or approve a strategy. With a fair coin the expected result
is a loss of the payout margin (about 5 percent of stake per trade). It touches no collected tick data and nothing in
docs/hypotheses. It never raises the stake after a loss: the stake is not its decision.

Purpose: observe a winning contract, buy error shapes, reconnects and limits under real conditions (docs/api-notes.md lists
these as UNVERIFIED). It is OFF unless the deployment sets PIPELINE_SOAK=1, and it only ever submits mode "demo".
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from collections.abc import Callable
from typing import Any

from derivbot.risk.engine import IntentRequest

log = logging.getLogger(__name__)
SOAK_ID, SOAK_VERSION = "pipeline_soak", "1"
SYMBOL = "1HZ100V"


class SoakTrader:
    def __init__(self, submit: Callable[[IntentRequest], Any], account_id: str, kill_engaged: Callable[[], bool],
                 session_ready: Callable[[], bool], interval_s: float = 300.0, rng: random.Random | None = None,
                 key: Callable[[], str] = lambda: uuid.uuid4().hex) -> None:
        if interval_s < 30:
            raise ValueError("soak interval must be at least 30 seconds")
        self.submit, self.account_id, self.kill_engaged, self.session_ready = submit, account_id, kill_engaged, session_ready
        self.interval_s, self.rng, self._key = interval_s, rng or random.SystemRandom(), key
        self.submitted = 0
        self.last_result: str | None = None

    def next_request(self) -> IntentRequest:
        return IntentRequest(f"soak-{self._key()}", SYMBOL, self.rng.choice(("CALL", "PUT")), SOAK_ID, SOAK_VERSION, "demo",
                             self.account_id)

    async def run_once(self) -> bool:
        """One attempt. Skips quietly when the kill switch is engaged or there is no connected session."""
        if self.kill_engaged() or not self.session_ready():
            return False
        result = await self.submit(self.next_request())
        self.submitted += 1
        self.last_result = f"{getattr(result, 'state', None)} {getattr(result, 'reason', '')}".strip()
        log.info("soak trade %d: %s", self.submitted, self.last_result)
        return True

    async def run(self, halt: asyncio.Event, startup_delay_s: float = 60.0) -> None:
        try:
            await asyncio.wait_for(halt.wait(), timeout=startup_delay_s)       # let the session, the reconcile and the exchange limits settle
            return
        except TimeoutError:
            pass
        while not halt.is_set():
            try:
                await self.run_once()
            except Exception as exc:  # noqa: BLE001 - the executor already alerts and engages the kill switch on its own errors
                log.warning("soak attempt failed: %s", type(exc).__name__)
            try:
                await asyncio.wait_for(halt.wait(), timeout=self.interval_s)
            except TimeoutError:
                pass
