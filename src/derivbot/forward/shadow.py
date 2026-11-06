"""Forward test ledger (Amendment 5): follows each registered strategy's would-be contracts on the LIVE tick stream.

Protocol, identical to derivbot.backtest.engine.run_backtest (a test feeds both the same series and requires the same contracts):
  * a decision at tick t uses ticks up to t only;
  * the entry spot is the quote of tick t+1, the exit spot the quote of tick t+1+duration (5 ticks later);
  * CALL wins only if exit > entry strictly, PUT only if exit < entry strictly, a tie loses;
  * a variant holds at most one open contract; the next decision may be taken on the exit tick itself.
Discards (counted, never silent): a gap in the tick stream between the decision tick and the exit tick, or a disconnect or restart while
a contract is open. After any gap or disconnect the history is cleared and decisions wait for the warm-up to finish again, so no
decision is made on a window that spans a hole. No contract is added or removed after its outcome is known.

The ledger reads no stored tick, no digit statistic and no database. It only sees ticks as they arrive, in memory.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

import numpy as np

from derivbot.signals.base import Strategy, TickWindow

log = logging.getLogger(__name__)
MAX_HISTORY = 3000
NOMINAL_INTERVAL_S = {"1HZ100V": 1, "R_100": 2}          # as registered in Amendment 1 (A1.3)
CONTROL_ID = "control_random"
CONTROL_RATE = 1 / 40                                    # chance per free tick that the coin-flip control opens a contract


@dataclass(frozen=True)
class Variant:
    key: str                      # "<strategy id>:<symbol>"
    strategy_id: str
    strategy_version: str
    symbol: str
    strategy: Strategy | None     # None for the coin-flip control
    duration: int
    warmup: int


@dataclass(frozen=True)
class Outcome:
    variant: str
    strategy_id: str
    strategy_version: str
    symbol: str
    side: str
    signal_epoch: int
    entry_epoch: int
    exit_epoch: int
    entry_quote: Decimal
    exit_quote: Decimal
    won: bool


@dataclass
class _Open:
    variant: Variant
    side: str
    signal_abs: int
    signal_epoch: int
    last_epoch: int
    entry_quote: Decimal | None = None
    entry_epoch: int | None = None


def build_variants(strategies: dict[str, Strategy], symbols: list[str], controls: bool = True) -> list[Variant]:
    out = [Variant(f"{key}:{sym}", s.id, s.version, sym, s, s.duration_ticks, s.warmup_ticks)
           for key, s in strategies.items() for sym in symbols]
    if controls:
        out += [Variant(f"{CONTROL_ID}:{sym}", CONTROL_ID, "1", sym, None, 5, 0) for sym in symbols]
    return out


def wins(side: str, entry: Decimal, exit_: Decimal) -> bool:
    """The contract rule, verified against 118 real contracts: strictly higher for CALL, strictly lower for PUT, a tie loses."""
    return exit_ > entry if side == "CALL" else exit_ < entry


class ShadowLedger:
    def __init__(self, variants: list[Variant], on_outcome: Callable[[Outcome], None],
                 on_discard: Callable[[str, int, str], None], rng: random.Random | None = None,
                 interval_s: dict[str, int] | None = None,
                 on_signal: Callable[[Variant, str, int], None] | None = None) -> None:
        self.variants = variants
        self._on_outcome, self._on_discard = on_outcome, on_discard
        self._on_signal = on_signal or (lambda v, side, epoch: None)
        self._rng = rng or random.SystemRandom()
        self._interval = interval_s or NOMINAL_INTERVAL_S
        self._by_symbol: dict[str, list[Variant]] = {}
        for v in variants:
            self._by_symbol.setdefault(v.symbol, []).append(v)
        self._q: dict[str, list[float]] = {s: [] for s in self._by_symbol}
        self._d: dict[str, list[int]] = {s: [] for s in self._by_symbol}
        self._e: dict[str, list[int]] = {s: [] for s in self._by_symbol}
        self._abs: dict[str, int] = {s: -1 for s in self._by_symbol}              # absolute tick index since the last reset
        self._open: dict[str, _Open] = {}                                            # variant key -> open shadow contract
        self.completed: dict[str, int] = {v.key: 0 for v in variants}
        self.discarded: dict[str, int] = {v.key: 0 for v in variants}
        self.resets = 0

    # ------------------------------------------------------------------ discards and resets
    def _discard(self, key: str, reason: str) -> None:
        o = self._open.pop(key, None)
        if o is None:
            return
        self.discarded[key] += 1
        self._on_discard(key, o.signal_epoch, reason)

    def reset(self, symbol: str | None = None, reason: str = "disconnect") -> None:
        """Connection lost, or a hole in the stream: discard open contracts and clear history so warm-up starts again."""
        symbols = [symbol] if symbol else list(self._by_symbol)
        for s in symbols:
            for v in self._by_symbol.get(s, []):
                self._discard(v.key, reason)
            self._q[s].clear()
            self._d[s].clear()
            self._e[s].clear()
            self._abs[s] = -1
        self.resets += 1

    # ------------------------------------------------------------------ one tick
    def on_tick(self, symbol: str, quote: Decimal, digit: int, epoch: int) -> None:
        if symbol not in self._by_symbol:
            return
        e = self._e[symbol]
        if e and epoch - e[-1] != self._interval.get(symbol, 1):
            if epoch <= e[-1]:
                return                                                              # a duplicate or out of order tick: ignore it
            self.reset(symbol, "gap")                                               # a hole: nothing spans it
        self._q[symbol].append(float(quote))
        self._d[symbol].append(digit)
        e.append(epoch)
        self._abs[symbol] += 1
        i = self._abs[symbol]
        if len(e) > MAX_HISTORY:
            del self._q[symbol][0], self._d[symbol][0], e[0]

        # 1. progress and settle open contracts
        for v in self._by_symbol[symbol]:
            o = self._open.get(v.key)
            if o is None:
                continue
            if i == o.signal_abs + 1:
                o.entry_quote, o.entry_epoch = quote, epoch
            if i == o.signal_abs + 1 + v.duration:
                assert o.entry_quote is not None and o.entry_epoch is not None
                self._open.pop(v.key)
                self.completed[v.key] += 1
                self._on_outcome(Outcome(v.key, v.strategy_id, v.strategy_version, v.symbol, o.side, o.signal_epoch, o.entry_epoch,
                                         epoch, o.entry_quote, quote, wins(o.side, o.entry_quote, quote)))

        # 2. decisions (a variant with no open contract may decide on the very tick another one settled)
        window = None
        for v in self._by_symbol[symbol]:
            if v.key in self._open or i < v.warmup:
                continue
            if v.strategy is None:
                side = self._rng.choice(("CALL", "PUT")) if self._rng.random() < CONTROL_RATE else None
            else:
                if window is None:
                    window = TickWindow(np.asarray(self._q[symbol]), np.asarray(self._d[symbol], dtype=np.int8),
                                        np.asarray(e, dtype=np.int64), len(e) - 1)
                sig = v.strategy.signal(window)
                side = sig.contract_type if sig is not None else None
            if side in ("CALL", "PUT"):
                self._open[v.key] = _Open(v, side, i, epoch, epoch)
                self._on_signal(v, side, epoch)                                     # lets the caller place a demo order as well

    # ------------------------------------------------------------------ read only
    def open_count(self) -> int:
        return len(self._open)
