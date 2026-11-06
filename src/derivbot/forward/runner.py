"""Ties the forward ledger to the live tick stream and, separately, places demo orders (Amendment 5, A5.6).

The STATISTICS come only from the ledger. The demo orders are an extra: when a variant decides to open a contract, the first such
signal after a global cooldown also becomes a demo order through the normal risk engine and executor. No ranking, no preference, no
use of order results to choose anything. Orders and ledger are independent: a refused order never changes the ledger.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from sqlalchemy import Engine

from derivbot.deriv import messages as m
from derivbot.forward.permit import ForwardApproval
from derivbot.forward.shadow import ShadowLedger, Variant, build_variants
from derivbot.forward.store import LedgerStore
from derivbot.risk.engine import IntentRequest
from derivbot.signals.base import Strategy

log = logging.getLogger(__name__)


class ForwardRunner:
    def __init__(self, approval: ForwardApproval, strategies: dict[str, Strategy], symbols: list[str], engine: Engine, account_id: str,
                 submit: Callable[[IntentRequest], Any], alert: Callable[[str, str], None] = lambda lvl, txt: None,
                 cooldown_s: float | None = None, clock: Callable[[], float] = time.monotonic, rng: random.Random | None = None) -> None:
        if not isinstance(approval, ForwardApproval):
            raise TypeError("the forward test needs an owner approval (derivbot.forward.permit)")
        self.approval, self.account_id, self._submit, self._clock = approval, account_id, submit, clock
        self.cooldown_s = float(cooldown_s if cooldown_s is not None else approval.registration["live_order_cooldown_s"])
        self.store = LedgerStore(engine, alert)
        self.ledger = ShadowLedger(build_variants(strategies, symbols), self.store.push_outcome, self.store.push_discard, rng=rng,
                                   on_signal=self._on_signal)
        self._last_live = float("-inf")
        self.live_orders = 0

    # ------------------------------------------------------------------ ticks
    def on_tick(self, tick: m.Tick) -> None:
        digit = int(format(tick.quote.quantize(Decimal(1).scaleb(-tick.pip_size)), "f")[-1])
        self.ledger.on_tick(tick.symbol, tick.quote, digit, tick.epoch)

    async def on_disconnected(self) -> None:
        self.ledger.reset(reason="disconnect")

    # ------------------------------------------------------------------ demo orders
    def _on_signal(self, v: Variant, side: str, epoch: int) -> None:
        if v.strategy is None:                                     # the coin-flip control never trades
            return
        now = self._clock()
        if now - self._last_live < self.cooldown_s:
            return
        self._last_live = now
        req = IntentRequest(idempotency_key=f"fwd:{v.key}:{epoch}", symbol=v.symbol, contract_type=side, strategy_id=v.strategy_id,
                            strategy_version=v.strategy_version, mode="demo", account_id=self.account_id)
        self.live_orders += 1
        try:
            asyncio.get_running_loop().create_task(self._submit(req))
        except RuntimeError:                                       # no running loop (never in the service); the order is simply not placed
            log.warning("forward demo order not placed: no event loop")

    # ------------------------------------------------------------------ lifecycle
    async def run(self, halt: asyncio.Event) -> None:
        await self.store.run(halt)
        self.ledger.reset(reason="restart")                        # open contracts at shutdown are counted as discarded
        await asyncio.to_thread(self.store.flush)
