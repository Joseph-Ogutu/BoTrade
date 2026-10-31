"""Edge verdict implementing docs/kill-criterion.md v1 section 2 for a backtest on a LOCKED holdout.

Conditions: (1) n >= n_min from the power calculation at the real break-even; (2) the one-sided Wilson lower bound at
confidence 1 - 0.05/K exceeds p_be AND the exact binomial test rejects at the multiple-testing level; (3) expectancy is
positive at the payout used and at r - 0.05; (4) maximum drawdown is within the risk spec, and if no limit is given
this condition FAILS; (5) at least two time blocks with enough trades each and positive expectancy in each.

This function does not decide anything about real data by itself: it is only meaningful on a holdout opened under the
permit, and the harness exercises it here on simulated data only.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from derivbot.backtest.engine import Trade
from derivbot.backtest.metrics import by_block, compute
from derivbot.stats.edge import binomial_sf, expectancy, holm_reject, required_n, wilson_lower

ALPHA = 0.05


@dataclass(frozen=True)
class EdgeVerdict:
    passed: bool
    conditions: dict[str, bool]
    n: int
    n_min: int
    p_be: float
    p_hat: float
    wilson_lower: float
    p_value: float
    detail: dict[str, object]


def evaluate_variant(trades: list[Trade], k_variants: int = 1, delta: float = 0.02, power: float = 0.80,
                     max_drawdown_limit: Decimal | None = None, blocks: int = 2) -> EdgeVerdict:
    if k_variants < 1:
        raise ValueError("k_variants must be at least 1")
    m = compute(trades)
    if m.n == 0:
        return EdgeVerdict(False, {k: False for k in ("n_min", "significant", "expectancy", "drawdown", "blocks")},
                           0, 0, 1.0, 0.0, 0.0, 1.0, {})
    r = m.mean_r
    p_be = 1.0 / (1.0 + r)
    n_min = required_n(p_be, delta, ALPHA / k_variants, power)
    level = ALPHA / k_variants
    lower = wilson_lower(m.wins, m.n, confidence=1.0 - level)
    pval = binomial_sf(m.wins, m.n, p_be)
    c_n = m.n >= n_min
    c_sig = lower > p_be and pval <= level
    c_exp = expectancy(m.win_rate, r) > 0 and expectancy(m.win_rate, r - 0.05) > 0
    c_dd = max_drawdown_limit is not None and Decimal(str(m.max_drawdown)) <= max_drawdown_limit
    bl = by_block(trades, blocks)
    c_blocks = len(bl) == blocks and all(b.n >= n_min / 4 and b.expectancy_per_unit > 0 for b in bl)
    conds = {"n_min": c_n, "significant": c_sig, "expectancy": c_exp, "drawdown": c_dd, "blocks": c_blocks}
    return EdgeVerdict(all(conds.values()), conds, m.n, n_min, p_be, m.win_rate, lower, pval,
                       {"mean_r": r, "block_expectancies": [b.expectancy_per_unit for b in bl],
                        "drawdown_limit": None if max_drawdown_limit is None else str(max_drawdown_limit)})


def evaluate_family(variants: dict[str, list[Trade]], **kw) -> dict[str, EdgeVerdict]:
    """All variants ever tried, together: Holm over their p-values, so K is counted honestly."""
    k = len(variants)
    verdicts = {name: evaluate_variant(tr, k_variants=k, **kw) for name, tr in variants.items()}
    names = list(verdicts)
    rejected = holm_reject([verdicts[n].p_value for n in names], ALPHA)
    out = {}
    for name, rej in zip(names, rejected):
        v = verdicts[name]
        conds = dict(v.conditions)
        conds["significant"] = conds["significant"] and rej
        out[name] = EdgeVerdict(all(conds.values()), conds, v.n, v.n_min, v.p_be, v.p_hat, v.wilson_lower, v.p_value, v.detail)
    return out
