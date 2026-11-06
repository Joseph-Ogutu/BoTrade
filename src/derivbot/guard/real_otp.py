"""OTP URL provider for the REAL account. It exists only for real mode and cannot be built without proof that the real-money guard
resolved to REAL (a ModeDecision whose mode is Mode.REAL, which needs all five conditions of HANDBOOK 11.4).

Mirror image of derivbot.guard.otp: it reads the REAL credentials only, and the URL that comes back must classify as exactly the real
endpoint. A demo URL, or anything else, is discarded and RealModeRefused is raised, so a mix-up in either direction never connects.
The OTP URL is a secret and is never logged. UNVERIFIED: this has never run against the real account (nothing has been funded or
approved); the request shape is the one observed on the demo account.
"""

from __future__ import annotations

from typing import Any

import httpx

from derivbot.common.redaction import redact_text, register_secret
from derivbot.config.settings import Settings
from derivbot.guard.endpoint import PROD_POLICY, EndpointPolicy, classify_url
from derivbot.guard.otp import ACCOUNT_ID, OtpError, RealModeRefused
from derivbot.guard.real_guard import ModeDecision


class RealOtpProvider:
    def __init__(self, decision: ModeDecision, *, app_id: str, pat: str, account_id: str, policy: EndpointPolicy = PROD_POLICY,
                 client: httpx.AsyncClient | None = None, rest_base: str = "https://api.derivws.com") -> None:
        if not isinstance(decision, ModeDecision) or not decision.real_unlocked:
            raise RealModeRefused("the real OTP provider needs a REAL decision issued by the real-money guard")
        if not ACCOUNT_ID.match(account_id):
            raise OtpError("account id has unexpected characters")
        register_secret(pat)
        self._app_id, self._pat, self._account, self._policy, self._base = app_id, pat, account_id, policy, rest_base
        self._client = client or httpx.AsyncClient(timeout=15.0)

    @classmethod
    def from_settings(cls, decision: ModeDecision, s: Settings, **kw: Any) -> RealOtpProvider:
        """Uses ONLY the real credentials, and only for a decision of REAL."""
        if not (s.deriv_app_id_real and s.deriv_pat_real and s.deriv_account_id_real):
            raise OtpError("real credentials are not configured")
        return cls(decision, app_id=s.deriv_app_id_real, pat=s.deriv_pat_real.get_secret_value(), account_id=s.deriv_account_id_real, **kw)

    async def get_url(self) -> str:
        try:
            r = await self._client.post(
                f"{self._base}/trading/v1/options/accounts/{self._account}/otp",
                headers={"Authorization": f"Bearer {self._pat}", "Deriv-App-ID": self._app_id, "Content-Type": "application/json"})
        except httpx.HTTPError as exc:
            raise OtpError(redact_text(f"otp request failed: {type(exc).__name__}")) from None
        if r.status_code in (401, 403):
            raise OtpError(f"otp request refused with HTTP {r.status_code}")
        if r.status_code >= 300:
            raise OtpError(f"otp request failed with HTTP {r.status_code}")
        try:
            url = r.json()["data"]["url"]
        except (ValueError, KeyError, TypeError):
            raise OtpError("otp response has no data.url") from None
        if not isinstance(url, str) or classify_url(url, self._policy) != "real":
            raise RealModeRefused("the OTP url is not the exact real endpoint: refusing to connect")
        register_secret(url)
        return url


def real_account_id(decision: ModeDecision, s: Settings) -> str:
    """The configured REAL account id, only for a genuine REAL decision (keeps every read of the real credentials inside this package)."""
    if not isinstance(decision, ModeDecision) or not decision.real_unlocked:
        raise RealModeRefused("the real account id is only available to a REAL decision issued by the real-money guard")
    if not s.deriv_account_id_real:
        raise OtpError("the real account id is not configured")
    return s.deriv_account_id_real


def real_view_credentials(decision: ModeDecision, s: Settings) -> tuple[str, str]:
    """(app id, PAT) for the READ-ONLY accounts view in a real process. The accounts list is the only thing it is used for."""
    if not isinstance(decision, ModeDecision) or not decision.real_unlocked:
        raise RealModeRefused("real credentials are only available to a REAL decision issued by the real-money guard")
    if not (s.deriv_app_id_real and s.deriv_pat_real):
        raise OtpError("real credentials are not configured")
    return s.deriv_app_id_real, s.deriv_pat_real.get_secret_value()
