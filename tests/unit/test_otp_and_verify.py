import asyncio
import logging
from decimal import Decimal as D

import httpx
import pytest

from derivbot.common import redaction
from derivbot.config.settings import load_settings
from derivbot.deriv.session import DerivSession, SessionSupervisor
from derivbot.guard.endpoint import DEMO_PATH, REAL_PATH, EndpointPolicy, classify_url
from derivbot.guard.otp import HttpOtpProvider, OtpError, RealModeRefused
from derivbot.guard.verify import DemoConnectionVerifier, assess
from tests.fakes.fake_deriv import FakeDeriv

PAT = "pat_FAKE_demo_token_ABCDEF123456"
DEMO_URL = f"wss://api.derivws.com{DEMO_PATH}?otp=FAKEOTP999888777"
REAL_URL = f"wss://api.derivws.com{REAL_PATH}?otp=FAKEOTP999888777"


@pytest.fixture(autouse=True)
def _clean():
    redaction.reset_secrets()
    yield
    redaction.reset_secrets()


def provider(handler, account="DEMO-ACC-1"):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return HttpOtpProvider(app_id="12345", pat=PAT, account_id=account, client=client)


def ok_handler(url):
    def h(request):
        return httpx.Response(200, json={"data": {"url": url}, "meta": {"endpoint": "x"}})
    return h


async def test_otp_request_has_the_documented_shape_and_returns_the_demo_url():
    seen = {}

    def handler(request):
        seen.update(method=request.method, path=request.url.path, auth=request.headers.get("authorization"),
                    app=request.headers.get("deriv-app-id"), ctype=request.headers.get("content-type"))
        return httpx.Response(200, json={"data": {"url": DEMO_URL}})

    assert await provider(handler).get_url() == DEMO_URL
    assert seen == {"method": "POST", "path": "/trading/v1/options/accounts/DEMO-ACC-1/otp", "auth": f"Bearer {PAT}",
                    "app": "12345", "ctype": "application/json"}


@pytest.mark.parametrize("returned", [
    REAL_URL, "wss://api.derivws.com/trading/v1/options/ws/public", "wss://evil.example/trading/v1/options/ws/demo?otp=x",
    "ws://api.derivws.com/trading/v1/options/ws/demo?otp=x", "", None, 5,
])
async def test_any_url_that_is_not_exactly_the_demo_endpoint_is_refused(returned):
    with pytest.raises(RealModeRefused):
        await provider(ok_handler(returned)).get_url()


@pytest.mark.parametrize("response,msg", [
    (httpx.Response(401, json={}), "401"), (httpx.Response(403, json={}), "403"), (httpx.Response(500), "500"),
    (httpx.Response(200, text="not json"), "no data.url"), (httpx.Response(200, json={"data": {}}), "no data.url"),
    (httpx.Response(200, json=[1, 2]), "no data.url"),
])
async def test_http_failures_and_malformed_bodies_are_clean_errors(response, msg):
    with pytest.raises(OtpError, match=msg):
        await provider(lambda r: response).get_url()


async def test_network_errors_do_not_leak_the_pat(caplog):
    def boom(request):
        raise httpx.ConnectError(f"cannot connect, auth was Bearer {PAT}")

    caplog.set_level(logging.DEBUG)
    with pytest.raises(OtpError) as ei:
        await provider(boom).get_url()
    assert PAT not in str(ei.value) and PAT not in caplog.text


def test_account_id_cannot_inject_path_segments():
    for bad in ("../real", "a/b", "x?y", "", "a" * 41, "a b"):
        with pytest.raises(OtpError):
            HttpOtpProvider(app_id="1", pat=PAT, account_id=bad)


def test_the_provider_is_built_from_demo_credentials_only_whatever_real_ones_are_configured():
    s = load_settings({"DATABASE_URL": "postgresql+psycopg://u:p@h/d", "REDIS_URL": "redis://r/0",
                       "DERIV_APP_ID_DEMO": "111", "DERIV_PAT_DEMO": PAT, "DERIV_ACCOUNT_ID_DEMO": "DEMO-ACC-1",
                       "DERIV_APP_ID_REAL": "999", "DERIV_PAT_REAL": "pat_FAKE_REAL_TOKEN_XYZ", "DERIV_ACCOUNT_ID_REAL": "REAL-1"})
    p = HttpOtpProvider.from_settings(s)
    assert (p._app_id, p._pat, p._account) == ("111", PAT, "DEMO-ACC-1")
    with pytest.raises(OtpError, match="not configured"):
        HttpOtpProvider.from_settings(load_settings({"DATABASE_URL": "postgresql+psycopg://u:p@h/d", "REDIS_URL": "redis://r/0",
                                                     "DERIV_APP_ID_REAL": "999", "DERIV_PAT_REAL": "pat_FAKE_REAL_TOKEN_XYZ",
                                                     "DERIV_ACCOUNT_ID_REAL": "REAL-1"}))


# ------------------------------------------------------------------ assess(): the demo-ness decision

@pytest.mark.parametrize("endpoint,loginid,expected,result", [
    ("demo", "VRTC1", "VRTC1", ("demo", True)),
    ("demo", "VRTC2", "VRTC1", ("demo", False)),          # a different account than configured
    ("demo", None, "VRTC1", ("demo", None)),               # unknown fails closed
    ("demo", "CR1234", "CR1234", ("demo", False)),         # equal to config but looks like a real-money id: vetoed
    ("demo", "MF99", "MF99", ("demo", False)),
    ("real", "VRTC1", "VRTC1", ("real", False)),
    (None, "VRTC1", "VRTC1", (None, None)),
    ("public", "VRTC1", "VRTC1", (None, None)),
])
def test_assess_matrix(endpoint, loginid, expected, result):
    assert assess(endpoint, loginid, expected) == result


# ------------------------------------------------------------------ connect-time verification against the fake server

async def make_supervisor(fake, expected, path=DEMO_PATH):
    pol = EndpointPolicy(scheme="ws", host="127.0.0.1", port=fake.port)

    async def url():
        return fake.url_for(path)

    verifier = DemoConnectionVerifier(expected)
    sup = SessionSupervisor(lambda: DerivSession(url, endpoint_classifier=lambda u: classify_url(u, pol)),
                            on_connected=[verifier.on_connected], on_disconnected=[verifier.on_disconnected],
                            backoff_base=0.05, backoff_cap=0.1)
    return sup, verifier


async def run_for(sup, seconds=0.6):
    halt = asyncio.Event()
    task = asyncio.create_task(sup.run(halt))
    published = []
    for _ in range(int(seconds / 0.05)):
        published.append(sup.session is not None)
        await asyncio.sleep(0.05)
    halt.set()
    await asyncio.wait_for(task, 5)
    return published


async def test_a_confirmed_demo_connection_is_published_with_account_and_balance():
    async with FakeDeriv(balance=1234.5) as fake:
        sup, ver = await make_supervisor(fake, "VRTC0000001")
        published = await run_for(sup)
    assert any(published)
    assert ver.info.endpoint_mode == "demo" or ver.info.endpoint_mode is None      # disconnected at the end
    assert sup.connects >= 1


async def test_info_while_connected_names_the_confirmed_demo_account():
    async with FakeDeriv(balance=1234.5) as fake:
        sup, ver = await make_supervisor(fake, "VRTC0000001")
        halt = asyncio.Event()
        task = asyncio.create_task(sup.run(halt))
        for _ in range(60):
            if sup.session is not None:
                break
            await asyncio.sleep(0.05)
        info = ver.get_info()
        halt.set()
        await asyncio.wait_for(task, 5)
    assert (info.endpoint_mode, info.account_is_demo, info.loginid, info.balance) == ("demo", True, "VRTC0000001", D("1234.5"))


async def test_a_different_account_is_never_published_to_the_executor():
    async with FakeDeriv() as fake:
        sup, ver = await make_supervisor(fake, "SOMEONE-ELSE")
        published = await run_for(sup)
    assert not any(published) and "not a confirmed demo account" in (sup.last_error or "")
    assert ver.info.account_is_demo is None


async def test_a_connection_that_turns_out_to_be_the_real_endpoint_is_refused():
    async with FakeDeriv() as fake:
        sup, _ = await make_supervisor(fake, "VRTC0000001", path=REAL_PATH)
        published = await run_for(sup)
    assert not any(published) and "endpoint=real" in (sup.last_error or "")


async def test_after_a_disconnect_demo_is_unproven_until_the_next_verification():
    async with FakeDeriv() as fake:
        sup, ver = await make_supervisor(fake, "VRTC0000001")
        halt = asyncio.Event()
        task = asyncio.create_task(sup.run(halt))
        for _ in range(60):
            if sup.session is not None:
                break
            await asyncio.sleep(0.05)
        await fake.drop_all()
        await asyncio.sleep(0.02)
        for _ in range(60):
            if sup.connects >= 2 and sup.session is not None:
                break
            await asyncio.sleep(0.05)
        assert sup.connects >= 2 and ver.get_info().account_is_demo is True
        halt.set()
        await asyncio.wait_for(task, 5)
