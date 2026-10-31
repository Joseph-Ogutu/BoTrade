"""Tail probabilities without scipy: chi-square upper tail and standard normal upper tail.

The chi-square tail is the regularised upper incomplete gamma function Q(df/2, x/2), by the series for small x and the
continued fraction (modified Lentz) otherwise. Checked against scipy reference values in tests.
"""

from __future__ import annotations

import math

_EPS = 1e-15
_MAX_ITER = 10_000


def _gamma_q(a: float, x: float) -> float:
    if x <= 0.0:
        return 1.0
    log_prefactor = -x + a * math.log(x) - math.lgamma(a)
    if x < a + 1.0:  # series for P(a, x), then Q = 1 - P
        term = total = 1.0 / a
        ap = a
        for _ in range(_MAX_ITER):
            ap += 1.0
            term *= x / ap
            total += term
            if abs(term) < abs(total) * _EPS:
                break
        return max(0.0, 1.0 - total * math.exp(log_prefactor))
    # continued fraction for Q(a, x)
    tiny = 1e-300
    b = x + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, _MAX_ITER):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < _EPS:
            break
    return math.exp(log_prefactor) * h


def chi2_sf(x: float, df: int) -> float:
    if df < 1:
        raise ValueError("df must be at least 1")
    if x < 0:
        raise ValueError("x must be non-negative")
    return min(1.0, max(0.0, _gamma_q(df / 2.0, x / 2.0)))


def norm_sf(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2.0))
