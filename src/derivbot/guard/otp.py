"""OTP URL provider (REST). UNVERIFIED: it has never run, because no demo App ID or PAT exists yet; the request shape is
from docs/api-notes.md (POST /trading/v1/options/accounts/{accountId}/otp, headers Authorization Bearer, Deriv-App-ID,
response data.url).

Safety: the provider is built from the DEMO credentials only, and it classifies the URL that comes back. If Deriv returns
anything other than the exact demo URL (including the real URL, which is what a real account id would produce) the URL is
discarded and RealModeRefused is raised, so the session never connects. The OTP URL is a secret and is never logged.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from derivbot.common.redaction import redact_text, register_secret
from derivbot.config.settings import Settings
from derivbot.guard.endpoint import PROD_POLICY, EndpointPolicy, classify_url

log = logging.getLogger(__name__)
ACCOUNT_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class OtpError(Exception):
    pass


class RealModeRefused(Exception):
    pass


class HttpOtpProvider:
    def __init__(self, *, app_id: str, pat: str, account_id: str, policy: EndpointPolicy = PROD_POLICY,
                 client: httpx.AsyncClient | None = None, rest_base: str = "https://api.derivws.com") -> None:
        if not ACCOUNT_ID.match(account_id):
            raise OtpError("account id has unexpected characters")
        register_secret(pat)
        self._app_id, self._pat, self._account, self._policy, self._base = app_id, pat, account_id, policy, rest_base
        self._client = client or httpx.AsyncClient(timeout=15.0)

    @classmethod
    def from_settings(cls, s: Settings, **kw: Any) -> HttpOtpProvider:
        """Uses ONLY the demo credentials; the real ones are never read here, whatever else is configured."""
        if not (s.deriv_app_id_demo and s.deriv_pat_demo and s.deriv_account_id_demo):
            raise OtpError("demo credentials are not configured")
        return cls(app_id=s.deriv_app_id_demo, pat=s.deriv_pat_demo.get_secret_value(), account_id=s.deriv_account_id_demo, **kw)

    async def get_url(self) -> str:
        try:
            r = await self._client.post(
                f"{self._base}/trading/v1/options/accounts/{self._account}/otp",
                headers={"Authorization": f"Bearer {self._pat}", "Deriv-App-ID": self._app_id, "Content-Type": "application/json"})
        except httpx.HTTPError as exc:
            raise OtpError(redact_text(f"otp request failed: {type(exc).__name__}")) from None
        if r.status_code in (401, 403):
            # docs: 401 missing/invalid token or legacy App ID, 403 missing scope (UNVERIFIED on the live API)
            raise OtpError(f"otp request refused with HTTP {r.status_code}")
        if r.status_code >= 300:
            raise OtpError(f"otp request failed with HTTP {r.status_code}")
        try:
            url = r.json()["data"]["url"]
        except (ValueError, KeyError, TypeError):
            raise OtpError("otp response has no data.url") from None
        if not isinstance(url, str) or classify_url(url, self._policy) != "demo":
            raise RealModeRefused("the OTP url is not the exact demo endpoint: refusing to connect")
        register_secret(url)
        return url
