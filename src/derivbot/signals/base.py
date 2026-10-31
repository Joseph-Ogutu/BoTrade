"""Strategy interface (HANDBOOK 6.1): a pure, deterministic function from market data to a signal.

A strategy sees the world only through a TickWindow, which exposes ticks up to and including the decision tick and
nothing later. Strategies never touch the network, a clock, a database or a random generator. Parameters are fixed
dataclass values chosen BEFORE any real-data run (docs/hypotheses/); nothing here tunes them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

DIGIT_CONTRACTS = ("DIGITEVEN", "DIGITODD", "DIGITOVER", "DIGITUNDER", "DIGITMATCH", "DIGITDIFF")
PRICE_CONTRACTS = ("CALL", "PUT")
CONTRACTS = DIGIT_CONTRACTS + PRICE_CONTRACTS


class LookaheadError(Exception):
    pass


@dataclass(frozen=True)
class Signal:
    contract_type: str
    barrier: int | None = None          # digit barrier for OVER, UNDER, MATCH, DIFF

    def __post_init__(self) -> None:
        if self.contract_type not in CONTRACTS:
            raise ValueError(f"unsupported contract type {self.contract_type!r}")


class TickWindow:
    """Read-only causal view: ticks 0..t inclusive. There is no way to ask for a later tick."""

    __slots__ = ("_q", "_d", "_e", "_t")

    def __init__(self, quotes: np.ndarray, digits: np.ndarray, epochs: np.ndarray, t: int) -> None:
        self._q, self._d, self._e, self._t = quotes, digits, epochs, t

    @property
    def index(self) -> int:
        """Number of ticks seen so far (t + 1)."""
        return self._t + 1

    @property
    def last_epoch(self) -> int:
        return int(self._e[self._t])

    @property
    def last_quote(self) -> float:
        return float(self._q[self._t])

    @property
    def last_digit(self) -> int:
        return int(self._d[self._t])

    def tail_quotes(self, n: int) -> np.ndarray:
        return self._ro(self._q[max(0, self._t + 1 - n): self._t + 1])

    def tail_digits(self, n: int) -> np.ndarray:
        return self._ro(self._d[max(0, self._t + 1 - n): self._t + 1])

    def tail_epochs(self, n: int) -> np.ndarray:
        return self._ro(self._e[max(0, self._t + 1 - n): self._t + 1])

    @staticmethod
    def _ro(a: np.ndarray) -> np.ndarray:
        v = a.view()
        v.flags.writeable = False
        return v

    def peek(self, k: int = 1):
        raise LookaheadError("a strategy may not look at future ticks")


class Strategy(Protocol):
    id: str
    version: str
    duration_ticks: int
    warmup_ticks: int

    @property
    def params(self) -> dict[str, Any]: ...

    def signal(self, window: TickWindow) -> Signal | None: ...
