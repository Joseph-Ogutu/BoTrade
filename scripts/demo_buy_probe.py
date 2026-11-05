"""ONE demo contract through the real executor path, to verify buy, settlement, portfolio and profit_table shapes.

DEMO ONLY: the OTP provider refuses any non-demo URL, the connect hook proves the endpoint is demo and the balance loginid
equals the configured demo account, and risk rule 2 re-checks it. Stake is capped at 0.35 USD (the observed minimum) by a
PROBE-ONLY risk config defined in this file; it is not config/risk.json and sets no production value. The probe strategy
"manual_probe" is approved in memory only and never written to config/strategies.json. It does not use any signal.

A scratch Postgres database is created in the local dev container and dropped at the end.
Requires the explicit flag --yes-one-demo-buy. Full redacted traffic goes to data/private/ (git-ignored).
"""
import asyncio
import json
import secrets
import sys
import time
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
from derivbot.common.redaction import redact_obj, register_secret  # noqa: E402
from derivbot.config.risk import StrategyApprovals, parse_risk_config  # noqa: E402
from derivbot.config.settings import load_settings, parse_env_file  # noqa: E402
from derivbot.db.config_store import store_config  # noqa: E402
from derivbot.deriv import messages as m  # noqa: E402
from derivbot.deriv.ratelimit import DEFAULT_LIMITS, RateLimiter  # noqa: E402
from derivbot.deriv.session import DerivSession  # noqa: E402
from derivbot.execution.executor import Executor, ExecutorDeps  # noqa: E402
from derivbot.execution.feeds import TickFreshness  # noqa: E402
from derivbot.execution.minstake import MinStakeCache  # noqa: E402
from derivbot.execution.reconciler import Reconciler  # noqa: E402
from derivbot.execution.tracker import ContractTracker  # noqa: E402
from derivbot.guard.endpoint import PROD_POLICY, classify_url  # noqa: E402
from derivbot.guard.otp import HttpOtpProvider  # noqa: E402
from derivbot.guard.verify import DemoConnectionVerifier  # noqa: E402
from derivbot.killswitch.switch import KillSwitch, MemoryEventLog  # noqa: E402
from derivbot.kv.store import MemoryStore  # noqa: E402
from derivbot.risk.engine import IntentRequest, RiskEngine  # noqa: E402

SYMBOL = "1HZ100V"
PROBE_RISK = {
    "risk_per_trade_pct": "0.01", "max_stake_abs": "0.35", "min_stake_abs": None, "max_daily_loss_abs": "1",
    "max_daily_loss_pct_of_start_balance": None, "max_consecutive_losses": 1, "max_open_positions": 1,
    "max_trades_per_hour": 1, "max_proposal_price_slippage": "0.02", "stale_data_max_age_s": 30,
    "min_net_payout_ratio": "0.80", "reconcile_max_age_s": 300, "recent_duplicate_window_s": 30, "daily_reset_utc_hour": 0,
}


async def main() -> int:
    if "--yes-one-demo-buy" not in sys.argv:
        print("refusing: pass --yes-one-demo-buy to place ONE 0.35 USD contract on the DEMO account")
        return 2
    env = parse_env_file(ROOT / ".env")
    env["DERIV_PAT_DEMO"] = (ROOT / "secrets" / "deriv_pat_demo").read_text().strip()
    pw = (ROOT / "dev-local" / "postgres.password").read_text().strip()
    base = f"postgresql+psycopg://bot:{pw}@127.0.0.1:55432"
    env["DATABASE_URL"], env["REDIS_URL"] = base + "/derivbot", "redis://127.0.0.1:56379/0"
    settings = load_settings(env)
    register_secret(env["DERIV_PAT_DEMO"])
    register_secret(pw)
    account = settings.deriv_account_id_demo
    assert account and settings.trading_mode == "demo"

    scratch = "probe_" + secrets.token_hex(5)
    admin = create_engine(base + "/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f"CREATE DATABASE {scratch}"))
    import os
    os.environ["DATABASE_URL"] = f"{base}/{scratch}"
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    engine = create_engine(f"{base}/{scratch}")
    trace: list[dict] = []
    try:
        cfg = parse_risk_config(PROBE_RISK)
        with engine.begin() as c:
            cv = store_config(c, "risk", cfg)
        provider = HttpOtpProvider.from_settings(settings)
        session = DerivSession(provider.get_url, limiter=RateLimiter(DEFAULT_LIMITS),
                               endpoint_classifier=partial(classify_url, policy=PROD_POLICY))
        tracker, fresh, minstake = ContractTracker(), TickFreshness([SYMBOL]), MinStakeCache([SYMBOL])
        verifier = DemoConnectionVerifier(account)
        session.on("proposal_open_contract", tracker.handle)
        session.on("proposal_open_contract", lambda raw: trace.append({"stream": redact_obj(raw)}))
        session.on("tick", fresh.handle)
        orig = session.request

        async def recording(req, timeout=10.0):
            reply = await orig(req, timeout)
            if next(iter(req)) in ("buy", "portfolio", "profit_table"):
                trace.append({"request": redact_obj(req), "reply": redact_obj(reply)})
            return reply

        session.request = recording                                  # type: ignore[method-assign]
        ks = KillSwitch(MemoryStore(), MemoryEventLog(), ROOT / "data" / "private" / "probe_KILL")
        box: dict = {}
        deps = ExecutorDeps(
            engine=engine, risk=RiskEngine(cfg, StrategyApprovals(approved={"manual_probe": "1"})), config_version_id=cv,
            kill_switch=ks, kv=MemoryStore(), tracker=tracker, get_session=lambda: session, get_conn_info=verifier.get_info,
            last_tick_age=lambda: fresh.age(SYMBOL), last_reconcile_age=lambda: box["r"].age_s(), api_min_stake=minstake.get,
            account_id=account, on_alert=lambda lvl, txt: print("ALERT", lvl, txt))
        ex = Executor(deps)
        box["r"] = Reconciler(ex, interval_s=60.0)
        await session.connect()
        for hook in (verifier.on_connected, box["r"].on_connected, minstake.on_connected, fresh.on_connected):
            await hook(session)
        print("connected; demo verified:", verifier.get_info().endpoint_mode, verifier.get_info().account_is_demo,
              "| min stake:", minstake.get(SYMBOL), "| reconcile age:", box["r"].age_s())
        for _ in range(40):
            if fresh.age(SYMBOL) is not None:
                break
            await asyncio.sleep(0.25)
        t0 = time.time()
        res = await ex.submit(IntentRequest(f"probe-{secrets.token_hex(4)}", SYMBOL, "CALL", "manual_probe", "1", "demo", account),
                              5, "t", True, 120.0)
        print(f"RESULT after {time.time() - t0:.1f}s:", res)
        await asyncio.sleep(1)
        for req in (m.req_portfolio(session.next_req_id()), m.req_profit_table(session.next_req_id(), limit=3)):
            await session.request(req)
        with engine.connect() as c:
            events = [dict(r._mapping) for r in c.execute(text("SELECT * FROM order_events ORDER BY id"))]
            decisions = [dict(r._mapping) for r in c.execute(text("SELECT approved, rule_results FROM risk_decisions"))]
        print("state transitions:", [(e.get("from_state"), e.get("to_state")) for e in events])
        print("risk decisions approved:", [d["approved"] for d in decisions])
        await session.close()
        out = {"result": str(res), "events": events, "trace": trace}
        outdir = ROOT / "data" / "private"
        outdir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        (outdir / f"demo_buy_{stamp}.json").write_text(json.dumps(redact_obj(out), indent=2, default=str))
        print("redacted traffic saved to data/private/")
        return 0
    finally:
        engine.dispose()
        with admin.connect() as c:
            c.execute(text(f"DROP DATABASE IF EXISTS {scratch} WITH (FORCE)"))


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
