"""Registered hypotheses: the finite grids fixed in advance (kill-criterion Amendment 4). A grid is NEVER narrowed or
extended after seeing data. Every grid point counts towards K, the number of variants tried, in the multiple-testing
correction. Regime filters wrap the DEFAULT inner strategy only, and the selector uses the DEFAULT candidates."""

from __future__ import annotations

from itertools import product
from typing import Any, cast

from derivbot.signals.strategies import (
    AdaptiveSelector,
    EmaTrend,
    RegimeFilter,
    RsiBollinger,
    VolBreakout,
    default_strategies,
)

CONFIRMATORY_SYMBOLS = ("1HZ100V", "R_100")        # K counts each variant once per confirmatory symbol
DURATION_TICKS = 5

GRIDS: dict[str, dict[str, list[Any]]] = {
    "ema_trend": {"fast": [5, 9, 13], "slow": [21, 34]},
    "rsi_bollinger": {"thresholds": [[30, 70], [25, 75]], "bb_k": [2.0, 2.5]},
    "vol_breakout": {"lookback": [20, 40], "k": [2.0, 3.0]},
    "regime_ema_trend": {"threshold": [0.2, 0.3]},
    "regime_vol_breakout": {"threshold": [0.2, 0.3]},
    "regime_rsi_bollinger": {"threshold": [0.2, 0.3]},
    "adaptive_selector": {"eval_window": [45, 60]},
}


def grid_points(name: str) -> list[dict[str, Any]]:
    grid = GRIDS[name]
    keys = list(grid)
    return [dict(zip(keys, vals)) for vals in product(*(grid[k] for k in keys))]


def build_variant(name: str, point: dict[str, Any]):
    base = default_strategies()
    if name == "ema_trend":
        return EmaTrend(**point)
    if name == "rsi_bollinger":
        lo, hi = point["thresholds"]
        return RsiBollinger(rsi_low=float(lo), rsi_high=float(hi), bb_k=point["bb_k"])
    if name == "vol_breakout":
        return VolBreakout(**point)
    if name.startswith("regime_"):
        inner = base[name.removeprefix("regime_")]
        return RegimeFilter(inner, "ranging" if "rsi" in name else "trending", threshold=point["threshold"])
    if name == "adaptive_selector":
        return AdaptiveSelector(cast(AdaptiveSelector, base["adaptive_selector"]).candidates, eval_window=point["eval_window"])
    raise KeyError(name)


def variants_per_symbol() -> int:
    return sum(len(grid_points(n)) for n in GRIDS)


def total_k() -> int:
    return variants_per_symbol() * len(CONFIRMATORY_SYMBOLS)
