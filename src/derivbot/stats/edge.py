"""Edge-test statistics defined by docs/kill-criterion.md.

Pure functions, standard library only. No data access, no network.
"""

from __future__ import annotations

import math
from statistics import NormalDist

_NORM = NormalDist()


def breakeven_p(r: float) -> float:
    """Break-even win probability for profit ratio r = (payout - stake) / stake."""
    if r <= 0:
        raise ValueError("r must be positive")
    return 1.0 / (1.0 + r)


def expectancy(p: float, r: float) -> float:
    """Expected profit per unit stake: p * r - (1 - p)."""
    return p * r - (1.0 - p)


def wilson_lower(x: int, n: int, confidence: float = 0.95, one_sided: bool = True) -> float:
    """Wilson score lower bound for a binomial proportion.

    one_sided=True uses z = Phi^-1(confidence). one_sided=False uses the
    two-sided interval, z = Phi^-1((1 + confidence) / 2).
    """
    if n == 0:
        return 0.0
    if not 0 <= x <= n:
        raise ValueError("x must be between 0 and n")
    q = confidence if one_sided else (1.0 + confidence) / 2.0
    z = _NORM.inv_cdf(q)
    phat = x / n
    denom = 1.0 + z * z / n
    centre = phat + z * z / (2.0 * n)
    margin = z * math.sqrt(phat * (1.0 - phat) / n + z * z / (4.0 * n * n))
    return max(0.0, (centre - margin) / denom)


def binomial_sf(k: int, n: int, p: float) -> float:
    """Exact P(X >= k) for X ~ Binomial(n, p), summed in log space."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    if p <= 0.0:
        return 0.0
    if p >= 1.0:
        return 1.0
    log_p, log_q = math.log(p), math.log1p(-p)
    log_terms = [
        math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * log_p + (n - i) * log_q
        for i in range(k, n + 1)
    ]
    top = max(log_terms)
    total = math.exp(top) * sum(math.exp(t - top) for t in log_terms)
    return min(1.0, total)


def required_n(p0: float, delta: float, alpha: float, power: float) -> int:
    """Trades needed to detect p1 = p0 + delta over H0: p = p0, one-sided.

    n = ceil((z_alpha*sqrt(p0 q0) + z_beta*sqrt(p1 q1))^2 / delta^2)
    """
    p1 = p0 + delta
    if not (0 < p0 < 1 and 0 < p1 < 1 and delta > 0):
        raise ValueError("need 0 < p0 < p1 < 1")
    z_alpha = _NORM.inv_cdf(1.0 - alpha)
    z_beta = _NORM.inv_cdf(power)
    num = z_alpha * math.sqrt(p0 * (1.0 - p0)) + z_beta * math.sqrt(p1 * (1.0 - p1))
    return math.ceil(num * num / (delta * delta))


def holm_reject(pvalues: list[float], alpha: float = 0.05) -> list[bool]:
    """Holm step-down procedure. Returns rejections in the input order."""
    m = len(pvalues)
    order = sorted(range(m), key=lambda i: pvalues[i])
    reject = [False] * m
    for rank, i in enumerate(order):
        if pvalues[i] <= alpha / (m - rank):
            reject[i] = True
        else:
            break
    return reject
