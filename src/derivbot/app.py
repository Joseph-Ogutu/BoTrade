"""The trading service: wires every component together and runs until told to stop.

Start-up order (each step fails loudly rather than degrade quietly):
 1. settings (secrets from *_FILE), risk config (SET_BY_OWNER values must be filled in), strategy approvals;
 2. database reachable and migrated to head; the risk config stored in config_versions;
 3. kill switch restored from whatever survived (any store saying engaged wins);
 4. notifier, admin API on 127.0.0.1, flag-file watcher, heartbeat, daily report, telegram commands;
 5. the Deriv session supervisor: demo-only OTP URL, demo verified at connect, then reconcile, then ticks;
 6. the signal runner ONLY if the owner approved real-data use; otherwise the service trades nothing and says so.

DEMO ONLY. The real endpoint cannot be built or reached (derivbot.guard). Nothing here has run against Deriv: every
authenticated behaviour is UNVERIFIED until demo credentials exist.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, text

from derivbot.api.app import create_app
from derivbot.backtest.permit import RealDataNotApproved, require_real_data_approval
from derivbot.common.logs import setup_logging
from derivbot.config.risk import load_risk_config, load_strategy_approvals
from derivbot.config.settings import Settings, load_settings, parse_env_file
from derivbot.db.config_store import store_config
from derivbot.db.engine import make_engine
from derivbot.deriv.ratelimit import DEFAULT_LIMITS, RateLimiter
from derivbot.deriv.session import DerivSession, SessionSupervisor
from derivbot.execution.executor import Executor, ExecutorDeps
from derivbot.execution.feeds import SignalRunner, TickFreshness
from derivbot.execution.reconciler import Reconciler
from derivbot.execution.tracker import ContractTracker
from derivbot.guard.endpoint import PROD_POLICY, EndpointPolicy, classify_url
from derivbot.guard.otp import HttpOtpProvider
from derivbot.guard.verify import DemoConnectionVerifier
from derivbot.killswitch.switch import KillSwitch, PgEventLog, watch_flag_file
from derivbot.killswitch.telegram_commands import TelegramCommands
from derivbot.kv.store import KV, HeartbeatKV, RedisStore
from derivbot.notify.notifier import Heartbeater, Notifier
from derivbot.notify.report import DailyReporter
from derivbot.notify.telegram import TelegramPoller
from derivbot.notify.transport import FakeTransport, Transport
from derivbot.risk.engine import RiskEngine
from derivbot.signals.strategies import default_strategies

log = logging.getLogger("derivbot.app")
ROOT = Path(__file__).resolve().parents[2]
SYMBOLS = ("1HZ100V", "R_100")


class StartupError(Exception):
    pass


def assert_schema_current(engine: Engine) -> None:
    head = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()
    with engine.connect() as c:
        try:
            current = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
        except Exception as exc:  # noqa: BLE001
            raise StartupError("database is not migrated (no alembic_version table): run the migrate step") from exc
    if current != head:
        raise StartupError(f"database schema is at {current}, code expects {head}: run the migrate step")


def head_commit(root: Path = ROOT) -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


@dataclass
class App:
    settings: Settings
    engine: Engine
    kv: KV
    kill_switch: KillSwitch
    notifier: Notifier
    executor: Executor
    reconciler: Reconciler
    supervisor: SessionSupervisor | None
    verifier: DemoConnectionVerifier
    fresh: TickFreshness
    heartbeater: Heartbeater
    reporter: DailyReporter
    api: Any
    poller: TelegramPoller | None
    signal_runner: SignalRunner | None
    notes: list[str] = field(default_factory=list)

    def status(self) -> dict[str, Any]:
        return {"mode": "demo", "session_connected": bool(self.supervisor and self.supervisor.session),
                "connects": self.supervisor.connects if self.supervisor else 0,
                "last_session_error": self.supervisor.last_error if self.supervisor else None,
                "reconcile_age_s": self.reconciler.age_s(), "signals": "on" if self.signal_runner else "off",
                "notifier": self.notifier.status(), "notes": self.notes}


def build(settings: Settings, *, root: Path = ROOT, kv: KV | None = None, transport: Transport | None = None,
          policy: EndpointPolicy = PROD_POLICY, otp_provider: Any = None, flag_path: Path | None = None) -> App:
    notes: list[str] = []
    risk_cfg = load_risk_config(root / "config" / "risk.json")           # raises RiskConfigError naming every unset value
    approvals = load_strategy_approvals(root / "config" / "strategies.json")
    engine = make_engine(settings.database_url.get_secret_value())
    assert_schema_current(engine)
    with engine.begin() as c:
        cv = store_config(c, "risk", risk_cfg)
    if kv is None:
        import redis

        kv = RedisStore(redis.Redis.from_url(settings.redis_url.get_secret_value(), socket_connect_timeout=3))
    from derivbot.db.audit import append_audit
    from derivbot.guard.real_guard import Mode, resolve_mode

    decision = resolve_mode(settings, repo_root=root, head_commit=head_commit(root), otp_endpoint_mode=None)
    if decision.mode is not Mode.DEMO:                       # unreachable while the build lock is closed; kept as a tripwire
        raise StartupError("mode guard did not resolve to demo")
    with engine.begin() as c:
        append_audit(c, "app", "MODE_GUARD", {"mode": decision.mode.value, "unmet_conditions": decision.failed, "reasons": decision.reasons})
    ks = KillSwitch(kv, PgEventLog(engine), flag_path or Path(os.environ.get("KILL_SWITCH_FILE", "/data/KILL_SWITCH")))
    ks.load_on_boot()
    chat_ids = settings.telegram_allowed_chat_ids
    if transport is None:
        if settings.telegram_bot_token and chat_ids:
            from derivbot.notify.transport import TelegramHttpTransport

            transport = TelegramHttpTransport(settings.telegram_bot_token.get_secret_value())
        else:
            transport = FakeTransport()
            notes.append("telegram not configured: alerts are only logged")
    notifier = Notifier(transport, chat_ids, min_level="info")
    tracker = ContractTracker()
    fresh = TickFreshness(list(SYMBOLS), kv=kv)
    from derivbot.execution.minstake import MinStakeCache

    min_stake = MinStakeCache(SYMBOLS)
    verifier = DemoConnectionVerifier(settings.deriv_account_id_demo or "")
    reconciler_box: dict[str, Reconciler] = {}
    sup_box: dict[str, SessionSupervisor | None] = {"s": None}
    deps = ExecutorDeps(
        engine=engine, risk=RiskEngine(risk_cfg, approvals), config_version_id=cv, kill_switch=ks, kv=kv, tracker=tracker,
        get_session=lambda: sup_box["s"].session if sup_box["s"] else None, get_conn_info=verifier.get_info,
        last_tick_age=lambda: fresh.age(SYMBOLS[0]), last_reconcile_age=lambda: reconciler_box["r"].age_s(),
        api_min_stake=min_stake.get,         # from proposal validation_params on every connect; unknown means no trade
        account_id=settings.deriv_account_id_demo or "", on_alert=notifier.submit)
    executor = Executor(deps)
    reconciler = Reconciler(executor, interval_s=60.0)
    reconciler_box["r"] = reconciler

    supervisor = None
    if otp_provider is None and settings.deriv_app_id_demo and settings.deriv_pat_demo and settings.deriv_account_id_demo:
        otp_provider = HttpOtpProvider.from_settings(settings, policy=policy)
    if otp_provider is not None:
        limiter = RateLimiter(DEFAULT_LIMITS)

        def factory() -> DerivSession:
            s = DerivSession(otp_provider.get_url, limiter=limiter, endpoint_classifier=partial(classify_url, policy=policy))
            s.on("proposal_open_contract", tracker.handle)
            s.on("tick", fresh.handle)
            s.on("balance", lambda msg: _on_balance(verifier, msg))
            return s

        supervisor = SessionSupervisor(factory, on_connected=[verifier.on_connected, reconciler.on_connected, min_stake.on_connected, fresh.on_connected],
                                       on_disconnected=[verifier.on_disconnected, min_stake.on_disconnected])
        sup_box["s"] = supervisor
    else:
        notes.append("no demo credentials: the Deriv session is not started")

    runner = None
    try:
        approval = require_real_data_approval(root)
        runner = SignalRunner(approval, approvals, default_strategies(), SYMBOLS[0], settings.deriv_account_id_demo or "",
                              lambda req: asyncio.get_event_loop().create_task(executor.submit(req)))
        fresh.add_sink(runner.on_tick)
    except RealDataNotApproved as exc:
        notes.append(f"signals off: real-data use not approved ({exc})")

    app_box: dict[str, App] = {}
    api = create_app(ks, settings.admin_api_token.get_secret_value() if settings.admin_api_token else None,
                     lambda: app_box["a"].status())
    cmds = TelegramCommands(ks, set(chat_ids), lambda: app_box["a"].status(), on_unauthorized=lambda c, t: log.warning("telegram: unlisted chat"))
    poller = TelegramPoller(transport, cmds) if (settings.telegram_bot_token and chat_ids) else None
    hb = Heartbeater(HeartbeatKV(kv, ttl_s=60), "bot", notifier, lambda: {"signals": "on" if runner else "off"}, interval_s=10)
    reporter = DailyReporter(notifier, engine, kv, hour_utc=risk_cfg.daily_reset_utc_hour)
    app = App(settings, engine, kv, ks, notifier, executor, reconciler, supervisor, verifier, fresh, hb, reporter, api, poller, runner, notes)
    app_box["a"] = app
    return app


def _on_balance(verifier: DemoConnectionVerifier, raw: dict) -> None:
    from derivbot.deriv import messages as m

    try:
        msg = m.parse_message(raw)
    except Exception:  # noqa: BLE001
        return
    if isinstance(msg, m.BalanceMsg):
        verifier.update_balance(msg.balance.balance, msg.balance.currency)


async def run(app: App, halt: asyncio.Event, api_port: int = 8787) -> None:
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app.api, host="127.0.0.1", port=api_port, log_level="warning"))
    tasks = [asyncio.create_task(c, name=n) for n, c in [
        ("api", server.serve()), ("flag_file", watch_flag_file(app.kill_switch, halt)), ("notifier", app.notifier.run(halt)),
        ("heartbeat", app.heartbeater.run(halt)), ("reconcile", app.reconciler.run_periodic(halt)),
        ("report", _report_loop(app, halt))]]
    if app.supervisor:
        tasks.append(asyncio.create_task(app.supervisor.run(halt), name="session"))
    if app.poller:
        tasks.append(asyncio.create_task(app.poller.run(halt), name="telegram"))
    for note in app.notes:
        log.warning("%s", note)
    app.notifier.submit("info", "trading service started (demo). " + "; ".join(app.notes))
    await halt.wait()
    server.should_exit = True
    await app.executor.shutdown()
    await asyncio.wait(tasks, timeout=10)
    for t in tasks:
        t.cancel()
    app.engine.dispose()


async def _report_loop(app: App, halt: asyncio.Event) -> None:
    while not halt.is_set():
        try:
            await app.reporter.tick()
        except Exception:  # noqa: BLE001
            log.exception("daily report failed")
        try:
            await asyncio.wait_for(halt.wait(), timeout=300)
        except TimeoutError:
            pass


def main() -> int:  # pragma: no cover - entry point
    env = dict(os.environ)
    if os.environ.get("ENV_FILE"):
        env = {**parse_env_file(os.environ["ENV_FILE"]), **env}
    settings = load_settings(env)
    setup_logging(settings.log_level)
    if settings.trading_mode != "demo":
        log.critical("TRADING_MODE is not demo: refusing to start (real trading is not implemented)")
        return 2
    app = build(settings)

    async def amain() -> None:
        halt = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, halt.set)
        await run(app, halt)

    asyncio.run(amain())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
