"""Executor test environment: fresh Postgres database per test, real fake-Deriv server, in-memory Redis/kill switch."""

import secrets
from dataclasses import dataclass, field
from decimal import Decimal as D
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text

from derivbot.config.risk import StrategyApprovals, parse_risk_config
from derivbot.db.config_store import store_config
from derivbot.deriv.session import DerivSession
from derivbot.execution.executor import ConnInfo, Executor, ExecutorDeps
from derivbot.execution.tracker import ContractTracker
from derivbot.killswitch.switch import KillSwitch, MemoryEventLog
from derivbot.kv.store import MemoryStore
from derivbot.risk.engine import IntentRequest, RiskEngine
from tests.fakes.fake_deriv import FakeDeriv
from tests.unit.test_config import VALID

ROOT = Path(__file__).resolve().parents[2]
HOST, PORT = "127.0.0.1", 55432

EXEC_CFG = {**VALID, "max_open_positions": 3, "max_trades_per_hour": 100, "max_proposal_price_slippage": "0.02",
            "min_net_payout_ratio": "0.80", "reconcile_max_age_s": 3600, "stale_data_max_age_s": 3600}


def admin_url() -> str:
    pw = ROOT / "dev-local" / "postgres.password"
    if not pw.exists():
        pytest.skip("run scripts/dev_up.sh")
    return f"postgresql+psycopg://bot:{pw.read_text().strip()}@{HOST}:{PORT}/postgres"


@pytest.fixture()
def fresh_db_url(monkeypatch):
    admin = create_engine(admin_url(), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"local Postgres not reachable ({type(exc).__name__})")
    name = "x_" + secrets.token_hex(6)
    with admin.connect() as c:
        c.execute(text(f"CREATE DATABASE {name}"))
    url = admin_url().rsplit("/", 1)[0] + "/" + name
    monkeypatch.setenv("DATABASE_URL", url)
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    yield url
    with admin.connect() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
    admin.dispose()


@dataclass
class Env:
    engine: object
    fake: FakeDeriv
    session: DerivSession
    tracker: ContractTracker
    kill_switch: KillSwitch
    executor: Executor
    deps: ExecutorDeps
    info: dict
    alerts: list = field(default_factory=list)
    holder: dict = field(default_factory=dict)

    def intent(self, key="k1", **kw):
        base = dict(idempotency_key=key, symbol="1HZ100V", contract_type="DIGITEVEN", strategy_id="ema_trend",
                    strategy_version="1", mode="demo", account_id="ACC")
        base.update(kw)
        return IntentRequest(**base)

    def q(self, sql, **params):
        with self.engine.connect() as c:
            return c.execute(text(sql), params).all()

    def state_of(self, intent_id):
        return self.q("SELECT state FROM order_intents WHERE id = :i", i=intent_id)[0][0]

    async def new_session(self):
        """Replace the session with a fresh connection (a reconnect). The old one is closed."""
        old = self.holder["session"]
        await old.close()
        s = DerivSession(lambda: _async_url(self.fake.url))
        s.on("proposal_open_contract", self.tracker.handle)
        await s.connect()
        self.holder["session"] = s
        self.session = s
        return s


async def _async_url(url):
    return url


@pytest_asyncio.fixture()
async def env(fresh_db_url, tmp_path):
    engine = create_engine(fresh_db_url)
    cfg = parse_risk_config(EXEC_CFG)
    with engine.begin() as c:
        cv = store_config(c, "risk", cfg)
    fake = await FakeDeriv(settle_after=0.15).start()
    tracker = ContractTracker()
    session = DerivSession(lambda: _async_url(fake.url))
    session.on("proposal_open_contract", tracker.handle)
    await session.connect()
    holder = {"session": session}
    info = {"v": ConnInfo("demo", True, D("1000"), "USD", "VRTC-FAKE")}
    alerts = []
    ks = KillSwitch(MemoryStore(), MemoryEventLog(), tmp_path / "KILL")
    deps = ExecutorDeps(
        engine=engine, risk=RiskEngine(cfg, StrategyApprovals(approved={"ema_trend": "1"})), config_version_id=cv,
        kill_switch=ks, kv=MemoryStore(), tracker=tracker, get_session=lambda: holder["session"],
        get_conn_info=lambda: info["v"], last_tick_age=lambda: 1.0, last_reconcile_age=lambda: 1.0,
        api_min_stake=lambda sym: D("0.35"), account_id="ACC", on_alert=lambda lvl, txt: alerts.append((lvl, txt)),
        request_timeout=1.0, buy_unknown_window_s=60.0)
    ex = Executor(deps)
    e = Env(engine, fake, session, tracker, ks, ex, deps, info, alerts, holder)
    yield e
    await ex.shutdown()
    await holder["session"].close()
    await fake.stop()
    engine.dispose()
