"""Tick series for the backtester, plus SIMULATED generators. The generators are the only data the tests and controls
use. Real ticks can be loaded only through backtest.permit (owner approval after Gate 1)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class TickSeries:
    symbol: str
    epochs: np.ndarray        # strictly increasing int64
    quotes: np.ndarray        # float64
    digits: np.ndarray        # int8, last digit at pip_size decimals
    pip_size: int

    def __post_init__(self) -> None:
        n = self.epochs.size
        if not (self.quotes.size == n == self.digits.size):
            raise ValueError("epochs, quotes and digits must have the same length")
        if n > 1 and np.any(np.diff(self.epochs) <= 0):
            raise ValueError("epochs must be strictly increasing")

    def __len__(self) -> int:
        return int(self.epochs.size)

    def prefix(self, n: int) -> "TickSeries":
        return TickSeries(self.symbol, self.epochs[:n], self.quotes[:n], self.digits[:n], self.pip_size)

    @classmethod
    def from_quotes(cls, symbol: str, epochs: np.ndarray, quotes: np.ndarray, pip_size: int) -> "TickSeries":
        scaled = np.rint(np.asarray(quotes, dtype=np.float64) * 10 ** pip_size).astype(np.int64)
        return cls(symbol, np.asarray(epochs, dtype=np.int64), scaled / 10.0 ** pip_size, (scaled % 10).astype(np.int8), pip_size)


def simulate_random_walk(n: int, seed: int, start: float = 1000.0, step: float = 0.5, pip_size: int = 2,
                         rho: float = 0.0, interval: int = 1, symbol: str = "SIM") -> TickSeries:
    """Gaussian increments, optionally with AR(1) autocorrelation rho (a PLANTED momentum when rho > 0)."""
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, step, n)
    inc = np.empty(n)
    inc[0] = noise[0]
    for i in range(1, n):
        inc[i] = rho * inc[i - 1] + noise[i]
    quotes = np.maximum(start + np.cumsum(inc), 1.0)
    return TickSeries.from_quotes(symbol, np.arange(n, dtype=np.int64) * interval + 1_000, quotes, pip_size)


def simulate_digits(n: int, seed: int, symbol: str = "SIMD", pip_size: int = 2) -> TickSeries:
    """iid uniform last digits on an arbitrary smooth price path (price strategies are not meant to be used on it)."""
    rng = np.random.default_rng(seed)
    base = 1000.0 + np.cumsum(rng.normal(0, 0.2, n))
    digits = rng.integers(0, 10, n)
    scaled = (np.rint(base * 10 ** pip_size).astype(np.int64) // 10) * 10 + digits
    return TickSeries(symbol, np.arange(n, dtype=np.int64) + 1_000, scaled / 10.0 ** pip_size, digits.astype(np.int8), pip_size)


def shuffled_returns(series: TickSeries, seed: int) -> TickSeries:
    """Control: same increments in random order. Destroys any time structure, keeps the marginal distribution."""
    rng = np.random.default_rng(seed)
    inc = np.diff(series.quotes)
    rng.shuffle(inc)
    quotes = np.concatenate(([series.quotes[0]], series.quotes[0] + np.cumsum(inc)))
    return TickSeries.from_quotes(series.symbol, series.epochs, np.maximum(quotes, 1.0), series.pip_size)
