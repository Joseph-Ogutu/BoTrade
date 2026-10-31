"""Admin API (HANDBOOK 6.1, 11.1): read-only status plus kill switch endpoints, bearer token required, bound to
127.0.0.1 only. Reach it through an SSH tunnel. It cannot change settings or enable real trading: it only holds the
kill switch and a read-only status callable."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from derivbot.killswitch.switch import KillSwitch

LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class KillBody(BaseModel):
    reason: str = Field(default="admin api", min_length=1, max_length=200)


class ReleaseBody(BaseModel):
    reason: str = Field(min_length=3, max_length=200)


def validate_bind_host(host: str) -> str:
    if host not in LOCAL_HOSTS:
        raise ValueError(f"the admin API may only bind to localhost, not {host!r}")
    return host


def create_app(kill_switch: KillSwitch, admin_token: str | None,
               status_provider: Callable[[], dict[str, Any]] = lambda: {}) -> FastAPI:
    app = FastAPI(title="derivbot admin", docs_url=None, redoc_url=None, openapi_url=None)

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
