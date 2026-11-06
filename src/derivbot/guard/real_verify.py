"""Assert real-ness at connect, the mirror of derivbot.guard.verify (which refuses everything that is not a confirmed demo account).

After every (re)connect, BEFORE the session is published to the executor, the verifier asks for the account (balance) and requires:
the endpoint was classified as the REAL endpoint, AND the loginid Deriv reports equals the configured real account id, AND that loginid
does not look like a demo account. Anything unknown fails closed. A connection that is not exactly the configured real account is
refused and never published.

It cannot be constructed without proof that the real-money guard resolved to REAL.
"""

from __future__ import annotations

import re

from derivbot.deriv import messages as m
from derivbot.deriv.session import DerivSession
from derivbot.execution.executor import ConnInfo
from derivbot.guard.otp import RealModeRefused
from derivbot.guard.real_guard import ModeDecision

DEMO_LOGINID = re.compile(r"^(VRT|VRTC|DOT)")            # demo-looking ids (observed DOT on the new API, VRTC on the legacy one)


def assess_real(endpoint_mode: str | None, loginid: str | None, expected_loginid: str) -> tuple[str | None, bool]:
    """(endpoint_mode, confirmed_real). confirmed_real is True only when the endpoint is real AND the loginid is exactly the configured
    real account AND it does not look like a demo account."""
    if endpoint_mode != "real":
        return endpoint_mode, False
    if not loginid or not expected_loginid or loginid != expected_loginid or DEMO_LOGINID.match(loginid):
        return "real", False
    return "real", True


class RealConnectionVerifier:
    def __init__(self, decision: ModeDecision, expected_loginid: str, request_timeout: float = 10.0) -> None:
        if not isinstance(decision, ModeDecision) or not decision.real_unlocked:
            raise RealModeRefused("the real connection verifier needs a REAL decision issued by the real-money guard")
        self._expected, self._timeout = expected_loginid, request_timeout
        self.info = ConnInfo(None, None, None)

    def get_info(self) -> ConnInfo:
        return self.info

    async def on_connected(self, session: DerivSession) -> None:
        self.info = ConnInfo(None, None, None)                        # unknown until proven, on every connect
        bal = m.parse_message(await session.request(m.req_balance(session.next_req_id(), subscribe=False), self._timeout))
        if not isinstance(bal, m.BalanceMsg):
            raise RealModeRefused("could not determine the account: unexpected reply to balance")
        endpoint, confirmed = assess_real(session.endpoint_mode, bal.balance.loginid, self._expected)
        if endpoint != "real" or not confirmed:
            raise RealModeRefused(f"connection is not the configured real account (endpoint={endpoint}, confirmed_real={confirmed})")
        # ConnInfo.account_is_demo is False here: the account is confirmed to be the real one
        self.info = ConnInfo("real", False, bal.balance.balance, bal.balance.currency, bal.balance.loginid)

    async def on_disconnected(self) -> None:
        self.info = ConnInfo(None, None, self.info.balance, self.info.currency, self.info.loginid)

    def update_balance(self, balance, currency: str | None = None) -> None:
        self.info = ConnInfo(self.info.endpoint_mode, self.info.account_is_demo, balance, currency or self.info.currency, self.info.loginid)
