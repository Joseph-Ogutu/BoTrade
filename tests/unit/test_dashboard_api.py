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
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setAttribute(\"style\"",
                   "localStorage"):
        assert banned not in js, banned


def test_the_page_offers_no_release_and_the_only_write_is_the_kill_switch():
    js = re.search(r"<script>(.*?)</script>", HTML, re.S).group(1)
    assert "/release" not in js
    assert re.findall(r"api\(\"([^\"]+)\"", js) == ["/api/summary", "/kill"]
    assert re.findall(r"method:\"([A-Z]+)\"", js) == ["POST"]


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
