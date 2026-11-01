"""Payout model parameterised from stored proposal quotes (HANDBOOK 9.2). Never a hard-coded ratio.

r = (payout - stake) / stake. The ratio depends on the stake (public quotes: 0.95 at 1.00 but 0.886 at the 0.35 minimum),
so the default mode is "worst": the lowest ratio observed for the contract, which is conservative.
Unknown contract types raise: there is no default payout.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class Quote:
    contract_type: str
    stake: Decimal
    payout: Decimal

    @property
    def r(self) -> Decimal:
        return (self.payout - self.stake) / self.stake


class PayoutModel:
    def __init__(self, quotes: list[Quote], mode: str = "worst", shift: Decimal = Decimal(0)) -> None:
        if mode not in ("worst", "nearest"):
            raise ValueError("mode must be 'worst' or 'nearest'")
        if not quotes:
            raise ValueError("a payout model needs at least one quote")
        if any(q.stake <= 0 or q.payout <= 0 for q in quotes):
            raise ValueError("quotes must have positive stake and payout")
        self.quotes, self.mode, self.shift = list(quotes), mode, shift

    @classmethod
    def from_quotes(cls, quotes: list[Quote], mode: str = "worst") -> PayoutModel:
        return cls(quotes, mode)

    @classmethod
    def from_evidence_batch(cls, path: str | Path, mode: str = "worst") -> PayoutModel:
        """Read proposal quotes (payouts only, no tick data) from a stamped probe-batch evidence file."""
        data = json.loads(Path(path).read_text())["responses"]
        quotes = []
        for key, resp in data.items():
            parts = key.split(":")
            if "proposal" in resp and len(parts) >= 2 and parts[1].startswith(("DIGIT", "CALL", "PUT")):
                p = resp["proposal"]
                quotes.append(Quote(parts[1], Decimal(str(p["ask_price"])), Decimal(str(p["payout"]))))
        return cls(quotes, mode)

    def r(self, contract_type: str, stake: Decimal = Decimal(1)) -> Decimal:
        qs = [q for q in self.quotes if q.contract_type == contract_type]
        if not qs:
            raise KeyError(f"no stored proposal quote for {contract_type}: refusing to assume a payout")
        base = min(q.r for q in qs) if self.mode == "worst" else min(qs, key=lambda q: abs(q.stake - stake)).r
        return base - self.shift

    def shifted(self, delta: Decimal) -> PayoutModel:
        return PayoutModel(self.quotes, self.mode, self.shift + delta)

    def to_json(self) -> dict:
        return {"mode": self.mode, "shift": str(self.shift),
                "quotes": [{"contract_type": q.contract_type, "stake": str(q.stake), "payout": str(q.payout)} for q in self.quotes]}
