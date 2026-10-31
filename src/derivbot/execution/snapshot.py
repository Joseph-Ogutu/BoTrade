"""Remote state snapshot used by reconciliation and BUY_UNKNOWN resolution: open contracts (portfolio) plus recently
settled ones (profit_table). A contract that settled quickly is NOT in portfolio, so portfolio alone would wrongly call a
placed-and-settled buy NOT_PLACED (HANDBOOK 7 rule 2: portfolio, then statement or profit_table).

portfolio is limited to 30 calls a minute and profit_table to the same budget (docs/limits); callers must not poll fast.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from derivbot.deriv import messages as m
from derivbot.deriv.session import DerivSession


@dataclass(frozen=True)
class ReconcileSnapshot:
    portfolio: tuple[m.PortfolioContract, ...]
    settled: tuple[m.ProfitTransaction, ...]
    fetched_at_epoch: int
    problems: tuple[str, ...] = field(default_factory=tuple)


async def fetch_snapshot(session: DerivSession, now_epoch: int, timeout: float = 10.0, profit_limit: int = 50) -> ReconcileSnapshot:
    """Both requests must succeed; a partial snapshot would make "not found" meaningless, so errors propagate."""
    pf = m.parse_message(await session.request(m.req_portfolio(session.next_req_id()), timeout))
    pt = m.parse_message(await session.request(m.req_profit_table(session.next_req_id(), profit_limit), timeout))
    if not isinstance(pf, m.PortfolioMsg) or not isinstance(pt, m.ProfitTableMsg):
        raise ValueError("unexpected reply type to portfolio or profit_table")      # UNVERIFIED shapes on the demo endpoint
    return ReconcileSnapshot(tuple(pf.contracts), tuple(pt.transactions), now_epoch)
