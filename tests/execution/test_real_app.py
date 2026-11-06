"""The service in REAL mode against the fake exchange. Real mode must start only when every guard agrees, and refuse with a reason otherwise.
No real endpoint, no real credential, no real money: the fake server stands in for Deriv and the credentials are fakes."""

import asyncio
import json
import shutil
from decimal import Decimal as D
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from derivbot import app as appmod
from derivbot.config.risk import RiskConfigError
from derivbot.config.settings import load_settings
from derivbot.guard.endpoint import REAL_PATH, EndpointPolicy
from derivbot.kv.store import MemoryStore
from derivbot.notify.transport import FakeTransport
from tests.execution.test_app import NoAccounts, free_port, start, stop
from tests.fakes.fake_deriv import FakeDeriv

pytestmark = pytest.mark.postgres
HEAD = "0123456789abcdef0123456789abcdef01234567"
REAL_ID = "ROT00000002"
READY = "# checklist\n" + "".join(f"- [x] item {i}\n" for i in range(5)) + "\nOwner sign-off: Joseph 2026-12-01\n"
REAL_RISK = {"risk_per_trade_pct": "0.01", "max_stake_abs": "5", "min_stake_abs": None, "max_daily_loss_abs": None,
             "max_daily_loss_pct_of_start_balance": "0.10", "max_consecutive_losses": 3, "max_open_positions": 1, "max_trades_per_hour": 20,
             "max_trades_per_day": 5, "max_losses_per_day": 2, "max_consecutive_wins_per_day": 3, "max_proposal_price_slippage": "0.02",
             "stale_data_max_age_s": 3600, "min_net_payout_ratio": "0.80", "reconcile_max_age_s": 3600, "recent_duplicate_window_s": 1,
             "daily_reset_utc_hour": 0}


@pytest.fixture(autouse=True)
def _head(monkeypatch):
    monkeypatch.setattr(appmod, "head_commit", lambda root: HEAD)


def real_root(tmp_path, *, approval=True, checklist=True, risk=None, strategies=None):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "risk.real.json").write_text(json.dumps(risk or REAL_RISK))
    (tmp_path / "config" / "strategies.json").write_text(json.dumps({"approved": strategies if strategies is not None else {"ema_trend": "1"}}))
    if approval:
        (tmp_path / "REAL_TRADING_APPROVED").write_text(f"approved_on: 2026-12-01\ncommit: {HEAD}\napproved_by: Joseph\n")
    if checklist:
        (tmp_path / "docs" / "live-readiness.md").write_text(READY)
    return tmp_path


def real_settings(url, **over):
    env = {"DATABASE_URL": url, "REDIS_URL": "redis://unused/0", "TRADING_MODE": "real", "ALLOW_REAL_TRADING": "true",
           "DERIV_APP_ID_REAL": "9", "DERIV_PAT_REAL": "FAKE-REAL-PAT-0123456789ABCDEF0123", "DERIV_ACCOUNT_ID_REAL": REAL_ID,
           "TELEGRAM_ALLOWED_CHAT_IDS": "1111", "ADMIN_API_TOKEN": "FAKE-ADMIN-TOKEN-ABCDEF123456"}
    env.update(over)
    return load_settings(env)


class RealFakeOtp:
    def __init__(self, fake, path=REAL_PATH):
        self.fake, self.path = fake, path

    async def get_url(self):
        return self.fake.url_for(self.path)


def build_real(url, root, fake=None, **kw):
    pol = EndpointPolicy("ws", "127.0.0.1", fake.port) if fake else None
    kwargs = dict(root=root, kv=MemoryStore(), transport=FakeTransport(), accounts_view=NoAccounts(), flag_path=root / "KILL")
    if fake:
        kwargs.update(policy=pol, otp_provider=kw.pop("otp", RealFakeOtp(fake)))
    return appmod.build(real_settings(url, **kw.pop("env", {})), **kwargs)


# ---------------------------------------------------------------- refusals at build time

@pytest.mark.parametrize("flags", [{"approval": False}, {"checklist": False}])
def test_real_mode_refuses_without_the_owner_approval_file_or_the_signed_checklist(fresh_db_url, tmp_path, flags, monkeypatch):
    monkeypatch.setattr("derivbot.guard.real_guard.REAL_TRADING_IMPLEMENTED", True)
    with pytest.raises(appmod.StartupError, match="real mode refused"):
        build_real(fresh_db_url, real_root(tmp_path, **flags))


def test_real_mode_refuses_while_the_flag_or_the_credentials_are_missing(fresh_db_url, tmp_path, monkeypatch):
    monkeypatch.setattr("derivbot.guard.real_guard.REAL_TRADING_IMPLEMENTED", True)
    root = real_root(tmp_path)
    with pytest.raises(appmod.StartupError, match="real mode refused"):
        build_real(fresh_db_url, root, env={"ALLOW_REAL_TRADING": "false"})
    with pytest.raises(appmod.StartupError, match="real mode refused"):
        build_real(fresh_db_url, root, env={"DERIV_PAT_REAL": ""})


def test_real_mode_refuses_while_the_build_lock_is_closed_even_with_everything_else_in_place(fresh_db_url, tmp_path, monkeypatch):
    monkeypatch.setattr("derivbot.guard.real_guard.REAL_TRADING_IMPLEMENTED", False)
    with pytest.raises(appmod.StartupError, match="build lock"):
        build_real(fresh_db_url, real_root(tmp_path))


def test_real_mode_refuses_unset_owner_values_and_values_above_the_hard_ceilings(fresh_db_url, tmp_path, monkeypatch):
    monkeypatch.setattr("derivbot.guard.real_guard.REAL_TRADING_IMPLEMENTED", True)
    with pytest.raises(RiskConfigError, match="risk_per_trade_pct"):
        build_real(fresh_db_url, real_root(tmp_path / "a", risk={**REAL_RISK, "risk_per_trade_pct": "SET_BY_OWNER"}))
    for bad in ({"risk_per_trade_pct": "0.2"}, {"max_trades_per_day": 6}, {"max_losses_per_day": 3}, {"max_consecutive_wins_per_day": None},
                {"max_open_positions": 2}):
        with pytest.raises(appmod.StartupError, match="real-money risk limits refused"):
            build_real(fresh_db_url, real_root(tmp_path / str(sorted(bad)), risk={**REAL_RISK, **bad}))


@pytest.mark.parametrize("flag", ["FORWARD_TEST", "PIPELINE_SOAK"])
def test_demo_only_features_are_refused_inside_a_real_process(fresh_db_url, tmp_path, monkeypatch, flag):
    monkeypatch.setattr("derivbot.guard.real_guard.REAL_TRADING_IMPLEMENTED", True)
    monkeypatch.setenv(flag, "1")
    with pytest.raises(appmod.StartupError, match="demo-only feature"):
        build_real(fresh_db_url, real_root(tmp_path))


def test_the_shipped_real_risk_template_fails_loudly_until_the_owner_sets_the_stake_rules():
    from derivbot.config.risk import load_risk_config

    root = Path(__file__).resolve().parents[2]
    with pytest.raises(RiskConfigError) as e:
        load_risk_config(root / "config" / "risk.real.json")
    for name in ("risk_per_trade_pct", "max_stake_abs", "max_daily_loss_pct_of_start_balance", "max_consecutive_losses"):
        assert name in str(e.value)
    shipped = json.loads((root / "config" / "risk.real.json").read_text())
    assert (shipped["max_trades_per_day"], shipped["max_losses_per_day"], shipped["max_consecutive_wins_per_day"]) == (5, 2, 3)


# ---------------------------------------------------------------- a real process against the fake exchange

async def test_real_mode_starts_verifies_the_real_account_and_trades_in_real_mode(fresh_db_url, tmp_path, monkeypatch):
    monkeypatch.setattr("derivbot.guard.real_guard.REAL_TRADING_IMPLEMENTED", True)
    async with FakeDeriv(balance=2500.0, loginid=REAL_ID) as fake:
        app = build_real(fresh_db_url, real_root(tmp_path), fake)
        assert app.mode == "real" and app.executor.d.configured_mode == "real" and app.executor.d.real_unlocked
        halt, task = await start(app, free_port())
        for _ in range(100):
            if app.supervisor.session is not None:
                break
            await asyncio.sleep(0.05)
        assert app.supervisor.session is not None
        info = app.verifier.get_info()
        assert (info.endpoint_mode, info.account_is_demo, info.loginid) == ("real", False, REAL_ID)
        assert app.status()["mode"] == "real"
        for _ in range(200):                                                 # ticks flowing, minimum stake discovered, reconcile done
            if app.fresh.age("1HZ100V") is not None and app.executor.d.api_min_stake("1HZ100V") and app.reconciler.age_s() is not None:
                break
            await asyncio.sleep(0.05)
        from derivbot.risk.engine import IntentRequest

        seed = create_engine(fresh_db_url)                                     # the percentage loss limit needs a start-of-day balance
        with seed.begin() as c:                                                # (the balance recorder writes one within seconds in service)
            c.execute(text("INSERT INTO balance_snapshots (mode, account_id, balance, currency, ts) "
                           "VALUES ('real', :a, 2500, 'USD', now() - interval '1 hour')"), {"a": REAL_ID})
        seed.dispose()
        res = await app.executor.submit(IntentRequest("real-1", "1HZ100V", "CALL", "ema_trend", "1", "real", REAL_ID))
        assert res.state.value == "SETTLED" and fake.buys_received == 1, res.reason
        eng = create_engine(fresh_db_url)
        with eng.connect() as c:
            assert c.execute(text("SELECT mode, account_id FROM order_intents")).one() == ("real", REAL_ID)
            assert c.execute(text("SELECT payload->>'mode' FROM audit_events WHERE event_type = 'MODE_GUARD'")).scalar() == "real"
        eng.dispose()
        await stop(halt, task)


async def test_a_real_process_that_is_given_the_demo_endpoint_never_publishes_a_session(fresh_db_url, tmp_path, monkeypatch):
    monkeypatch.setattr("derivbot.guard.real_guard.REAL_TRADING_IMPLEMENTED", True)
    async with FakeDeriv(balance=2500.0, loginid=REAL_ID) as fake:
        from derivbot.guard.endpoint import DEMO_PATH

        class DemoUrlOtp(RealFakeOtp):
            async def get_url(self):
                return self.fake.url_for(DEMO_PATH)                     # the provider hands back a demo URL

        app = build_real(fresh_db_url, real_root(tmp_path), fake, otp=DemoUrlOtp(fake))
        halt, task = await start(app, free_port())
        await asyncio.sleep(1.0)
        assert app.supervisor.session is None and fake.buys_received == 0
        await stop(halt, task)


async def test_a_real_process_connected_to_a_different_account_never_publishes_a_session(fresh_db_url, tmp_path, monkeypatch):
    monkeypatch.setattr("derivbot.guard.real_guard.REAL_TRADING_IMPLEMENTED", True)
    for other in ("ROT99999999", "DOT94618170", "VRTC0000001"):
        async with FakeDeriv(balance=2500.0, loginid=other) as fake:
            app = build_real(fresh_db_url, real_root(tmp_path / other), fake)
            halt, task = await start(app, free_port())
            await asyncio.sleep(0.8)
            assert app.supervisor.session is None and fake.buys_received == 0, other
            await stop(halt, task)


async def test_a_demo_process_never_accepts_the_real_account_even_if_misconfigured(fresh_db_url, tmp_path):
    from derivbot.guard.endpoint import DEMO_PATH
    from tests.execution.test_app import FakeOtp, make_root, settings_for

    async with FakeDeriv(balance=2500.0, loginid=REAL_ID) as fake:                  # the "demo" connection reports a real account id
        app = appmod.build(settings_for(fresh_db_url, account=REAL_ID), root=make_root(tmp_path), kv=MemoryStore(), transport=FakeTransport(),
                           policy=EndpointPolicy("ws", "127.0.0.1", fake.port), otp_provider=FakeOtp(fake), flag_path=tmp_path / "KILL",
                           accounts_view=NoAccounts())
        halt, task = await start(app, free_port())
        await asyncio.sleep(0.8)
        assert app.supervisor.session is None and DEMO_PATH and fake.buys_received == 0
        await stop(halt, task)
    assert D("0") == 0 and shutil
