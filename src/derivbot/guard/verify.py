"""Assert demo-ness at connect (kill-criterion rules, HANDBOOK 10.1 check 2, 11.4).

After every (re)connect, BEFORE the session is published to the executor, the verifier asks for the account (balance) and
requires: the endpoint was classified as the demo endpoint, AND the loginid Deriv reports equals the configured demo
account id, AND the loginid does not look like a legacy real-money id. Anything unknown fails closed.

UNVERIFIED: that balance reports loginid on the demo endpoint, and the legacy real-money loginid prefixes below (taken from
the legacy API; the new API's formats are unknown). The primary evidence is the URL classification plus exact loginid equality.
"""

from __future__ import annotations

import re

from derivbot.deriv import messages as m
from derivbot.deriv.session import DerivSession
from derivbot.execution.executor import ConnInfo
from derivbot.guard.otp import RealModeRefused

LEGACY_REAL_LOGINID = re.compile(r"^(CR|MF|MLT|MX)\d")        # UNVERIFIED prefixes


def assess(endpoint_mode: str | None, loginid: str | None, expected_loginid: str) -> tuple[str | None, bool | None]:
    """(endpoint_mode, account_is_demo). account_is_demo is True only when everything agrees; None when unknown."""
    if endpoint_mode == "real":
        return "real", False
    if endpoint_mode != "demo":
        return None, None
    if loginid is None:
        return "demo", None
    if loginid != expected_loginid or LEGACY_REAL_LOGINID.match(loginid):
        return "demo", False
    return "demo", True


class DemoConnectionVerifier:
    def __init__(self, expected_loginid: str, request_timeout: float = 10.0) -> None:
        self._expected, self._timeout = expected_loginid, request_timeout
        self.info = ConnInfo(None, None, None)

    def get_info(self) -> ConnInfo:
        return self.info

    async def on_connected(self, session: DerivSession) -> None:
        self.info = ConnInfo(None, None, None)                    # unknown until proven, on every connect
        bal = m.parse_message(await session.request(m.req_balance(session.next_req_id(), subscribe=False), self._timeout))
        if not isinstance(bal, m.BalanceMsg):
            raise RealModeRefused("could not determine the account: unexpected reply to balance")
        endpoint, is_demo = assess(session.endpoint_mode, bal.balance.loginid, self._expected)
        if endpoint != "demo" or is_demo is not True:
            raise RealModeRefused(f"connection is not a confirmed demo account (endpoint={endpoint}, demo_account={is_demo})")
        self.info = ConnInfo("demo", True, bal.balance.balance, bal.balance.currency, bal.balance.loginid)

    async def on_disconnected(self) -> None:
        self.info = ConnInfo(None, None, self.info.balance, self.info.currency, self.info.loginid)

    def update_balance(self, balance, currency: str | None = None) -> None:
        self.info = ConnInfo(self.info.endpoint_mode, self.info.account_is_demo, balance, currency or self.info.currency,
                             self.info.loginid)
