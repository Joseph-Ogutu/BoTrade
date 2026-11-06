"""Records the demo balance every few minutes so the dashboard can draw a trend even when no trade has happened.

Only a balance the connect-time verification has proven to belong to the configured DEMO account is stored (endpoint demo,
account demo). Unknown or unverified means nothing is written. A database error is logged and skipped, never raised: a
missing history point must not stop the service.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from sqlalchemy import Engine

from derivbot.db import repo
from derivbot.execution.executor import ConnInfo

log = logging.getLogger(__name__)


class BalanceHistory:
    def __init__(self, engine: Engine, get_info: Callable[[], ConnInfo], account_id: str, interval_s: float = 300.0,
                 mode: str = "demo") -> None:
        self.engine, self.get_info, self.account_id, self.interval_s, self.mode = engine, get_info, account_id, interval_s, mode
        self.retry_s = 10.0

    def record_once(self) -> bool:
        info = self.get_info()
        proven = (info.endpoint_mode == "demo" and info.account_is_demo is True) if self.mode == "demo" else \
                 (self.mode == "real" and info.endpoint_mode == "real" and info.account_is_demo is False)
        if info.balance is None or not info.currency or not proven:
            return False
        try:
            with self.engine.begin() as conn:
                repo.record_balance(conn, mode=self.mode, account_id=self.account_id, balance=info.balance, currency=info.currency)
        except Exception as exc:  # noqa: BLE001
            log.warning("balance history write failed: %s", type(exc).__name__)
            return False
        return True

    async def run(self, halt: asyncio.Event) -> None:
        while not halt.is_set():
            wrote = await asyncio.to_thread(self.record_once)
            try:                                  # until the first point exists, look again soon (the session may still be connecting)
                await asyncio.wait_for(halt.wait(), timeout=self.interval_s if wrote else self.retry_s)
            except TimeoutError:
                pass
