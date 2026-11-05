import pytest
from fastapi.testclient import TestClient

from derivbot.api.app import COOKIE, AuthLimiter, SessionStore, create_app
from derivbot.api.passwords import MIN_LENGTH, fingerprint, hash_password, validate_new_password, verify_password
from derivbot.killswitch.switch import KillSwitch, MemoryEventLog
from derivbot.kv.store import MemoryStore

TOKEN = "FAKE-ADMIN-TOKEN-ABCDEF123456"
PW = "correct horse battery"
HDR = {"X-Requested-With": "botrade"}
STORED = hash_password(PW)


# ---------------------------------------------------------------- hashing

def test_hash_roundtrip_and_salting():
    assert STORED.startswith("scrypt$") and PW not in STORED
    assert verify_password(PW, STORED) and not verify_password(PW + "x", STORED) and not verify_password("", STORED)
    assert hash_password(PW) != STORED                                   # a fresh salt every time
    assert verify_password(PW, hash_password(PW))


@pytest.mark.parametrize("bad", ["", "plain", "scrypt$1$2$3", "bcrypt$a$b$c$d$e", "scrypt$99999999$8$1$AAAA$AAAA",
                                 "scrypt$16384$8$1$!!!$!!!", "scrypt$16384$8$1$AAAAAAAAAAAA$AAAA", "$".join(["scrypt"] * 6)])
def test_malformed_stored_values_never_verify_and_never_raise(bad):
    assert verify_password(PW, bad) is False


def test_oversized_or_wrong_typed_input_is_rejected_without_work():
    assert verify_password("x" * 10_000, STORED) is False
    assert verify_password(None, STORED) is False        # type: ignore[arg-type]


def test_new_password_rules():
    assert validate_new_password("short") and validate_new_password("a" * 20) and validate_new_password("1234567890123")
    assert validate_new_password(PW) is None and len(PW) >= MIN_LENGTH
    assert validate_new_password("x" * 300)


# ---------------------------------------------------------------- the login flow over the API

def make(tmp_path, stored=STORED, token=TOKEN, sessions=None, limiter=None, https=False):
    ks = KillSwitch(MemoryStore(), MemoryEventLog(), tmp_path / "KILL")
    box = {"h": stored}
    app = create_app(ks, token, lambda: {"mode": "demo"}, lambda: {}, limiter=limiter, password_hash=lambda: box["h"], sessions=sessions)
    return TestClient(app, base_url="https://testserver" if https else "http://testserver"), ks, box


def test_auth_methods_tell_the_page_which_forms_to_show(tmp_path):
    c, _, box = make(tmp_path)
    assert c.get("/api/auth-methods").json() == {"password": True, "token": True}
    box["h"] = None
    assert c.get("/api/auth-methods").json() == {"password": False, "token": True}


def test_login_sets_a_hardened_cookie_and_the_cookie_opens_the_status(tmp_path):
    c, _, _ = make(tmp_path, https=True)                 # a Secure cookie is only sent back over https
    assert c.get("/status").status_code == 401
    r = c.post("/api/login", json={"password": PW})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert COOKIE in cookie and "httponly" in cookie and "samesite=strict" in cookie and "secure" in cookie and "path=/" in cookie
    assert PW not in r.headers["set-cookie"]
    assert c.get("/status").status_code == 200 and c.get("/api/summary").status_code == 200


def test_the_secure_flag_is_set_only_over_https(tmp_path):
    c, _, _ = make(tmp_path)
    assert "secure" not in c.post("/api/login", json={"password": PW}).headers["set-cookie"].lower().split("; ")
    behind_proxy = c.post("/api/login", json={"password": PW}, headers={"x-forwarded-proto": "https"})
    assert "secure" in behind_proxy.headers["set-cookie"].lower().split("; ")


def test_wrong_password_is_401_and_counts_toward_the_lockout(tmp_path):
    c, _, _ = make(tmp_path)
    for _ in range(10):
        assert c.post("/api/login", json={"password": "wrong password!"}).status_code == 401
    r = c.post("/api/login", json={"password": PW})
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0          # even the right password is refused while locked


def test_login_is_503_when_no_password_is_set_and_the_token_still_works(tmp_path):
    c, _, _ = make(tmp_path, stored=None)
    assert c.post("/api/login", json={"password": PW}).status_code == 503
    assert c.get("/status", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_a_cookie_write_needs_the_csrf_header_and_a_bearer_write_does_not(tmp_path):
    c, ks, _ = make(tmp_path)
    c.post("/api/login", json={"password": PW})
    assert c.post("/kill", json={"reason": "x"}).status_code == 403 and not ks.is_engaged()
    assert c.post("/kill", json={"reason": "dashboard"}, headers=HDR).status_code == 200 and ks.is_engaged()
    c2, ks2, _ = make(tmp_path / "b" if (tmp_path / "b").mkdir() is None else tmp_path)
    assert c2.post("/kill", json={}, headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200 and ks2.is_engaged()


def test_release_works_with_the_token_and_never_with_a_browser_session(tmp_path):
    c, ks, _ = make(tmp_path)
    ks.engage("cli", "x")
    c.post("/api/login", json={"password": PW})
    assert c.post("/release", json={"reason": "all clear"}, headers=HDR).status_code == 403 and ks.is_engaged()
    c.cookies.clear()
    r = c.post("/release", json={"reason": "all clear"}, headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code in (200, 409)                                            # allowed to try: 409 only if it cannot be logged


def test_logout_ends_the_session_and_the_old_cookie_is_dead(tmp_path):
    c, _, _ = make(tmp_path)
    c.post("/api/login", json={"password": PW})
    stolen = c.cookies.get(COOKIE)
    assert c.post("/api/logout").status_code == 200
    c.cookies.set(COOKIE, stolen)
    assert c.get("/status").status_code == 401


def test_changing_the_password_signs_everyone_out(tmp_path):
    c, _, box = make(tmp_path)
    c.post("/api/login", json={"password": PW})
    assert c.get("/status").status_code == 200
    box["h"] = hash_password("a different passphrase 99")
    assert c.get("/status").status_code == 401


def test_sessions_expire_when_idle_and_at_the_absolute_limit():
    t = {"v": 0.0}
    s = SessionStore(idle_s=100, absolute_s=300, clock=lambda: t["v"])
    fp = fingerprint(STORED)
    sid = s.create(fp)
    t["v"] = 90.0
    assert s.valid(sid, fp)                 # activity refreshes the idle timer
    t["v"] = 180.0
    assert s.valid(sid, fp)
    t["v"] = 270.0
    assert s.valid(sid, fp)
    t["v"] = 310.0
    assert not s.valid(sid, fp)             # absolute limit
    sid2 = s.create(fp)
    t["v"] = 500.0
    assert not s.valid(sid2, fp)            # idle limit
    assert not s.valid("nonsense", fp) and not s.valid(None, fp) and not s.valid(sid2, None)


def test_session_store_is_bounded_and_keeps_only_hashes():
    s = SessionStore(cap=3)
    fp = fingerprint(STORED)
    ids = [s.create(fp) for _ in range(5)]
    assert len(s._s) <= 3 and not s.valid(ids[0], fp) and s.valid(ids[-1], fp)
    assert all(i not in s._s for i in ids)                       # the cookie value itself is never a key


def test_a_missing_credential_is_not_counted_as_a_guess(tmp_path):
    lim = AuthLimiter(max_failures=3)
    c, _, _ = make(tmp_path, limiter=lim)
    for _ in range(20):
        assert c.get("/status").status_code == 401               # the page probes without credentials when it loads
    assert c.post("/api/login", json={"password": PW}).status_code == 200


def test_the_password_script_writes_into_the_existing_file_so_a_mounted_container_sees_it(tmp_path, monkeypatch):
    import importlib.util
    import os
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("setpw", Path(__file__).resolve().parents[2] / "scripts" / "set_dashboard_password.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    target = tmp_path / "secrets" / "dashboard_password_hash"
    target.parent.mkdir()
    target.write_text("")
    inode = os.stat(target).st_ino
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "TARGET", target)
    answers = iter(["a long enough passphrase 1", "a long enough passphrase 1"])
    monkeypatch.setattr(mod.getpass, "getpass", lambda prompt="": next(answers))
    assert mod.main() == 0
    assert os.stat(target).st_ino == inode                       # same file, not a replacement
    assert verify_password("a long enough passphrase 1", target.read_text().strip())
