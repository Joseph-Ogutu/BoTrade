"""Strategy tests on SIMULATED data only. No parameter is tuned; defaults are the registered conventional values."""

import dataclasses
import hashlib
import json
import re
from decimal import Decimal as D
from pathlib import Path

import numpy as np
import pytest

from derivbot.backtest.engine import causality_check, run_backtest
from derivbot.backtest.payout import PayoutModel, Quote
from derivbot.backtest.series import TickSeries, simulate_random_walk
from derivbot.signals import registry
from derivbot.signals.base import Signal, TickWindow
from derivbot.signals.strategies import (
    AdaptiveSelector,
    EmaTrend,
    RegimeFilter,
    RsiBollinger,
    VolBreakout,
    default_strategies,
)

ROOT = Path(__file__).resolve().parents[2]
PAYOUT = PayoutModel([Quote("CALL", D("1"), D("1.95")), Quote("PUT", D("1"), D("1.95"))])


def win(quotes, t=None):
    q = np.asarray(quotes, dtype=float)
    t = len(q) - 1 if t is None else t
    return TickWindow(q, np.zeros(len(q), dtype=np.int8), np.arange(len(q), dtype=np.int64), t)


# ------------------------------------------------------------------ EMA trend, against an independent loop oracle

def oracle_ema(x, n):
    a, e = 2.0 / (n + 1.0), None
    num = den = 0.0
    for i, v in enumerate(x):                   # finite-memory EMA: weights alpha*(1-alpha)^age, normalised
        age = len(x) - 1 - i
        w = a * (1 - a) ** age
        num += w * v
        den += w
    return num / den


def oracle_signal(q, t, fast=9, slow=21):
    look = 5 * slow
    if t + 1 < look + 1:
        return None
    seg = q[t - look: t + 1]
    now, prev = seg[1:], seg[:-1]
    f1, s1, f0, s0 = oracle_ema(now, fast), oracle_ema(now, slow), oracle_ema(prev, fast), oracle_ema(prev, slow)
    if f0 <= s0 and f1 > s1:
        return Signal("CALL")
    if f0 >= s0 and f1 < s1:
        return Signal("PUT")
    return None


def test_ema_trend_equals_the_loop_oracle_at_every_tick_of_a_random_walk():
    s = simulate_random_walk(700, seed=2)
    strat, seen = EmaTrend(), set()
    for t in range(len(s)):
        got = strat.signal(TickWindow(s.quotes, s.digits, s.epochs, t))
        assert got == oracle_signal(s.quotes, t), t
        seen.add(got.contract_type if got else None)
    assert seen == {None, "CALL", "PUT"}          # the test did exercise both crossover directions


def test_ema_trend_is_silent_before_its_warmup():
    assert EmaTrend().signal(win(np.linspace(100, 120, 100))) is None


# ------------------------------------------------------------------ RSI + Bollinger

def test_rsi_bollinger_buys_a_spike_down_and_sells_a_spike_up_and_ignores_calm():
    calm = list(100 + 0.05 * np.sin(np.arange(40)))
    assert RsiBollinger().signal(win(calm)) is None
    down = calm[:-3] + [99.0, 98.0, 96.0]
    up = calm[:-3] + [101.0, 102.0, 104.0]
    assert RsiBollinger().signal(win(down)) == Signal("CALL")
    assert RsiBollinger().signal(win(up)) == Signal("PUT")


def test_rsi_bollinger_requires_both_conditions():
    calm = list(100 + 0.05 * np.sin(np.arange(40)))
    spike_only = calm[:-1] + [101.5]                                            # beyond the band
    assert RsiBollinger(rsi_high=101.0).signal(win(spike_only)) is None         # RSI can never exceed 100
    assert RsiBollinger().signal(win([100.0] * 40)) is None                     # zero variance: no band


# ------------------------------------------------------------------ volatility breakout

def test_vol_breakout_follows_a_jump_larger_than_k_sd_in_either_direction():
    rng = np.random.default_rng(0)
    calm = list(100 + np.cumsum(rng.normal(0, 0.01, 30)))
    assert VolBreakout().signal(win(calm + [calm[-1] + 1.0])) == Signal("CALL")
    assert VolBreakout().signal(win(calm + [calm[-1] - 1.0])) == Signal("PUT")
    assert VolBreakout().signal(win(calm + [calm[-1] + 0.001])) is None
    assert VolBreakout().signal(win([100.0] * 31)) is None


# ------------------------------------------------------------------ regime filter

def test_regime_classification_and_gating():
    ramp = np.linspace(100, 110, 60)
    zig = 100 + 0.2 * (np.arange(60) % 2)
    f = RegimeFilter(EmaTrend(), "trending")
    assert f.regime(win(ramp)) == "trending" and f.regime(win(zig)) == "ranging"
    assert f.regime(win([100.0] * 60)) == "ranging"                              # no path at all counts as ranging
    inner_calls = []

    class Spy:
        id, version, duration_ticks, warmup_ticks, params = "spy", "1", 5, 0, {}

        def signal(self, w):
            inner_calls.append(1)
            return Signal("CALL")

    assert RegimeFilter(Spy(), "trending").signal(win(zig)) is None and inner_calls == []
    assert RegimeFilter(Spy(), "trending").signal(win(ramp)) == Signal("CALL")
    assert RegimeFilter(Spy(), "ranging").signal(win(zig)) == Signal("CALL")
    assert RegimeFilter(Spy(), "ranging").signal(win(ramp)) is None


def test_regime_filter_rejects_an_unknown_mode_and_inherits_the_inner_duration():
    with pytest.raises(ValueError):
        RegimeFilter(EmaTrend(), "sideways")
    assert RegimeFilter(EmaTrend(), "trending").duration_ticks == 5


# ------------------------------------------------------------------ adaptive selector

class AlwaysCall:
    id, version, duration_ticks, warmup_ticks, params = "always_call", "1", 5, 0, {}

    def signal(self, w):
        return Signal("CALL")


class AlwaysPut(AlwaysCall):
    id = "always_put"

    def signal(self, w):
        return Signal("PUT")


def test_selector_follows_the_candidate_that_has_been_right_and_drops_the_one_that_has_not():
    rising = np.linspace(100, 160, 200)
    sel = AdaptiveSelector((AlwaysPut(), AlwaysCall()), eval_window=45, step=3)
    assert sel.signal(win(rising)) == Signal("CALL")                             # CALL has been right every time
    falling = rising[::-1]
    assert sel.signal(win(falling)) == Signal("PUT")


def test_selector_abstains_when_nothing_has_a_good_record_or_too_few_decisions():
    flat = np.full(200, 100.0)
    assert AdaptiveSelector((AlwaysCall(), AlwaysPut()), eval_window=45, step=3).signal(win(flat)) is None
    assert AdaptiveSelector((AlwaysCall(),), eval_window=45, step=3, min_decisions=50).signal(win(np.linspace(1, 9, 200))) is None
    assert AdaptiveSelector((AlwaysCall(),), eval_window=45, step=3).signal(win(np.linspace(1, 9, 20))) is None


def test_selector_needs_candidates_with_a_common_duration():
    class Slow(AlwaysCall):
        duration_ticks = 7

    with pytest.raises(ValueError):
        AdaptiveSelector((AlwaysCall(), Slow()))


def test_selector_only_uses_decisions_whose_outcome_is_already_known():
    """Scoring a decision at k ticks back needs the quote dur+1 ticks after it: never beyond the current tick."""
    q = np.concatenate([np.linspace(100, 130, 150), np.full(5, 130.0)])
    full = AdaptiveSelector((AlwaysCall(),), eval_window=45, step=3)
    a = full.signal(win(q))
    b = full.signal(win(np.concatenate([q, [10_000.0]]), t=len(q) - 1))        # a later spike must change nothing
    assert a == b


# ------------------------------------------------------------------ properties shared by all strategies

NAMES = list(default_strategies())


@pytest.mark.parametrize("name", NAMES)
def test_pure_and_deterministic_and_does_not_mutate_its_input(name):
    s = simulate_random_walk(400, seed=5)
    strat = default_strategies()[name]
    before = hashlib.sha256(s.quotes.tobytes() + s.digits.tobytes() + s.epochs.tobytes()).hexdigest()
    outs1 = [strat.signal(TickWindow(s.quotes, s.digits, s.epochs, t)) for t in range(len(s))]
    outs2 = [strat.signal(TickWindow(s.quotes, s.digits, s.epochs, t)) for t in range(len(s))]
    assert outs1 == outs2
    assert hashlib.sha256(s.quotes.tobytes() + s.digits.tobytes() + s.epochs.tobytes()).hexdigest() == before
    assert all(o is None or o.contract_type in ("CALL", "PUT") for o in outs1)


@pytest.mark.parametrize("name", NAMES)
def test_every_strategy_passes_the_prefix_invariance_causality_check(name):
    s = simulate_random_walk(900, seed=6)
    causality_check(lambda series, n=name: default_strategies()[n], s, probes=25)


@pytest.mark.parametrize("name", NAMES)
def test_every_strategy_runs_through_the_backtester_on_simulated_data(name):
    s = simulate_random_walk(1500, seed=7)
    res = run_backtest(s, lambda series, n=name: default_strategies()[n], PAYOUT, check_causality=False)
    assert all(t.contract_type in ("CALL", "PUT") and t.entry_epoch > t.decision_epoch for t in res.trades)
    assert res.strategy_id == default_strategies()[name].id


def test_ids_are_unique_durations_are_five_ticks_and_instances_are_immutable():
    strats = default_strategies()
    assert len({s.id for s in strats.values()}) == len(strats)
    assert {s.duration_ticks for s in strats.values()} == {5}
    with pytest.raises(dataclasses.FrozenInstanceError):
        strats["ema_trend"].fast = 3                         # type: ignore[misc]


def test_strategy_module_imports_only_numpy_and_the_signal_base():
    src = (ROOT / "src/derivbot/signals/strategies.py").read_text()
    imports = set(re.findall(r"^(?:from|import) ([\w\.]+)", src, re.M))
    assert imports <= {"__future__", "dataclasses", "typing", "numpy", "derivbot.signals.base"}, imports
    for banned in ("random", "time", "datetime", "socket", "requests", "httpx", "websockets", "sqlite3", "anthropic", "openai"):
        assert banned not in imports


# ------------------------------------------------------------------ hypotheses registered and consistent with the code

def test_every_strategy_has_a_registered_hypothesis_whose_parameters_equal_the_code_defaults():
    strats = default_strategies()
    for name in registry.GRIDS:
        text = (ROOT / "docs" / "hypotheses" / f"{name}.md").read_text()
        block = json.loads(re.search(r"```json HYPOTHESIS\n(.*?)\n```", text, re.S).group(1))
        assert block["id"] == name and block["params"] == strats[name].params
        assert block["grid"] == registry.GRIDS[name] and block["grid_points"] == len(registry.grid_points(name))
        assert block["duration_ticks"] == registry.DURATION_TICKS


def test_the_committed_hypothesis_files_equal_what_the_generator_produces(tmp_path, monkeypatch):
    import runpy
    import shutil

    snapshot = {p.name: p.read_text() for p in (ROOT / "docs" / "hypotheses").glob("*.md")}
    try:
        runpy.run_path(str(ROOT / "scripts" / "gen_hypotheses.py"), run_name="__main__")
        regenerated = {p.name: p.read_text() for p in (ROOT / "docs" / "hypotheses").glob("*.md")}
    finally:
        for name, text in snapshot.items():
            (ROOT / "docs" / "hypotheses" / name).write_text(text)
    assert regenerated == snapshot


def test_index_k_equals_the_variants_actually_registered_and_every_grid_point_builds():
    assert registry.total_k() == 44
    assert f"Total K = {registry.total_k()}" in (ROOT / "docs" / "hypotheses" / "INDEX.md").read_text()
    for name in registry.GRIDS:
        for point in registry.grid_points(name):
            strat = registry.build_variant(name, point)
            assert strat.duration_ticks == registry.DURATION_TICKS


def test_no_strategy_default_or_grid_was_derived_from_the_collected_ticks():
    """Mechanical backstop: the strategy and registry modules never import anything that can read the tick store."""
    for rel in ("signals/strategies.py", "signals/registry.py", "signals/base.py"):
        text = (ROOT / "src/derivbot" / rel).read_text()
        for needle in ("sqlite3", "data/ticks", "holdout", "FROM ticks", "collector"):
            assert needle not in text, (rel, needle)
