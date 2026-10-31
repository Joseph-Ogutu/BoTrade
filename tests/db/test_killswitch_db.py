"""Restart survival against the real Postgres and Redis."""

import pytest
import redis as redis_lib
from sqlalchemy import create_engine, text

from derivbot.killswitch.switch import KillSwitch, PgEventLog
from derivbot.kv.store import RedisStore

pytestmark = pytest.mark.postgres


@pytest.fixture()
def redis_store():
    import uuid

    client = redis_lib.Redis(host="127.0.0.1", port=56379, socket_connect_timeout=1)
    try:
        client.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"local Redis not reachable ({type(exc).__name__})")
    return RedisStore(client, prefix=f"ks:{uuid.uuid4().hex}:")


def new(pg_url, kv, path):
    return KillSwitch(kv, PgEventLog(create_engine(pg_url)), path)


def test_engage_is_logged_in_postgres_and_audit_and_survives_a_restart(pg_url, redis_store, tmp_path):
    path = tmp_path / "KILL"
    ks = new(pg_url, redis_store, path)
    ks.engage("api", "db test")
    eng = create_engine(pg_url)
    with eng.connect() as c:
        row = c.execute(text("SELECT action, source, reason FROM kill_switch_events ORDER BY id DESC LIMIT 1")).one()
        assert tuple(row) == ("engage", "api", "db test")
        assert c.execute(text("SELECT count(*) FROM audit_events WHERE event_type = 'KILL_SWITCH'")).scalar_one() >= 1
    restarted = new(pg_url, redis_store, path)
    restarted.load_on_boot()
    assert restarted.is_engaged()


def test_survives_when_redis_is_flushed_and_the_file_is_gone_because_postgres_remembers(pg_url, redis_store, tmp_path):
    path = tmp_path / "KILL2"
    new(pg_url, redis_store, path).engage("telegram", "kill")
    path.unlink()
    redis_store.set("kill_switch", "0")                     # simulate data loss in Redis
    second = new(pg_url, redis_store, path)
    second.load_on_boot()
    assert second.is_engaged() and path.exists() and redis_store.get("kill_switch") == "1"


def test_release_is_logged_and_a_restart_stays_released(pg_url, redis_store, tmp_path):
    path = tmp_path / "KILL3"
    ks = new(pg_url, redis_store, path)
    ks.engage("api", "x")
    ks.release("api", "all clear after review")
    again = new(pg_url, redis_store, path)
    again.load_on_boot()
    assert not again.is_engaged()
    with create_engine(pg_url).connect() as c:
        assert c.execute(text("SELECT action FROM kill_switch_events ORDER BY id DESC LIMIT 1")).scalar_one() == "release"
