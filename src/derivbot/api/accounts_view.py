"""View-only balances of the owner's Deriv accounts, for the dashboard.

READ ONLY and deliberately narrow:
- one HTTP GET, to one path (/trading/v1/options/accounts), with the DEMO token. No OTP, no WebSocket, no buy, no write
  endpoint. It cannot open a session on the real account, and nothing here can place or change anything.
- OBSERVED once on the live API (docs/api-notes.md): the reply lists every account the token can see, each with
  account_type "demo" or "real", a balance STRING, a currency and a status. A Trade token is not restricted to demo.
- The output keeps only account type, balance, currency and status. Account ids are dropped on purpose.
- The result is cached for ttl seconds so the dashboard refresh rate does not become the API request rate.
- UNVERIFIED: the rate limit of this endpoint, and its behaviour when the token has no real account.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from derivbot.common.redaction import redact_text, register_secret

PATH = "/trading/v1/options/accounts"      # the only path this module may call
BASE = "https://api.derivws.com"


def _money(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        return None
    try:
        d = Decimal(str(raw))
    except InvalidOperation:
        return None
    return float(d) if d.is_finite() else None


def parse_accounts(body: Any) -> dict[str, dict[str, Any] | None]:
    """{'demo': {...}|None, 'real': {...}|None} from one accounts reply. Anything unexpected is ignored, never guessed."""
    out: dict[str, dict[str, Any] | None] = {"demo": None, "real": None}
    rows = body.get("data") if isinstance(body, dict) else None
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        kind = row.get("account_type")
        if kind not in out or out[kind] is not None:
            continue
        out[kind] = {"balance": _money(row.get("balance")),
                     "currency": row["currency"] if isinstance(row.get("currency"), str) else None,
                     "status": row["status"] if isinstance(row.get("status"), str) else None}
    return out


class AccountsView:
    def __init__(self, app_id: str, pat: str, ttl_s: float = 30.0, base: str = BASE,
                 client: httpx.Client | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        register_secret(pat)                   # any error text that carries the token is redacted
        self._app_id, self._pat, self._base, self._ttl, self._clock = app_id, pat, base, ttl_s, clock
        self._client = client or httpx.Client(timeout=8.0)
        self._lock = threading.Lock()
        self._at: float | None = None
        self._last: dict[str, Any] = {"demo": None, "real": None, "error": "not fetched yet"}

    def get(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            if self._at is not None and now - self._at < self._ttl:
                return self._last
            self._at = now                     # a failure is also cached, so an outage is not hammered
            try:
                r = self._client.get(self._base + PATH, headers={
                    "Authorization": f"Bearer {self._pat}", "Deriv-App-ID": self._app_id, "Accept": "application/json"})
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")
                self._last = {**parse_accounts(r.json()), "error": None, "fetched_at_epoch": int(time.time())}
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                self._last = {**{k: self._last.get(k) for k in ("demo", "real")},
                              "error": redact_text(f"{type(exc).__name__}: {exc}")[:120],
                              "fetched_at_epoch": self._last.get("fetched_at_epoch")}
            return self._last
