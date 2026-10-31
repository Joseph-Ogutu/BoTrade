"""Postgres engine (SQLAlchemy 2, psycopg 3). The URL comes from DATABASE_URL, never from a file in the repo."""

from __future__ import annotations

import os

from sqlalchemy import Engine, create_engine


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def make_engine(url: str | None = None) -> Engine:
    return create_engine(url or database_url(), pool_pre_ping=True, future=True)
