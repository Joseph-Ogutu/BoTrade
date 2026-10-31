"""Postgres fixtures. Integration tests need the local container from scripts/dev_up.sh.

If it is not reachable the tests SKIP and say so; a skipped database test is not evidence of anything.
"""

import os
import secrets
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
HOST, PORT = "127.0.0.1", 55432


def _admin_url() -> str:
    pw_file = ROOT / "dev-local" / "postgres.password"
    if not pw_file.exists():
        pytest.skip("dev-local/postgres.password missing: run scripts/dev_up.sh")
    return f"postgresql+psycopg://bot:{pw_file.read_text().strip()}@{HOST}:{PORT}/postgres"


@pytest.fixture(scope="module")
def pg_url():
    admin = create_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"local Postgres not reachable ({type(exc).__name__}): run scripts/dev_up.sh")
    name = "t_" + secrets.token_hex(6)
    with admin.connect() as c:
        c.execute(text(f"CREATE DATABASE {name}"))
    url = _admin_url().rsplit("/", 1)[0] + "/" + name
    prev = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    yield url
    if prev is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = prev
    with admin.connect() as c:
        c.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
    admin.dispose()


@pytest.fixture()
def conn(pg_url):
    engine = create_engine(pg_url)
    with engine.connect() as c:
        yield c
        c.rollback()
    engine.dispose()
