"""Endpoint allowlist. Only the exact production demo URL shape is accepted; the real URL is recognised only so it can
be REFUSED. A test policy for the fake server is an explicit object, never a default and never configurable by env."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

DEMO_PATH = "/trading/v1/options/ws/demo"
REAL_PATH = "/trading/v1/options/ws/real"        # recognised for refusal only


class RealTradingDisabled(Exception):
    pass


@dataclass(frozen=True)
class EndpointPolicy:
    scheme: str = "wss"
    host: str = "api.derivws.com"
    port: int | None = None


PROD_POLICY = EndpointPolicy()


def classify_url(url: str, policy: EndpointPolicy) -> str | None:
    """'demo', 'real' or None. None for anything that is not exactly the allowed scheme, host, port and path."""
    try:
        u = urlparse(url)
        port = u.port
    except ValueError:
        return None
    if (u.scheme != policy.scheme or u.hostname != policy.host or port != policy.port or u.username or u.password
            or u.fragment or u.params):
        return None
    if u.query and set(parse_qs(u.query, keep_blank_values=True)) - {"otp"}:
        return None
    return {DEMO_PATH: "demo", REAL_PATH: "real"}.get(u.path)


def build_ws_url(mode: str, policy: EndpointPolicy) -> str:
    """The demo URL (without an OTP). There is no code path that returns a real URL."""
    if mode != "demo":
        raise RealTradingDisabled("only the demo endpoint can be built")
    port = f":{policy.port}" if policy.port else ""
    return f"{policy.scheme}://{policy.host}{port}{DEMO_PATH}"
