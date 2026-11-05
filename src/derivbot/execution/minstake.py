"""Minimum stake discovery from the exchange, never from a constant.

OBSERVED on the demo endpoint (2026-11, one run): a proposal reply carries validation_params.stake.min as a decimal STRING
("0.35" for CALL on 1HZ100V, 5 ticks). Other contract types and symbols are UNVERIFIED, so every (symbol, contract type)
the bot may trade is probed on each connect and the HIGHEST minimum per symbol is kept (the safe side: a stake below any
of them is never sized). Anything missing, unparsable or not positive leaves the symbol UNKNOWN, and unknown means no trade
(risk/stake.py). A probe is a quote only: it sends a proposal and forgets the subscription; it never sends a buy.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from typing import Any

from derivbot.deriv import messages as m
from derivbot.deriv.session import ConnectionLost, DerivSession, RequestTimeout

log = logging.getLogger(__name__)


def parse_min_stake(reply: dict[str, Any]) -> Decimal | None:
    """The minimum from one proposal reply, or None if the reply does not state a usable one."""
    if reply.get("error") or not isinstance(reply.get("proposal"), dict):
        return None
    try:
        raw = reply["proposal"]["validation_params"]["stake"]["min"]
    except (KeyError, TypeError):
        return None
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):     # floats are refused: money is never a float
        return None
    try:
        value = Decimal(str(raw))
    except InvalidOperation:
        return None
    return value if value.is_finite() and value > 0 else None


class MinStakeCache:
    def __init__(self, symbols: Iterable[str], contract_types: Iterable[str] = ("CALL", "PUT"), duration: int = 5,
                 duration_unit: str = "t", currency: str = "USD", probe_stake: Decimal = Decimal("1")) -> None:
        self.symbols = tuple(symbols)
        self.contract_types = tuple(contract_types)
        self._dur, self._unit, self._ccy, self._probe = duration, duration_unit, currency, probe_stake
        self._min: dict[str, Decimal] = {}

    def get(self, symbol: str) -> Decimal | None:
        return self._min.get(symbol)

    async def on_disconnected(self) -> None:
        """A minimum learned on a dead connection is not trusted after it: back to unknown."""
        self._min.clear()

    async def on_connected(self, session: DerivSession) -> None:
        """Connect hook. Never raises for an ordinary failure: a symbol that cannot be probed simply stays unknown."""
        self._min.clear()
        for sym in self.symbols:
            found: list[Decimal] = []
            complete = True
            for ctype in self.contract_types:
                try:
                    reply = await session.request(m.req_proposal(sym, ctype, self._probe, self._dur, self._unit, self._ccy,
                                                                 session.next_req_id()))
                except (ConnectionLost, RequestTimeout) as exc:
                    log.warning("min stake probe %s %s failed: %s", sym, ctype, type(exc).__name__)
                    complete = False
                    if isinstance(exc, ConnectionLost):
                        return
                    continue
                sub_id = (reply.get("subscription") or {}).get("id")
                if isinstance(sub_id, str):
                    try:
                        await session.send(m.req_forget(sub_id, session.next_req_id()))
                    except (ConnectionLost, ValueError):
                        pass
                value = parse_min_stake(reply)
                if value is None:
                    log.warning("min stake probe %s %s: no usable validation_params", sym, ctype)
                    complete = False
                else:
                    found.append(value)
            if found and complete:                     # one unprobed type could have a higher minimum: stay unknown
                self._min[sym] = max(found)
        log.info("min stake discovered for %d of %d symbols", len(self._min), len(self.symbols))
