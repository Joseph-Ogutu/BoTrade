"""Stage 1 of the real-account path: the primitives. Each must refuse every way of being used wrongly, in BOTH directions
(a demo thing must never reach real, a real thing must never reach demo) and without a genuine REAL decision from the guard."""

from decimal import Decimal as D

import httpx
import pytest

from derivbot.config.risk import parse_risk_config
from derivbot.guard import real_guard as rg
from derivbot.guard.endpoint import DEMO_PATH, PROD_POLICY, REAL_PATH, RealTradingDisabled, build_ws_url, classify_url
from derivbot.guard.otp import HttpOtpProvider, OtpError, RealModeRefused
from derivbot.guard.real_limits import (
    MAX_RISK_FRACTION,
    RealLimitsError,
    real_limit_violations,
    validate_real_limits,
)
from derivbot.guard.real_otp import RealOtpProvider
from derivbot.guard.real_verify import DEMO_LOGINID, RealConnectionVerifier, assess_real
from derivbot.guard.verify import DemoConnectionVerifier, assess
from tests.unit.test_config import VALID
from tests.unit.test_real_guard import CONDITIONS, HEAD, build

REAL_ID = "ROT00000002"
FAKE_PAT = "FAKE-REAL-PAT-0123456789ABCDEF0123456789"


def real_decision(tmp_path):
    s, r, otp = build(tmp_path, set(CONDITIONS))
    d = rg.resolve_mode(s, repo_root=r, head_commit=HEAD, otp_endpoint_mode=otp, build_lock=False)
    assert d.real_unlocked
    return d, s


# ---------------------------------------------------------------- the decision cannot be forged

def test_only_the_guard_can_issue_a_real_decision(tmp_path):
    real, _ = real_decision(tmp_path)
    assert real.real_unlocked
    forged = rg.ModeDecision(rg.Mode.REAL)
    assert forged.mode is rg.Mode.REAL and not forged.real_unlocked                  # a hand-built REAL decision proves nothing
    assert not rg.ModeDecision(rg.Mode.DEMO).real_unlocked
    (tmp_path / "x").mkdir()
    s, r, otp = build(tmp_path / "x", {"mode_is_real"})
    assert not rg.resolve_mode(s, repo_root=r, head_commit=HEAD, otp_endpoint_mode=otp).real_unlocked


# ---------------------------------------------------------------- endpoint builder

def test_the_real_url_is_built_only_with_the_explicit_unlock_flag():
    assert build_ws_url("demo", PROD_POLICY).endswith(DEMO_PATH)
    assert build_ws_url("real", PROD_POLICY, real_unlocked=True).endswith(REAL_PATH)
    for args in (("real", PROD_POLICY), ("live", PROD_POLICY), ("", PROD_POLICY)):
        with pytest.raises(RealTradingDisabled):
            build_ws_url(*args)
    with pytest.raises(RealTradingDisabled):
        build_ws_url("live", PROD_POLICY, real_unlocked=True)                          # the flag does not make an unknown mode valid
    assert classify_url(build_ws_url("real", PROD_POLICY, real_unlocked=True), PROD_POLICY) == "real"


# ---------------------------------------------------------------- real OTP provider

def otp_client(url):
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json={"data": {"url": url}})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


REAL_URL = "wss://api.derivws.com" + REAL_PATH + "?otp=FAKEOTP123456"
DEMO_URL = "wss://api.derivws.com" + DEMO_PATH + "?otp=FAKEOTP123456"


def provider(decision, client, **kw):
    return RealOtpProvider(decision, app_id="APPREAL", pat=FAKE_PAT, account_id=REAL_ID, client=client, **kw)


async def test_the_real_otp_provider_needs_a_genuine_real_decision(tmp_path):
    client, _ = otp_client(REAL_URL)
    for bad in (None, rg.ModeDecision(rg.Mode.REAL), rg.ModeDecision(rg.Mode.DEMO), "real"):
        with pytest.raises(RealModeRefused):
            provider(bad, client)


async def test_it_returns_the_real_url_using_the_real_credentials_and_account(tmp_path):
    d, _ = real_decision(tmp_path)
    client, seen = otp_client(REAL_URL)
    url = await provider(d, client).get_url()
    assert url == REAL_URL
    req = seen[0]
    assert req.url.path.endswith(f"/accounts/{REAL_ID}/otp") and req.headers["authorization"] == f"Bearer {FAKE_PAT}"
    assert req.headers["deriv-app-id"] == "APPREAL"


async def test_a_demo_url_or_any_other_url_from_the_real_provider_is_refused(tmp_path):
    d, _ = real_decision(tmp_path)
    for bad in (DEMO_URL, "wss://evil.example.com" + REAL_PATH, "wss://api.derivws.com/other", "ws://api.derivws.com" + REAL_PATH, 5, None):
        client, _ = otp_client(bad)
        with pytest.raises(RealModeRefused):
            await provider(d, client).get_url()


async def test_the_real_provider_reports_errors_without_leaking_the_token(tmp_path):
    d, _ = real_decision(tmp_path)
    for status in (401, 403, 500):
        c = httpx.AsyncClient(transport=httpx.MockTransport(lambda r, s=status: httpx.Response(s, text=FAKE_PAT)))
        with pytest.raises(OtpError) as e:
            await provider(d, c).get_url()
        assert FAKE_PAT not in str(e.value)
    c = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"nope": 1})))
    with pytest.raises(OtpError, match="no data.url"):
        await provider(d, c).get_url()

    def boom(request):
        raise httpx.ConnectError("boom " + FAKE_PAT)

    with pytest.raises(OtpError) as e:
        await provider(d, httpx.AsyncClient(transport=httpx.MockTransport(boom))).get_url()
    assert FAKE_PAT not in str(e.value)


def test_from_settings_uses_only_real_credentials_and_needs_all_of_them(tmp_path):
    d, s = real_decision(tmp_path)
    assert isinstance(RealOtpProvider.from_settings(d, s), RealOtpProvider)
    (tmp_path / "y").mkdir()
    s2, _, _ = build(tmp_path / "y", {"mode_is_real", "allow_flag"})              # no real credentials
    with pytest.raises(OtpError, match="not configured"):
        RealOtpProvider.from_settings(d, s2)


async def test_the_demo_provider_still_refuses_a_real_url_and_the_real_provider_a_demo_url(tmp_path):
    client, _ = otp_client(REAL_URL)
    demo = HttpOtpProvider(app_id="A", pat=FAKE_PAT, account_id="DOT00000001", client=client)
    with pytest.raises(RealModeRefused):
        await demo.get_url()


# ---------------------------------------------------------------- connection verifiers

class Sess:
    def __init__(self, loginid, endpoint_mode, balance="1000.00"):
        self.endpoint_mode, self._lid, self._bal, self._n = endpoint_mode, loginid, balance, 0

    def next_req_id(self):
        self._n += 1
        return self._n

    async def request(self, req, timeout=10.0):
        return {"msg_type": "balance", "balance": {"balance": self._bal, "currency": "USD", "loginid": self._lid}}


def test_assess_real_table():
    assert assess_real("real", REAL_ID, REAL_ID) == ("real", True)
    assert assess_real("real", "ROT99999999", REAL_ID) == ("real", False)             # a different real account
    assert assess_real("real", None, REAL_ID) == ("real", False)
    assert assess_real("real", REAL_ID, "") == ("real", False)
    assert assess_real("demo", REAL_ID, REAL_ID) == ("demo", False)
    assert assess_real(None, REAL_ID, REAL_ID) == (None, False)
    for demo_like in ("VRT123", "VRTC0000001", "DOT94618170"):
        assert DEMO_LOGINID.match(demo_like) and assess_real("real", demo_like, demo_like) == ("real", False)


async def test_the_real_verifier_publishes_only_the_exact_configured_real_account(tmp_path):
    d, _ = real_decision(tmp_path)
    v = RealConnectionVerifier(d, REAL_ID)
    await v.on_connected(Sess(REAL_ID, "real"))
    info = v.get_info()
    assert (info.endpoint_mode, info.account_is_demo, info.loginid, str(info.balance)) == ("real", False, REAL_ID, "1000.00")
    await v.on_disconnected()
    assert v.get_info().endpoint_mode is None and v.get_info().account_is_demo is None          # proof is withdrawn on disconnect


@pytest.mark.parametrize("loginid,endpoint", [("ROT99999999", "real"), ("DOT94618170", "real"), (REAL_ID, "demo"), (REAL_ID, None), ("", "real")])
async def test_the_real_verifier_refuses_everything_else(tmp_path, loginid, endpoint):
    d, _ = real_decision(tmp_path)
    v = RealConnectionVerifier(d, REAL_ID)
    with pytest.raises(RealModeRefused):
        await v.on_connected(Sess(loginid, endpoint))
    assert v.get_info().endpoint_mode is None


async def test_the_real_verifier_refuses_a_reply_that_is_not_a_balance(tmp_path):
    d, _ = real_decision(tmp_path)

    class Odd(Sess):
        async def request(self, req, timeout=10.0):
            return {"msg_type": "ping", "ping": "pong"}

    with pytest.raises(RealModeRefused, match="unexpected reply"):
        await RealConnectionVerifier(d, REAL_ID).on_connected(Odd(REAL_ID, "real"))


def test_the_real_verifier_cannot_be_built_without_a_genuine_decision():
    for bad in (None, rg.ModeDecision(rg.Mode.REAL), rg.ModeDecision(rg.Mode.DEMO)):
        with pytest.raises(RealModeRefused):
            RealConnectionVerifier(bad, REAL_ID)


def test_the_balance_update_keeps_the_proof_and_changes_only_the_balance(tmp_path):
    d, _ = real_decision(tmp_path)
    v = RealConnectionVerifier(d, REAL_ID)
    v.info = m_info = v.info.__class__("real", False, D("10"), "USD", REAL_ID)
    v.update_balance(D("12.5"))
    assert (v.info.endpoint_mode, v.info.account_is_demo, v.info.balance) == ("real", False, D("12.5")) and m_info is not v.info


async def test_the_demo_verifier_refuses_the_real_account_in_every_shape(tmp_path):

    assert assess("demo", REAL_ID, REAL_ID) == ("demo", False)                         # a ROT id is never accepted as demo, even if configured
    v = DemoConnectionVerifier(REAL_ID)
    with pytest.raises(RealModeRefused):
        await v.on_connected(Sess(REAL_ID, "demo"))
    with pytest.raises(RealModeRefused):
        await DemoConnectionVerifier("DOT00000001").on_connected(Sess("DOT00000001", "real"))   # the real endpoint is refused by demo


# ---------------------------------------------------------------- hard ceilings

REAL_OK = {**VALID, "risk_per_trade_pct": "0.01", "max_stake_abs": "5", "max_trades_per_day": 5, "max_losses_per_day": 2,
           "max_consecutive_wins_per_day": 3, "max_daily_loss_abs": None, "max_daily_loss_pct_of_start_balance": "0.10",
           "max_open_positions": 1, "min_net_payout_ratio": "0.85"}


def test_a_config_within_every_ceiling_is_accepted():
    validate_real_limits(parse_risk_config(REAL_OK))
    validate_real_limits(parse_risk_config({**REAL_OK, "risk_per_trade_pct": str(MAX_RISK_FRACTION)}))      # exactly at the ceiling


@pytest.mark.parametrize("override,needle", [
    ({"risk_per_trade_pct": "0.16"}, "risk_per_trade_pct"),
    ({"max_trades_per_day": 6}, "max_trades_per_day"), ({"max_trades_per_day": None}, "max_trades_per_day"),
    ({"max_losses_per_day": 3}, "max_losses_per_day"), ({"max_losses_per_day": None}, "max_losses_per_day"),
    ({"max_consecutive_wins_per_day": 4}, "max_consecutive_wins_per_day"), ({"max_consecutive_wins_per_day": None}, "max_consecutive_wins_per_day"),
    ({"max_open_positions": 2}, "max_open_positions"),
    ({"max_daily_loss_pct_of_start_balance": "0.31"}, "max_daily_loss_pct_of_start_balance"),
    ({"max_daily_loss_pct_of_start_balance": None, "max_daily_loss_abs": "10"}, "max_daily_loss_pct_of_start_balance"),
    ({"max_proposal_price_slippage": "0.03"}, "max_proposal_price_slippage"), ({"min_net_payout_ratio": "0.79"}, "min_net_payout_ratio"),
])
def test_every_ceiling_and_every_required_owner_rule_is_enforced(override, needle):
    cfg = parse_risk_config({**REAL_OK, **override})
    assert any(needle in v for v in real_limit_violations(cfg))
    with pytest.raises(RealLimitsError, match=needle):
        validate_real_limits(cfg)


def test_the_ceilings_are_the_owners_own_rules():
    from derivbot.guard import real_limits as L

    assert (L.MAX_RISK_FRACTION, L.MAX_TRADES_PER_DAY, L.MAX_LOSSES_PER_DAY, L.MAX_WIN_STREAK_STOP) == (D("0.15"), 5, 2, 3)
