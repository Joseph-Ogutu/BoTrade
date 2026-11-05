"""Admin API (HANDBOOK 6.1, 11.1): read-only status plus kill switch endpoints, bearer token required, bound to
127.0.0.1 only. Reach it through an SSH tunnel. It cannot change settings or enable real trading: it only holds the
kill switch and a read-only status callable."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from derivbot.killswitch.switch import KillSwitch

LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class KillBody(BaseModel):
    reason: str = Field(default="admin api", min_length=1, max_length=200)


class ReleaseBody(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


# Inside a container "localhost" is unreachable from the host, so a container may bind all of ITS interfaces, but only when
# the deployment says so explicitly, and compose then publishes the port on the HOST's 127.0.0.1 only.
CONTAINER_BIND = "0.0.0.0"  # noqa: S104


def validate_bind_host(host: str, container: bool = False) -> str:
    if host in LOCAL_HOSTS or (container and host == CONTAINER_BIND):
        return host
    raise ValueError(f"the admin API may only bind to localhost, not {host!r}")


STATIC = Path(__file__).parent / "static"


def _csp_and_page() -> tuple[str, str]:
    """The page and a Content-Security-Policy that allows exactly its one inline style and one inline script, by hash."""
    html = (STATIC / "dashboard.html").read_text(encoding="utf-8")

    def h(tag: str) -> str:
        m = re.search(rf"<{tag}>(.*?)</{tag}>", html, re.S)
        assert m, tag
        return "'sha256-" + base64.b64encode(hashlib.sha256(m.group(1).encode()).digest()).decode() + "'"

    csp = (f"default-src 'none'; script-src {h('script')}; style-src {h('style')}; connect-src 'self'; img-src 'self' data:; "
           "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
    return html, csp


def create_app(kill_switch: KillSwitch, admin_token: str | None,
               status_provider: Callable[[], dict[str, Any]] = lambda: {},
               summary_provider: Callable[[], dict[str, Any]] = lambda: {}) -> FastAPI:
    app = FastAPI(title="derivbot admin", docs_url=None, redoc_url=None, openapi_url=None)
    page, csp = _csp_and_page()

    def auth(authorization: str | None = Header(default=None)) -> None:
        if not admin_token:
            raise HTTPException(503, "admin token not configured")
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(401, "bearer token required", headers={"WWW-Authenticate": "Bearer"})
        if not hmac.compare_digest(token.encode(), admin_token.encode()):
            raise HTTPException(401, "invalid token", headers={"WWW-Authenticate": "Bearer"})

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/ui", response_class=HTMLResponse)
    def ui() -> HTMLResponse:
        """The dashboard shell. It carries no data: every number comes from /api/summary, which needs the bearer token."""
        return HTMLResponse(page, headers={
            "Content-Security-Policy": csp, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"})

    @app.get("/api/summary", dependencies=[Depends(auth)])
    def summary() -> dict[str, Any]:
        return {"kill_switch": kill_switch.status(), **status_provider(), **summary_provider()}

    @app.get("/status", dependencies=[Depends(auth)])
    def status() -> dict[str, Any]:
        return {"kill_switch": kill_switch.status(), **status_provider()}

    @app.post("/kill", dependencies=[Depends(auth)])
    def kill(body: KillBody = KillBody()) -> dict[str, Any]:
        kill_switch.engage("api", body.reason)
        return {"engaged": True}

    @app.post("/release", dependencies=[Depends(auth)])
    def release(body: ReleaseBody) -> dict[str, Any]:
        try:
            kill_switch.release("api", body.reason)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"engaged": kill_switch.is_engaged()}

    return app


def serve(app: FastAPI, host: str = "127.0.0.1", port: int = 8787) -> None:  # pragma: no cover - thin wrapper
    import uvicorn

    uvicorn.run(app, host=validate_bind_host(host), port=port, log_level="warning")
