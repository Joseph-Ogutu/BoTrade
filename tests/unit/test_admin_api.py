import pytest
from fastapi.testclient import TestClient

from derivbot.api.app import create_app, validate_bind_host
from derivbot.killswitch.switch import KillSwitch, MemoryEventLog
from derivbot.kv.store import MemoryStore

TOKEN = "FAKE-ADMIN-TOKEN-ABCDEF123456"
H = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture()
def env(tmp_path):
    ks = KillSwitch(MemoryStore(), MemoryEventLog(), tmp_path / "KILL")
    client = TestClient(create_app(ks, TOKEN, lambda: {"mode": "demo"}))
    return ks, client


def test_health_is_open_and_reveals_nothing(env):
    _, c = env
    r = c.get("/health")
    assert r.status_code == 200 and r.json() == {"ok": True}


@pytest.mark.parametrize("method,path", [("get", "/status"), ("post", "/kill"), ("post", "/release")])
def test_every_other_endpoint_needs_the_bearer_token(env, method, path):
    ks, c = env
    assert getattr(c, method)(path).status_code == 401
    assert getattr(c, method)(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert getattr(c, method)(path, headers={"Authorization": f"Basic {TOKEN}"}).status_code == 401
    assert getattr(c, method)(path, headers={"Authorization": "Bearer"}).status_code == 401
    assert not ks.is_engaged()


def test_unconfigured_token_means_503_never_open_access(tmp_path):
    ks = KillSwitch(MemoryStore(), MemoryEventLog(), tmp_path / "KILL")
    for token in (None, ""):
        c = TestClient(create_app(ks, token))
        assert c.post("/kill", headers=H).status_code == 503
        assert c.post("/kill").status_code == 503
    assert not ks.is_engaged()


def test_kill_engages_with_api_as_source(env):
    ks, c = env
    r = c.post("/kill", headers=H, json={"reason": "manual test"})
    assert r.status_code == 200 and ks.is_engaged() and ks.log.events[0] == ("engage", "api", "manual test")
    assert c.post("/kill", headers=H).status_code == 200            # default reason works too


def test_release_needs_a_real_reason_and_is_logged(env):
    ks, c = env
    ks.engage("api", "x")
    assert c.post("/release", headers=H, json={}).status_code == 422
    assert c.post("/release", headers=H, json={"reason": "ab"}).status_code == 422
    assert ks.is_engaged()
    r = c.post("/release", headers=H, json={"reason": "reviewed the audit log"})
    assert r.status_code == 200 and r.json() == {"engaged": False} and ks.log.events[-1][0] == "release"


def test_release_that_cannot_be_logged_returns_409_and_stays_engaged(tmp_path):
    from tests.unit.test_killswitch import BrokenLog

    log = BrokenLog()
    log.down = False
    ks = KillSwitch(MemoryStore(), log, tmp_path / "KILL")
    ks.engage("api", "x")
    log.down = True
    c = TestClient(create_app(ks, TOKEN))
    assert c.post("/release", headers=H, json={"reason": "should fail"}).status_code == 409
    assert ks.is_engaged()


def test_status_shows_state_and_no_secrets(env):
    _, c = env
    body = c.get("/status", headers=H).json()
    assert body["kill_switch"]["engaged"] is False and body["mode"] == "demo"
    assert TOKEN not in c.get("/status", headers=H).text


def test_no_docs_or_openapi_endpoints_are_exposed(env):
    _, c = env
    for p in ("/docs", "/redoc", "/openapi.json"):
        assert c.get(p).status_code == 404


def test_binding_to_anything_but_localhost_is_refused():
    for host in ("127.0.0.1", "::1", "localhost"):
        assert validate_bind_host(host) == host
    for host in ("0.0.0.0", "::", "192.168.1.5", "10.0.0.3", "example.com", ""):
        with pytest.raises(ValueError):
            validate_bind_host(host)


def test_admin_api_has_no_route_that_could_touch_trading_mode(env):
    ks, c = env
    paths = {r.path for r in c.app.routes}
    # /ui (static shell, no data) and /api/summary (read-only, bearer) were added with the dashboard on 2026-11-05.
    # /api/auth-methods, /api/login and /api/logout were added with the password login. Nothing here can change the trading mode, a setting or a credential; any further route must update this list on purpose.
    assert paths == {"/health", "/status", "/kill", "/release", "/ui", "/api/summary", "/api/auth-methods", "/api/login", "/api/logout", "/api/trades"}
