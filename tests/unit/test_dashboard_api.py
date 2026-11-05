import base64
import hashlib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from derivbot.api.app import CONTAINER_BIND, STATIC, create_app, validate_bind_host
from derivbot.killswitch.switch import KillSwitch, MemoryEventLog
from derivbot.kv.store import MemoryStore

TOKEN = "FAKE-ADMIN-TOKEN-ABCDEF123456"
HTML = (STATIC / "dashboard.html").read_text()


def client(tmp_path, token=TOKEN):
    ks = KillSwitch(MemoryStore(), MemoryEventLog(), tmp_path / "KILL")
    app = create_app(ks, token, lambda: {"mode": "demo", "session_connected": True},
                     lambda: {"today": {"pnl": 1.5}, "recent_trades": []})
    return TestClient(app), ks


def test_ui_page_is_public_but_carries_no_data(tmp_path):
    c, _ = client(tmp_path)
    r = c.get("/ui")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert TOKEN not in r.text and r.text == c.get("/ui").text == HTML          # a static shell: same bytes every time
    assert r.headers["cache-control"] == "no-store" and r.headers["x-frame-options"] == "DENY"


def test_csp_allows_exactly_the_one_script_and_one_style_by_hash_and_nothing_else(tmp_path):
    csp = client(tmp_path)[0].get("/ui").headers["content-security-policy"]
    for tag, directive in (("script", "script-src"), ("style", "style-src")):
        body = re.search(rf"<{tag}>(.*?)</{tag}>", HTML, re.S).group(1)
        want = "'sha256-" + base64.b64encode(hashlib.sha256(body.encode()).digest()).decode() + "'"
        assert f"{directive} {want}" in csp
    assert "default-src 'none'" in csp and "connect-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp and "http" not in csp


def test_page_has_no_external_resources_no_inline_handlers_and_no_unsafe_dom_writes():
    assert not re.search(r"(src|href)\s*=\s*[\"']?(https?:)?//", HTML)
    assert not re.search(r"\son[a-z]+\s*=", HTML)                 # no inline event handlers
    assert not re.search(r"\sstyle\s*=", HTML)                    # the CSP forbids inline style attributes
    js = re.search(r"<script>(.*?)</script>", HTML, re.S).group(1)
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setAttribute(\"style\""):
        assert banned not in js, banned


def test_the_page_offers_no_release_and_its_only_writes_are_kill_login_and_logout():
    js = re.search(r"<script>(.*?)</script>", HTML, re.S).group(1)
    assert "/release" not in js
    assert set(re.findall(r"api\(\"([^\"]+)\"", js)) == {"/status", "/api/summary", "/kill", "/api/trades?page="}
    assert set(re.findall(r"fetch\(\"([^\"]+)\"", js)) == {"/api/auth-methods", "/api/login", "/api/logout"}
    assert re.findall(r"method:\"([A-Z]+)\"", js) == ["POST", "POST", "POST"]          # kill, logout, login: no PUT, no DELETE
    assert "document.cookie" not in js                                                  # the session cookie is HttpOnly anyway
    # localStorage may hold the theme choice and nothing else: never a token, a password or any account data
    assert set(re.findall(r"localStorage\.(?:get|set)Item\(\"([^\"]+)\"", js)) == {"bt_theme"}
    assert len(re.findall(r"localStorage", js)) == len(re.findall(r"localStorage\.(?:get|set)Item\(\"bt_theme\"", js))
    assert re.findall(r"sessionStorage\.setItem\(KEY,token\)", js)                    # the token is per tab only


def test_summary_needs_the_bearer_token(tmp_path):
    c, _ = client(tmp_path)
    assert c.get("/api/summary").status_code == 401
    assert c.get("/api/summary", headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = c.get("/api/summary", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "demo" and body["today"]["pnl"] == 1.5 and body["kill_switch"]["engaged"] is False


def test_summary_is_503_when_no_token_is_configured(tmp_path):
    assert client(tmp_path, token=None)[0].get("/api/summary").status_code == 503


def test_kill_from_the_page_engages_and_the_summary_shows_it(tmp_path):
    c, ks = client(tmp_path)
    h = {"Authorization": f"Bearer {TOKEN}"}
    assert c.post("/kill", json={"reason": "dashboard"}, headers=h).status_code == 200
    assert ks.is_engaged() and c.get("/api/summary", headers=h).json()["kill_switch"]["engaged"] is True


def test_bind_rules_loopback_always_and_all_interfaces_only_for_a_container():
    assert validate_bind_host("127.0.0.1") == "127.0.0.1"
    with pytest.raises(ValueError):
        validate_bind_host(CONTAINER_BIND)
    assert validate_bind_host(CONTAINER_BIND, container=True) == CONTAINER_BIND
    for bad in ("10.0.0.5", "192.168.1.2", "", "example.com"):
        with pytest.raises(ValueError):
            validate_bind_host(bad, container=True)


def test_compose_publishes_the_api_on_the_host_loopback_only():
    compose = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text()
    ports = re.findall(r"^\s*-\s*\"?([0-9.:]+8787[:0-9]*)\"?\s*$", compose, re.M)
    assert ports == ["127.0.0.1:8787:8787"]
    assert "ADMIN_API_BIND: container" in compose


# ---------------------------------------------------------------- lockout after failed logins

from derivbot.api.app import AuthLimiter, client_ip  # noqa: E402


def test_limiter_locks_out_after_the_limit_then_releases():
    t = {"v": 0.0}
    lim = AuthLimiter(max_failures=3, window_s=60, lockout_s=100, clock=lambda: t["v"])
    for _ in range(2):
        lim.failure("1.2.3.4")
    assert lim.retry_after("1.2.3.4") == 0
    lim.failure("1.2.3.4")
    assert 0 < lim.retry_after("1.2.3.4") <= 101 and lim.retry_after("9.9.9.9") == 0
    t["v"] = 101.0
    assert lim.retry_after("1.2.3.4") == 0


def test_old_failures_age_out_of_the_window():
    t = {"v": 0.0}
    lim = AuthLimiter(max_failures=3, window_s=60, lockout_s=100, clock=lambda: t["v"])
    lim.failure("a"); lim.failure("a")  # noqa: E702
    t["v"] = 61.0
    lim.failure("a")
    assert lim.retry_after("a") == 0


def test_the_api_returns_429_with_retry_after_after_ten_bad_tokens_and_good_tokens_still_work_for_others(tmp_path):
    ks = KillSwitch(MemoryStore(), MemoryEventLog(), tmp_path / "KILL")
    c = TestClient(create_app(ks, TOKEN))
    bad = {"Authorization": "Bearer wrong"}
    for _ in range(10):
        assert c.get("/status", headers=bad, ).status_code == 401
    r = c.get("/status", headers=bad)
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    # the locked address is refused even with the CORRECT token
    assert c.get("/status", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 429
    # a different address (through the proxy header, since the direct peer here is private/testclient) is unaffected
    other = c.get("/status", headers={"Authorization": f"Bearer {TOKEN}", "X-Forwarded-For": "8.8.8.8"})
    assert other.status_code in (200, 429)


class _Req:
    def __init__(self, peer, xff=None):
        self.client = type("C", (), {"host": peer})()
        self.headers = {"x-forwarded-for": xff} if xff else {}


def test_forwarded_for_is_believed_only_from_a_private_peer():
    assert client_ip(_Req("172.18.0.5", "93.184.216.34")) == "93.184.216.34"       # the proxy container
    assert client_ip(_Req("127.0.0.1", "93.184.216.34")) == "93.184.216.34"
    assert client_ip(_Req("8.8.8.8", "93.184.216.34")) == "8.8.8.8"                # a public peer cannot choose its identity
    assert client_ip(_Req("172.18.0.5")) == "172.18.0.5"
    assert client_ip(_Req("172.18.0.5", "1.1.1.1, 93.184.216.34")) == "93.184.216.34"   # the proxy's own entry is the last one


def test_public_front_is_opt_in_blocks_release_and_overwrites_the_forwarded_address():
    root = Path(__file__).resolve().parents[2]
    compose = (root / "docker-compose.yml").read_text()
    block = compose.split("  caddy:\n", 1)[1].split("\n  collector:", 1)[0]
    assert 'profiles: ["public"]' in block                       # not started unless the owner asks for it
    assert re.findall(r'^\s*-\s*"([0-9.:]*\d+:\d+)"\s*$', block, re.M) == ["80:80", "443:443"]
    assert "cap_add: [NET_BIND_SERVICE]" in block and "bot:" in block.split("depends_on:")[1]
    # nothing else may publish a public port: every other published port is host-loopback only
    others = compose.replace(block, "")
    assert all(p.startswith("127.0.0.1:") for p in re.findall(r'^\s*-\s*"([0-9.:]+:\d+)"\s*$', others, re.M))
    cf = (root / "deploy" / "Caddyfile").read_text()
    assert "/release" in cf and "respond @private 404" in cf
    assert "header_up X-Forwarded-For {remote_host}" in cf and "Strict-Transport-Security" in cf


def test_trades_endpoint_needs_auth_validates_input_and_passes_it_to_the_provider(tmp_path):
    seen = []
    ks = KillSwitch(MemoryStore(), MemoryEventLog(), tmp_path / "KILL")
    c = TestClient(create_app(ks, TOKEN, trades_provider=lambda p, s: seen.append((p, s)) or {"page": p, "size": s, "total": 0, "pages": 1, "items": []}))
    h = {"Authorization": f"Bearer {TOKEN}"}
    assert c.get("/api/trades").status_code == 401
    assert c.get("/api/trades?page=2&size=25", headers=h).json()["page"] == 2 and seen[-1] == (2, 25)
    assert c.get("/api/trades", headers=h).status_code == 200 and seen[-1] == (1, 15)
    for bad in ("page=0", "page=-1", "size=0", "size=101", "page=abc", "size=1e9"):
        assert c.get(f"/api/trades?{bad}", headers=h).status_code == 422, bad
