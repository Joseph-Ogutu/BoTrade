import httpx

from derivbot.api.accounts_view import PATH, AccountsView, parse_accounts

# shape OBSERVED on the live API (docs/api-notes.md), values replaced by fakes
REPLY = {"data": [
    {"account_id": "DOT00000001", "balance": "10010.23", "currency": "USD", "group": "row", "status": "active", "account_type": "demo"},
    {"account_id": "ROT00000002", "balance": "125.50", "currency": "USD", "group": "row", "status": "active", "account_type": "real"},
], "meta": {"endpoint": "/x", "method": "GET", "timing": 1}}
PAT = "FAKE-PAT-0123456789ABCDEF0123456789ABCDEF"


def view(handler, ttl=30.0, clock=None):
    kw = {"clock": clock} if clock else {}
    return AccountsView("APPID", PAT, ttl_s=ttl, client=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def test_parse_keeps_type_balance_currency_status_and_drops_ids():
    out = parse_accounts(REPLY)
    assert out == {"demo": {"balance": 10010.23, "currency": "USD", "status": "active"},
                   "real": {"balance": 125.5, "currency": "USD", "status": "active"}}
    assert "DOT00000001" not in str(out) and "ROT00000002" not in str(out)


def test_parse_ignores_the_unexpected_instead_of_guessing():
    assert parse_accounts("nope") == {"demo": None, "real": None}
    assert parse_accounts({"data": [1, {"account_type": "weird", "balance": "5"}, {"account_type": "real", "balance": "abc"}]}) \
        == {"demo": None, "real": {"balance": None, "currency": None, "status": None}}
    assert parse_accounts({"data": [{"account_type": "real", "balance": "1"}, {"account_type": "real", "balance": "2"}]})["real"]["balance"] == 1.0


def test_one_get_to_the_accounts_path_with_the_demo_token_and_nothing_else():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=REPLY)

    r = view(handler).get()
    assert len(seen) == 1 and seen[0].method == "GET" and seen[0].url.path == PATH
    assert seen[0].headers["authorization"] == f"Bearer {PAT}" and seen[0].headers["deriv-app-id"] == "APPID"
    assert r["real"]["balance"] == 125.5 and r["error"] is None


def test_the_result_is_cached_for_the_ttl_then_refreshed():
    calls, t = [], {"v": 0.0}

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json=REPLY)

    v = view(handler, ttl=30, clock=lambda: t["v"])
    v.get()
    v.get()
    assert len(calls) == 1
    t["v"] = 31.0
    v.get()
    assert len(calls) == 2


def test_a_failure_is_reported_without_the_token_and_keeps_the_last_good_values():
    state = {"ok": True}

    def handler(req):
        return httpx.Response(200, json=REPLY) if state["ok"] else httpx.Response(401, text="Invalid token format " + PAT)

    t = {"v": 0.0}
    v = view(handler, ttl=1, clock=lambda: t["v"])
    assert v.get()["error"] is None
    state["ok"] = False
    t["v"] = 5.0
    r = v.get()
    assert r["error"] and PAT not in str(r) and r["real"]["balance"] == 125.5


def test_a_network_error_is_reported_not_raised():
    def handler(req):
        raise httpx.ConnectError("boom " + PAT)

    r = view(handler).get()
    assert r["error"] and PAT not in str(r) and r["real"] is None
