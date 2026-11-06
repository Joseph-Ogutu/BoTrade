"""Live tick handling inside the trading service.

TickFreshness records only WHEN the latest tick of each symbol arrived (the risk engine's market-data-fresh rule). It
computes nothing from quote or digit values.

SignalRunner feeds approved strategies with live ticks and submits intents. Running strategies on live ticks is a use of
real data, so it REFUSES to be constructed without the owner's real-data approval (backtest.permit): with no approval
the trading service runs with the executor, reconciler, kill switch and notifier, and no signals.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import numpy as np

from derivbot.backtest.permit import Approval, RealDataNotApproved
from derivbot.config.risk import StrategyApprovals
from derivbot.deriv import messages as m
from derivbot.deriv.session import ConnectionLost, DerivSession
from derivbot.risk.engine import IntentRequest
from derivbot.signals.base import Strategy, TickWindow

log = logging.getLogger(__name__)
MAX_HISTORY = 3000


class TickFreshness:
    def __init__(self, symbols: list[str], clock: Callable[[], float] = time.time, kv=None) -> None:
        self.symbols, self._clock, self._kv = list(symbols), clock, kv
        self.last_seen: dict[str, float] = {}
        self.last_tick: dict[str, tuple[str, int, int]] = {}       # symbol -> (quote text at pip_size decimals, epoch, pip_size)
        self._sinks: list[Callable[[m.Tick], object]] = []

    def add_sink(self, fn: Callable[[m.Tick], object]) -> None:
        self._sinks.append(fn)

    def handle(self, raw: dict) -> None:
        try:
            msg = m.parse_message(raw)
        except Exception:  # noqa: BLE001 - a tick error must not break the message loop
            return
        if isinstance(msg, m.TickMsg):
            self.last_seen[msg.tick.symbol] = self._clock()
            self.last_tick[msg.tick.symbol] = (f"{msg.tick.quote:.{msg.tick.pip_size}f}", msg.tick.epoch, msg.tick.pip_size)
            if self._kv is not None:
                try:
                    self._kv.set(f"tick:last:{msg.tick.symbol}", str(msg.tick.epoch), 3600)
                except Exception:  # noqa: BLE001
                    pass
            for sink in self._sinks:
                sink(msg.tick)

    def age(self, symbol: str) -> float | None:
        t = self.last_seen.get(symbol)
        return None if t is None else max(0.0, self._clock() - t)

    async def on_connected(self, session: DerivSession) -> None:
        for sym in self.symbols:
            try:
                await session.send(m.req_ticks(sym, session.next_req_id()))
            except ConnectionLost:
                return


class SignalRunner:
    def __init__(self, approval: Approval | None, approvals: StrategyApprovals, strategies: dict[str, Strategy],
                 symbol: str, account_id: str, submit: Callable[[IntentRequest], Any], mode: str = "demo") -> None:
        if not isinstance(approval, Approval):
            raise RealDataNotApproved("the signal runner uses live ticks and needs the owner's real-data approval")
        self._approvals, self._symbol, self._account, self._submit, self._mode = approvals, symbol, account_id, submit, mode
        self._strategies = {sid: s for sid, s in strategies.items() if approvals.is_approved(s.id, s.version)}
        self._q: list[float] = []
        self._d: list[int] = []
        self._e: list[int] = []
        self.submitted = 0

    @property
    def active_strategies(self) -> list[str]:
        return sorted(self._strategies)

    def on_tick(self, tick: m.Tick) -> list[IntentRequest]:
        if tick.symbol != self._symbol:
            return []
        self._q.append(float(tick.quote))
        # the digit is the last character of the quote at exactly pip_size decimals (kill-criterion A1.4)
        self._d.append(int(format(tick.quote.quantize(Decimal(1).scaleb(-tick.pip_size)), "f")[-1]))
        self._e.append(tick.epoch)
        if len(self._q) > MAX_HISTORY:
            del self._q[0], self._d[0], self._e[0]
        window = TickWindow(np.asarray(self._q), np.asarray(self._d, dtype=np.int8), np.asarray(self._e, dtype=np.int64), len(self._q) - 1)
        out = []
        for strat in self._strategies.values():
            sig = strat.signal(window)
            if sig is None:
                continue
            req = IntentRequest(idempotency_key=f"{strat.id}:{strat.version}:{self._symbol}:{tick.epoch}",
                                symbol=self._symbol, contract_type=sig.contract_type, strategy_id=strat.id,
                                strategy_version=strat.version, mode=self._mode, account_id=self._account)
            out.append(req)
            self.submitted += 1
            self._submit(req)
        return out
