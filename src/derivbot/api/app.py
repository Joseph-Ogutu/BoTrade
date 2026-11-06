"""Admin API (HANDBOOK 6.1, 11.1): read-only status plus kill switch endpoints, bearer token required, bound to
127.0.0.1 only. Reach it through an SSH tunnel. It cannot change settings or enable real trading: it only holds the
kill switch and a read-only status callable."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import re
import secrets
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from derivbot.api.passwords import fingerprint, verify_password
from derivbot.killswitch.switch import KillSwitch

COOKIE = "botrade_session"
CSRF_HEADER, CSRF_VALUE = "x-requested-with", "botrade"

LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class KillBody(BaseModel):
    reason: str = Field(default="admin api", min_length=1, max_length=200)


class LoginBody(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class DashboardReleaseBody(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=3, max_length=200)


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


def client_ip(request: Request) -> str:
    """The caller's address. X-Forwarded-For is believed ONLY when the direct peer is a private or loopback address (the
    reverse proxy in front of us, which overwrites the header with the address it saw). A direct peer from anywhere else
    cannot choose its own identity."""
    peer = request.client.host if request.client else "unknown"
    try:
        trusted = ipaddress.ip_address(peer).is_private
    except ValueError:
        trusted = False
    forwarded = request.headers.get("x-forwarded-for", "")
    if trusted and forwarded:
        return forwarded.split(",")[-1].strip()[:64] or peer
    return peer


class AuthLimiter:
    """Locks an address out after too many failed authentications. In memory: a restart clears it, and that is acceptable
    because the token has 256 bits and the lockout only exists to make guessing and noisy scanning pointless."""

    def __init__(self, max_failures: int = 10, window_s: float = 600.0, lockout_s: float = 900.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.max, self.window, self.lockout, self._clock = max_failures, window_s, lockout_s, clock
        self._fails: dict[str, deque[float]] = defaultdict(deque)
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def retry_after(self, ip: str) -> int:
        """Seconds until this address may try again, 0 if it may try now."""
        with self._lock:
            until = self._locked_until.get(ip, 0.0)
            now = self._clock()
            if until > now:
                return int(until - now) + 1
            self._locked_until.pop(ip, None)
            return 0

    def failure(self, ip: str) -> None:
        with self._lock:
            now = self._clock()
            q = self._fails[ip]
            q.append(now)
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.max:
                self._locked_until[ip] = now + self.lockout
                q.clear()
            if len(self._fails) > 5000:                      # bound memory against address spraying
                self._fails.clear()

    def success(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)


class SessionStore:
    """Server-side login sessions. The cookie holds a random id; only its SHA-256 is kept here, so a memory dump does not
    reveal usable cookies. Sessions expire when idle and after an absolute limit, die with a restart, and stop working when
    the stored password hash changes (they carry its fingerprint)."""

    def __init__(self, idle_s: float = 2 * 3600, absolute_s: float = 8 * 3600, cap: int = 50,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.idle, self.absolute, self.cap, self._clock = idle_s, absolute_s, cap, clock
        self._s: dict[str, tuple[float, float, str]] = {}          # sha256(id) -> (created, last_seen, fingerprint)
        self._lock = threading.Lock()

    @staticmethod
    def _key(sid: str) -> str:
        return hashlib.sha256(sid.encode()).hexdigest()

    def create(self, fp: str) -> str:
        sid = secrets.token_urlsafe(32)
        with self._lock:
            now = self._clock()
            self._s = {k: v for k, v in self._s.items() if now - v[0] < self.absolute and now - v[1] < self.idle}
            while len(self._s) >= self.cap:
                self._s.pop(min(self._s, key=lambda k: self._s[k][0]))
            self._s[self._key(sid)] = (now, now, fp)
        return sid

    def valid(self, sid: str | None, fp: str | None) -> bool:
        if not sid or not fp:
            return False
        with self._lock:
            k = self._key(sid)
            rec = self._s.get(k)
            if rec is None:
                return False
            now = self._clock()
            if rec[2] != fp or now - rec[0] >= self.absolute or now - rec[1] >= self.idle:
                self._s.pop(k, None)
                return False
            self._s[k] = (rec[0], now, rec[2])
            return True

    def revoke(self, sid: str | None) -> None:
        if sid:
            with self._lock:
                self._s.pop(self._key(sid), None)


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
               summary_provider: Callable[[], dict[str, Any]] = lambda: {},
               limiter: AuthLimiter | None = None,
               password_hash: Callable[[], str | None] = lambda: None,
               sessions: SessionStore | None = None,
               trades_provider: Callable[[int, int], dict[str, Any]] = lambda page, size: {
                   "page": 1, "size": size, "total": 0, "pages": 1, "items": []}) -> FastAPI:
    """Two ways in: a bearer token (command line tools, fallback) or a password login that sets a session cookie."""
    app = FastAPI(title="derivbot admin", docs_url=None, redoc_url=None, openapi_url=None)
    page, csp = _csp_and_page()
    limiter = limiter or AuthLimiter()
    sessions = sessions or SessionStore()

    def auth(request: Request, authorization: str | None = Header(default=None)) -> str:
        """Returns how the caller got in: "bearer" or "session"."""
        stored = password_hash()
        if not admin_token and not stored:
            raise HTTPException(503, "no admin token or password configured")
        ip = client_ip(request)
        wait = limiter.retry_after(ip)
        if wait:
            raise HTTPException(429, "too many failed attempts", headers={"Retry-After": str(wait)})
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() == "bearer" and token:
            if admin_token and hmac.compare_digest(token.encode(), admin_token.encode()):
                limiter.success(ip)
                return "bearer"
            limiter.failure(ip)
            raise HTTPException(401, "invalid token", headers={"WWW-Authenticate": "Bearer"})
        if stored and sessions.valid(request.cookies.get(COOKIE), fingerprint(stored)):
            return "session"
        raise HTTPException(401, "sign in required", headers={"WWW-Authenticate": "Bearer"})   # no credentials: not a failed guess

    def auth_write(request: Request, via: str = Depends(auth)) -> str:
        """A cookie session can be ridden by another site, so a write through it also needs a header a cross-site form or
        image cannot send (and SameSite=Strict keeps the cookie from other sites in the first place)."""
        if via == "session" and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
            raise HTTPException(403, "missing request header")
        return via

    def auth_bearer_only(via: str = Depends(auth)) -> str:
        """Release is the dangerous direction: only the bearer token (command line on the server) may do it, never a browser."""
        if via != "bearer":
            raise HTTPException(403, "release needs the admin token")
        return via

    def secure_cookie(request: Request) -> bool:
        return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"

    @app.get("/api/auth-methods")
    def auth_methods() -> dict[str, bool]:
        """Which sign-in forms to offer. Public on purpose; it reveals nothing but that a password has been set."""
        return {"password": bool(password_hash()), "token": bool(admin_token)}

    @app.post("/api/login")
    def login(body: LoginBody, request: Request, response: Response) -> dict[str, bool]:
        stored = password_hash()
        if not stored:
            raise HTTPException(503, "password login is not set up")
        ip = client_ip(request)
        wait = limiter.retry_after(ip)
        if wait:
            raise HTTPException(429, "too many failed attempts", headers={"Retry-After": str(wait)})
        if not verify_password(body.password, stored):
            limiter.failure(ip)
            raise HTTPException(401, "wrong password")
        limiter.success(ip)
        response.set_cookie(COOKIE, sessions.create(fingerprint(stored)), max_age=int(sessions.absolute), httponly=True,
                            samesite="strict", secure=secure_cookie(request), path="/")
        return {"ok": True}

    @app.post("/api/logout")
    def logout(request: Request, response: Response) -> dict[str, bool]:
        sessions.revoke(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

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

    @app.get("/api/trades", dependencies=[Depends(auth)])
    def trades(page: int = Query(1, ge=1, le=100_000), size: int = Query(15, ge=1, le=100)) -> dict[str, Any]:
        return trades_provider(page, size)

    @app.get("/status", dependencies=[Depends(auth)])
    def status() -> dict[str, Any]:
        return {"kill_switch": kill_switch.status(), **status_provider()}

    @app.post("/kill", dependencies=[Depends(auth_write)])
    def kill(body: KillBody = KillBody()) -> dict[str, Any]:
        kill_switch.engage("api", body.reason)
        return {"engaged": True}

    @app.post("/api/release", dependencies=[Depends(auth_write)])
    def release_from_dashboard(body: DashboardReleaseBody, request: Request) -> dict[str, Any]:
        """Release the kill switch from the dashboard. Needs a signed-in session (or the token), the CSRF header AND the password typed
        again: a session left open on a shared screen, or hijacked, cannot restart trading on its own. A wrong password counts toward the
        same lockout as a wrong login. The release is logged like any other (it fails if it cannot be logged)."""
        stored = password_hash()
        if not stored:
            raise HTTPException(503, "password sign-in is not set up, so a release cannot be confirmed from the dashboard")
        ip = client_ip(request)
        wait = limiter.retry_after(ip)
        if wait:
            raise HTTPException(429, "too many failed attempts", headers={"Retry-After": str(wait)})
        if not verify_password(body.password, stored):
            limiter.failure(ip)
            raise HTTPException(401, "wrong password")
        limiter.success(ip)
        try:
            kill_switch.release("api", f"dashboard: {body.reason}")
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"engaged": kill_switch.is_engaged()}

    @app.post("/release", dependencies=[Depends(auth_bearer_only)])
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
