"""End-to-end wiring of the trading service against a real Postgres and the fake Deriv server."""

import asyncio
import json
import socket

import httpx
import pytest
from sqlalchemy import create_engine, text

from derivbot import app as appmod
from derivbot.config.risk import RiskConfigError
from derivbot.config.settings import load_settings
from derivbot.guard.endpoint import DEMO_PATH, EndpointPolicy
from derivbot.kv.store import MemoryStore
from derivbot.notify.transport import FakeTransport
from tests.execution.conftest import EXEC_CFG
from tests.fakes.fake_deriv import FakeDeriv

pytestmark = pytest.mark.postgres
TOKEN = "FAKE-ADMIN-TOKEN-ABCDEF123456"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def make_root(tmp_path, risk=None):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "risk.json").write_text(json.dumps(risk or EXEC_CFG))
    (tmp_path / "config" / "strategies.json").write_text('{"approved": {}}')
    return tmp_path


def settings_for(url, account="VRTC0000001"):
    return load_settings({"DATABASE_URL": url, "REDIS_URL": "redis://unused/0", "TRADING_MODE": "demo", "TELEGRAM_ALLOWED_CHAT_IDS": "1111",
                          "DERIV_APP_ID_DEMO": "1", "DERIV_PAT_DEMO": "pat_FAKE_demo_token_ABCDEF123456",
                          "DERIV_ACCOUNT_ID_DEMO": account, "ADMIN_API_TOKEN": TOKEN})


class FakeOtp:
    def __init__(self, fake):
        self.fake = fake

    async def get_url(self):
        return self.fake.url_for(DEMO_PATH)


async def start(app, port):
    halt = asyncio.Event()
    task = asyncio.create_task(appmod.run(app, halt, api_port=port))
    return halt, task


async def stop(halt, task):
    halt.set()
    await asyncio.wait_for(task, 20)


async def test_the_service_starts_verifies_demo_reconciles_and_serves_the_loopback_api(fresh_db_url, tmp_path):
    async with FakeDeriv(balance=2500.0) as fake:
        transport, port = FakeTransport(), free_port()
        app = appmod.build(settings_for(fresh_db_url), root=make_root(tmp_path), kv=MemoryStore(), transport=transport,
                           policy=EndpointPolicy("ws", "127.0.0.1", fake.port), otp_provider=FakeOtp(fake), flag_path=tmp_path / "KILL")
        halt, task = await start(app, port)
        for _ in range(100):
            if app.supervisor.session is not None:
                break
            await asyncio.sleep(0.05)
        assert app.supervisor.session is not None
        info = app.verifier.get_info()
        assert (info.endpoint_mode, info.account_is_demo, str(info.balance)) == ("demo", True, "2500.0")
        await asyncio.sleep(0.5)
        eng = create_engine(fresh_db_url)
        with eng.connect() as c:
            runs = c.execute(text("SELECT trigger, status FROM reconciliation_runs ORDER BY id")).all()
            assert runs and tuple(runs[0]) == ("startup", "clean")
            assert c.execute(text("SELECT count(*) FROM config_versions WHERE kind = 'risk'")).scalar_one() == 1
        assert app.heartbeater.hb.age("bot") is not None
        assert any("trading service started" in m for _, m in transport.sent)
        async with httpx.AsyncClient() as client:
            assert (await client.get(f"http://127.0.0.1:{port}/status")).status_code == 401
            body = (await client.get(f"http://127.0.0.1:{port}/status", headers={"Authorization": f"Bearer {TOKEN}"})).json()
        assert body["mode"] == "demo" and body["signals"] == "off" and body["session_connected"] is True
        assert body["kill_switch"]["engaged"] is False and any("signals off" in n for n in body["notes"])
        assert fake.buy_messages == []                       # the service trades nothing without approvals and a known minimum stake
        await stop(halt, task)
        eng.dispose()


async def test_the_service_refuses_to_start_while_owner_risk_values_are_unset(fresh_db_url, tmp_path):
    root = make_root(tmp_path, risk={**EXEC_CFG, "max_stake_abs": "SET_BY_OWNER"})
    with pytest.raises(RiskConfigError, match="max_stake_abs"):
        appmod.build(settings_for(fresh_db_url), root=root, kv=MemoryStore(), transport=FakeTransport())


async def test_the_service_refuses_to_start_on_an_unmigrated_database(tmp_path):
    from tests.execution.conftest import admin_url

    admin = create_engine(admin_url(), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text("DROP DATABASE IF EXISTS x_empty WITH (FORCE)"))
        c.execute(text("CREATE DATABASE x_empty"))
    try:
        url = admin_url().rsplit("/", 1)[0] + "/x_empty"
        with pytest.raises(appmod.StartupError, match="not migrated"):
            appmod.build(settings_for(url), root=make_root(tmp_path), kv=MemoryStore(), transport=FakeTransport())
    finally:
        with admin.connect() as c:
            c.execute(text("DROP DATABASE IF EXISTS x_empty WITH (FORCE)"))
        admin.dispose()


async def test_a_session_for_the_wrong_account_is_never_published_by_the_running_service(fresh_db_url, tmp_path):
    async with FakeDeriv() as fake:
        port = free_port()
        app = appmod.build(settings_for(fresh_db_url, account="NOT-THE-FAKE-ACCOUNT"), root=make_root(tmp_path), kv=MemoryStore(),
                           transport=FakeTransport(), policy=EndpointPolicy("ws", "127.0.0.1", fake.port), otp_provider=FakeOtp(fake),
                           flag_path=tmp_path / "KILL")
        halt, task = await start(app, port)
        await asyncio.sleep(1.0)
        assert app.supervisor.session is None and "not a confirmed demo account" in (app.supervisor.last_error or "")
        await stop(halt, task)


async def test_an_engaged_kill_switch_survives_a_restart_of_the_service(fresh_db_url, tmp_path):
    kv = MemoryStore()
    flag = tmp_path / "KILL"
    common = dict(root=make_root(tmp_path), kv=kv, transport=FakeTransport(), flag_path=flag)
    first = appmod.build(settings_for(fresh_db_url), **common)
    first.kill_switch.engage("api", "drill")
    second = appmod.build(settings_for(fresh_db_url), **{**common, "kv": MemoryStore()})      # new process, Redis lost
    assert second.kill_switch.is_engaged()
    first.engine.dispose()
    second.engine.dispose()


async def test_without_demo_credentials_the_service_still_runs_its_safety_parts_and_says_so(fresh_db_url, tmp_path):
    s = load_settings({"DATABASE_URL": fresh_db_url, "REDIS_URL": "redis://unused/0", "ADMIN_API_TOKEN": TOKEN})
    app = appmod.build(s, root=make_root(tmp_path), kv=MemoryStore(), transport=FakeTransport(), flag_path=tmp_path / "KILL")
    assert app.supervisor is None and any("no demo credentials" in n for n in app.notes)
    app.engine.dispose()


# ---------------------------------------------------------------- forward test wiring (Amendment 5)

def forward_root(tmp_path, approved=True):
    import shutil
    from pathlib import Path

    src = Path(__file__).resolve().parents[2] / "docs"
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    for name in ("kill-criterion.md", "forward_test_lock.json"):
        shutil.copy(src / name, tmp_path / "docs" / name)
    line = "LIVE_DEMO_FORWARD_TEST_APPROVED: 2026-11-06 by Joseph (owner) amendment=5\n" if approved else "no approval here\n"
    (tmp_path / "docs" / "approvals.md").write_text(line)
    return make_root(tmp_path)


async def test_forward_test_is_off_unless_asked_for_and_off_without_the_owner_line(fresh_db_url, tmp_path, monkeypatch):
    app = appmod.build(settings_for(fresh_db_url), root=forward_root(tmp_path / "a"), kv=MemoryStore(), transport=FakeTransport())
    assert app.forward is None and not any("FORWARD TEST" in n for n in app.notes)                  # FORWARD_TEST not set
    monkeypatch.setenv("FORWARD_TEST", "1")
    app = appmod.build(settings_for(fresh_db_url), root=forward_root(tmp_path / "b", approved=False), kv=MemoryStore(), transport=FakeTransport())
    assert app.forward is None and any("forward test OFF" in n and "no LIVE_DEMO_FORWARD_TEST_APPROVED" in n for n in app.notes)
    assert not app.executor.d.risk.approvals.is_approved("ema_trend", "1")                          # nothing was approved either


async def test_forward_test_on_registers_fourteen_variants_and_approves_only_the_registered_strategies_in_memory(fresh_db_url, tmp_path, monkeypatch):
    monkeypatch.setenv("FORWARD_TEST", "1")
    monkeypatch.setenv("FORWARD_COOLDOWN_S", "3600")
    root = forward_root(tmp_path)
    app = appmod.build(settings_for(fresh_db_url), root=root, kv=MemoryStore(), transport=FakeTransport())
    assert app.forward is not None and any("FORWARD TEST ON" in n for n in app.notes) and app.forward.cooldown_s == 3600
    keys = [v.key for v in app.forward.ledger.variants]
    assert len([k for k in keys if not k.startswith("control_random")]) == 14 and len(keys) == 16
    from derivbot.signals.strategies import default_strategies
    for s in default_strategies().values():
        assert app.executor.d.risk.approvals.is_approved(s.id, s.version)
    assert not app.executor.d.risk.approvals.is_approved("pipeline_soak", "1")
    assert json.loads((root / "config" / "strategies.json").read_text()) == {"approved": {}}        # the owner's file is never touched
    assert app.status()["signals"] == "forward"


async def test_live_ticks_reach_the_ledger_and_the_summary_shows_counts_only(fresh_db_url, tmp_path, monkeypatch):
    monkeypatch.setenv("FORWARD_TEST", "1")
    root = forward_root(tmp_path)
    async with FakeDeriv(balance=2500.0) as fake:
        transport, port = FakeTransport(), free_port()
        app = appmod.build(settings_for(fresh_db_url), root=root, kv=MemoryStore(), transport=transport,
                           policy=EndpointPolicy("ws", "127.0.0.1", fake.port), otp_provider=FakeOtp(fake), flag_path=tmp_path / "KILL")
        halt, task = await start(app, port)
        for _ in range(200):
            if app.forward.ledger._abs.get("1HZ100V", -1) >= 3 or app.forward.ledger.resets:
                break
            await asyncio.sleep(0.05)
        assert app.forward.ledger._abs["1HZ100V"] >= 3 or app.forward.ledger.resets >= 1              # ticks arrived through the session
        async with httpx.AsyncClient() as client:
            body = (await client.get(f"http://127.0.0.1:{port}/api/summary", headers={"Authorization": f"Bearer {TOKEN}"})).json()
        f = body["forward"]
        assert f["n_eval"] == 7800 and len(f["variants"]) == 16
        assert all(set(v) == {"key", "control", "completed", "discarded"} for v in f["variants"])       # counts only: no outcome fields
        await stop(halt, task)
