"""The five strategy modules (HANDBOOK 9.1). Pure, deterministic, no I/O, no clock, no randomness, no tuning.

Every parameter below is a conventional textbook value fixed BEFORE any real-data run and registered in
docs/hypotheses/. None was chosen by looking at data. Indicators use a bounded lookback so each call costs O(lookback)
and depends only on the last `lookback` ticks (a finite-memory EMA is used instead of an infinite recursion, which also
makes the signal at tick t independent of how long the series was before the window).

All strategies emit CALL or PUT with a fixed duration in ticks. They say nothing about stake, payout or risk: those
belong to the risk engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from derivbot.signals.base import Signal, Strategy, TickWindow


def _ema_weights(n: int, length: int) -> np.ndarray:
    alpha = 2.0 / (n + 1.0)
    w = alpha * (1.0 - alpha) ** np.arange(length)[::-1]
    return w / w.sum()


@dataclass(frozen=True)
class EmaTrend:
    """Fast EMA crossing the slow EMA: CALL on an upward cross, PUT on a downward cross."""

    fast: int = 9
    slow: int = 21
    duration_ticks: int = 5
    id: str = field(default="ema_trend", init=False)
    version: str = field(default="1", init=False)

    @property
    def lookback(self) -> int:
        return 5 * self.slow

    @property
    def warmup_ticks(self) -> int:
        return self.lookback + 1

    @property
    def params(self) -> dict[str, Any]:
        return {"fast": self.fast, "slow": self.slow, "duration_ticks": self.duration_ticks, "lookback": self.lookback}

    def signal(self, w: TickWindow) -> Signal | None:
        if w.index < self.warmup_ticks:
            return None
        x = w.tail_quotes(self.lookback + 1)
        wf, ws = _ema_weights(self.fast, self.lookback), _ema_weights(self.slow, self.lookback)
        now, prev = x[1:], x[:-1]
        f1, s1, f0, s0 = float(wf @ now), float(ws @ now), float(wf @ prev), float(ws @ prev)
        if f0 <= s0 and f1 > s1:
            return Signal("CALL")
        if f0 >= s0 and f1 < s1:
            return Signal("PUT")
        return None


@dataclass(frozen=True)
class RsiBollinger:
    """Mean reversion: price beyond the Bollinger band AND RSI at an extreme -> bet on the move back."""

    rsi_period: int = 14
    rsi_low: float = 30.0
    rsi_high: float = 70.0
    bb_period: int = 20
    bb_k: float = 2.0
    duration_ticks: int = 5
    id: str = field(default="rsi_bollinger", init=False)
    version: str = field(default="1", init=False)

    @property
    def warmup_ticks(self) -> int:
        return max(self.rsi_period + 1, self.bb_period) + 1

    @property
    def params(self) -> dict[str, Any]:
        return {"rsi_period": self.rsi_period, "rsi_low": self.rsi_low, "rsi_high": self.rsi_high,
                "bb_period": self.bb_period, "bb_k": self.bb_k, "duration_ticks": self.duration_ticks}

    def signal(self, w: TickWindow) -> Signal | None:
        if w.index < self.warmup_ticks:
            return None
        band = w.tail_quotes(self.bb_period)
        mean, sd = float(band.mean()), float(band.std())
        if sd == 0.0:
            return None
        d = np.diff(w.tail_quotes(self.rsi_period + 1))
        gain, loss = float(d[d > 0].sum()) / self.rsi_period, float(-d[d < 0].sum()) / self.rsi_period
        rsi = 50.0 if gain == 0 and loss == 0 else 100.0 if loss == 0 else 100.0 - 100.0 / (1.0 + gain / loss)
        price = w.last_quote
        if price < mean - self.bb_k * sd and rsi < self.rsi_low:
            return Signal("CALL")
        if price > mean + self.bb_k * sd and rsi > self.rsi_high:
            return Signal("PUT")
        return None


@dataclass(frozen=True)
class VolBreakout:
    """A tick move larger than k standard deviations of the preceding moves -> follow it."""

    lookback: int = 20
    k: float = 2.0
    duration_ticks: int = 5
    id: str = field(default="vol_breakout", init=False)
    version: str = field(default="1", init=False)

    @property
    def warmup_ticks(self) -> int:
        return self.lookback + 2

    @property
    def params(self) -> dict[str, Any]:
        return {"lookback": self.lookback, "k": self.k, "duration_ticks": self.duration_ticks}

    def signal(self, w: TickWindow) -> Signal | None:
        if w.index < self.warmup_ticks:
            return None
        d = np.diff(w.tail_quotes(self.lookback + 2))
        sd = float(d[:-1].std())
        if sd == 0.0:
            return None
        if d[-1] > self.k * sd:
            return Signal("CALL")
        if d[-1] < -self.k * sd:
            return Signal("PUT")
        return None


@dataclass(frozen=True)
class RegimeFilter:
    """Lets the inner strategy trade only in the chosen regime, measured by the efficiency ratio of the last n ticks
    (net move divided by total path length): high means trending, low means ranging. Not a predictor on its own."""

    inner: Strategy
    allow: str = "trending"           # "trending" or "ranging"
    n: int = 30
    threshold: float = 0.30

    def __post_init__(self) -> None:
        if self.allow not in ("trending", "ranging"):
            raise ValueError("allow must be 'trending' or 'ranging'")

    @property
    def id(self) -> str:
        return f"regime_{self.allow}({self.inner.id})"

    version = "1"

    @property
    def duration_ticks(self) -> int:
        return self.inner.duration_ticks

    @property
    def warmup_ticks(self) -> int:
        return max(self.inner.warmup_ticks, self.n + 1)

    @property
    def params(self) -> dict[str, Any]:
        return {"allow": self.allow, "n": self.n, "threshold": self.threshold, "inner": self.inner.params}

    def regime(self, w: TickWindow) -> str | None:
        x = w.tail_quotes(self.n + 1)
        if x.size < self.n + 1:
            return None
        path = float(np.abs(np.diff(x)).sum())
        if path == 0.0:
            return "ranging"
        return "trending" if abs(float(x[-1] - x[0])) / path >= self.threshold else "ranging"

    def signal(self, w: TickWindow) -> Signal | None:
        if w.index < self.warmup_ticks or self.regime(w) != self.allow:
            return None
        return self.inner.signal(w)


@dataclass(frozen=True)
class AdaptiveSelector:
    """Follows whichever candidate has the best RECENT hit rate, judged only on decisions whose outcome is already known.

    It re-evaluates each candidate's signal at past ticks (sampled every `step` ticks over `eval_window`), scores them
    against what happened (CALL/PUT, latency 1 tick), and trades the best candidate's current signal if its hit rate is
    at least `min_hit_rate` over at least `min_decisions` decisions. Registered as a hypothesis ("recent performance
    persists"), expected to do no better than its best candidate on random data. Candidates must be CALL/PUT strategies
    with the same duration."""

    candidates: tuple[Strategy, ...]
    eval_window: int = 60
    step: int = 3
    min_decisions: int = 8
    min_hit_rate: float = 0.55
    id: str = field(default="adaptive_selector", init=False)
    version: str = field(default="1", init=False)

    def __post_init__(self) -> None:
        if len({c.duration_ticks for c in self.candidates}) != 1:
            raise ValueError("all candidates need the same duration")

    @property
    def duration_ticks(self) -> int:
        return self.candidates[0].duration_ticks

    @property
    def warmup_ticks(self) -> int:
        return max(c.warmup_ticks for c in self.candidates) + self.eval_window

    @property
    def params(self) -> dict[str, Any]:
        return {"candidates": [c.id for c in self.candidates], "eval_window": self.eval_window, "step": self.step,
                "min_decisions": self.min_decisions, "min_hit_rate": self.min_hit_rate}

    def _hit_rate(self, cand: Strategy, w: TickWindow) -> tuple[int, float]:
        dur, hits, n = cand.duration_ticks, 0, 0
        tail = w.tail_quotes(self.eval_window + 1)
        for k in range(dur + 1, self.eval_window, self.step):
            past = w.back(k)
            if past.index <= cand.warmup_ticks:
                continue
            sig = cand.signal(past)
            if sig is None or sig.contract_type not in ("CALL", "PUT"):
                continue
            entry, exit_ = tail[-k - 1 + 1], tail[-k - 1 + 1 + dur]
            n += 1
            hits += (exit_ > entry) if sig.contract_type == "CALL" else (exit_ < entry)
        return n, (hits / n if n else 0.0)

    def signal(self, w: TickWindow) -> Signal | None:
        if w.index < self.warmup_ticks:
            return None
        best, best_rate = None, -1.0
        for cand in self.candidates:
            n, rate = self._hit_rate(cand, w)
            if n >= self.min_decisions and rate >= self.min_hit_rate and rate > best_rate:
                best, best_rate = cand, rate
        return None if best is None else best.signal(w)


def default_strategies() -> dict[str, Strategy]:
    base = {"ema_trend": EmaTrend(), "rsi_bollinger": RsiBollinger(), "vol_breakout": VolBreakout()}
    out: dict[str, Strategy] = dict(base)
    out["regime_ema_trend"] = RegimeFilter(base["ema_trend"], "trending")
    out["regime_vol_breakout"] = RegimeFilter(base["vol_breakout"], "trending")
    out["regime_rsi_bollinger"] = RegimeFilter(base["rsi_bollinger"], "ranging")
    out["adaptive_selector"] = AdaptiveSelector(tuple(base.values()))
    return out
