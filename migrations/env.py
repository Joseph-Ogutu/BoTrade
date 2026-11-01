import os

from alembic import context
from sqlalchemy import create_engine, pool

config = context.config


def _url() -> str:
    """DATABASE_URL, or the same assembly from POSTGRES_* and *_FILE that the application uses. Never a URL from a file in
    the repository."""
    from derivbot.config.settings import assemble_urls, resolve_file_secrets

    url = assemble_urls(resolve_file_secrets(dict(os.environ))).get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL (or POSTGRES_HOST and friends) is not set")
    return url


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("offline migrations are not supported")
run_migrations_online()
