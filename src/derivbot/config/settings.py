"""Typed process settings from environment variables (names in .env.example). Secrets are SecretStr, hidden from repr
and dumps, and registered with the log redactor the moment they are loaded."""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError, field_validator

from derivbot.common.redaction import register_secret

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


class SettingsError(Exception):
    pass


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trading_mode: Literal["demo", "real"] = "demo"
    allow_real_trading: bool = False
    deriv_app_id_demo: str | None = None
    deriv_pat_demo: SecretStr | None = None
    deriv_account_id_demo: str | None = None
    deriv_app_id_real: str | None = None
    deriv_pat_real: SecretStr | None = None
    deriv_account_id_real: str | None = None
    database_url: SecretStr
    redis_url: SecretStr
    telegram_bot_token: SecretStr | None = None
    telegram_allowed_chat_ids: list[int] = []
    admin_api_token: SecretStr | None = None
    log_level: str = "INFO"

    @field_validator("log_level")
    @classmethod
    def _level(cls, v: str) -> str:
        if v.upper() not in LEVELS:
            raise ValueError(f"log level must be one of {LEVELS}")
        return v.upper()

    def secrets(self) -> list[str]:
        return [v.get_secret_value() for v in (self.deriv_pat_demo, self.deriv_pat_real, self.database_url,
                self.redis_url, self.telegram_bot_token, self.admin_api_token) if v is not None]

    def safe_dump(self) -> dict[str, Any]:
        return {k: ("[REDACTED]" if isinstance(v, SecretStr) else v) for k, v in self.__dict__.items()}


_TRUE, _FALSE = {"1", "true", "yes", "on"}, {"0", "false", "no", "off", ""}


def _bool(name: str, v: str) -> bool:
    if v.strip().lower() in _TRUE:
        return True
    if v.strip().lower() in _FALSE:
        return False
    raise SettingsError(f"{name}: {v!r} is not a boolean")


# Secrets whose absence is safe because every consumer fails closed: no PAT means no Deriv session, no Telegram token
# means alerts are only logged, no admin token means the admin API answers 503 to everything. A MISSING file for one of
# these is treated as "not provided". Every other *_FILE (database and Redis passwords) must exist.
OPTIONAL_SECRETS = frozenset({"DERIV_PAT_DEMO", "DERIV_PAT_REAL", "TELEGRAM_BOT_TOKEN", "ADMIN_API_TOKEN"})


# Only these names can be supplied as NAME_FILE. Any other variable that merely ends in _FILE (a path such as a flag file)
# is left alone, so a path is never mistaken for a secret reference.
FILE_SECRET_NAMES = OPTIONAL_SECRETS | frozenset({"POSTGRES_PASSWORD", "REDIS_PASSWORD", "DATABASE_URL", "REDIS_URL"})


def resolve_file_secrets(env: Mapping[str, str]) -> dict[str, str]:
    """Docker/Compose style: NAME_FILE=/run/secrets/x supplies NAME from a file. Setting both is an error, so a stale
    value cannot silently win. An empty file is "not provided" for the optional secrets (so a fresh checkout can ship empty
    placeholders) and an error for every other secret."""
    out = dict(env)
    for key in [k for k in env if k.endswith("_FILE") and env[k] and k[: -len("_FILE")] in FILE_SECRET_NAMES]:
        name = key[: -len("_FILE")]
        if env.get(name):
            raise SettingsError(f"both {name} and {key} are set")
        try:
            value = Path(env[key]).read_text().strip()
        except FileNotFoundError:
            if name in OPTIONAL_SECRETS:
                continue
            raise SettingsError(f"{key}: the file does not exist") from None
        except OSError as exc:
            raise SettingsError(f"{key}: cannot read the file ({type(exc).__name__})") from None
        if not value:
            if name in OPTIONAL_SECRETS:
                continue
            raise SettingsError(f"{key}: the file is empty")
        out[name] = value
    return out


def assemble_urls(env: dict[str, str]) -> dict[str, str]:
    """Build DATABASE_URL and REDIS_URL from parts when they are not given whole, URL-quoting the passwords."""
    from urllib.parse import quote

    out = dict(env)
    if not out.get("DATABASE_URL") and out.get("POSTGRES_HOST"):
        pw = quote(out.get("POSTGRES_PASSWORD", ""), safe="")
        out["DATABASE_URL"] = (f"postgresql+psycopg://{out.get('POSTGRES_USER', 'bot')}:{pw}@{out['POSTGRES_HOST']}:"
                               f"{out.get('POSTGRES_PORT', '5432')}/{out.get('POSTGRES_DB', 'derivbot')}")
    if not out.get("REDIS_URL") and out.get("REDIS_HOST"):
        pw = quote(out.get("REDIS_PASSWORD", ""), safe="")
        auth = f":{pw}@" if pw else ""
        out["REDIS_URL"] = f"redis://{auth}{out['REDIS_HOST']}:{out.get('REDIS_PORT', '6379')}/0"
    return out


def load_settings(env: Mapping[str, str]) -> Settings:
    env = assemble_urls(resolve_file_secrets(env))

    def opt(name: str) -> str | None:
        v = env.get(name)
        return v if v not in (None, "") else None

    missing = [n for n in ("DATABASE_URL", "REDIS_URL") if not opt(n)]
    if missing:
        raise SettingsError(f"required settings are missing: {missing}")
    try:
        chat_raw = opt("TELEGRAM_ALLOWED_CHAT_IDS")
        chat_ids = [int(x) for x in re.split(r"[,\s]+", chat_raw.strip()) if x] if chat_raw else []
    except ValueError as exc:
        raise SettingsError("TELEGRAM_ALLOWED_CHAT_IDS must be integers separated by commas") from exc
    data: dict[str, Any] = {
        "trading_mode": (opt("TRADING_MODE") or "demo").lower(),
        "allow_real_trading": _bool("ALLOW_REAL_TRADING", env.get("ALLOW_REAL_TRADING", "false")),
        "deriv_app_id_demo": opt("DERIV_APP_ID_DEMO"), "deriv_pat_demo": opt("DERIV_PAT_DEMO"),
        "deriv_account_id_demo": opt("DERIV_ACCOUNT_ID_DEMO"),
        "deriv_app_id_real": opt("DERIV_APP_ID_REAL"), "deriv_pat_real": opt("DERIV_PAT_REAL"),
        "deriv_account_id_real": opt("DERIV_ACCOUNT_ID_REAL"),
        "database_url": opt("DATABASE_URL"), "redis_url": opt("REDIS_URL"),
        "telegram_bot_token": opt("TELEGRAM_BOT_TOKEN"), "telegram_allowed_chat_ids": chat_ids,
        "admin_api_token": opt("ADMIN_API_TOKEN"), "log_level": opt("LOG_LEVEL") or "INFO",
    }
    try:
        s = Settings(**data)
    except ValidationError as exc:
        fields = sorted({str(e["loc"][0]) for e in exc.errors()})
        raise SettingsError(f"invalid settings: {fields}") from None   # never echo input values
    for secret in s.secrets():
        register_secret(secret)
    return s


def parse_env_file(path: str | Path) -> dict[str, str]:
    """Minimal KEY=VALUE parser for entrypoints. Values are never logged."""
    out: dict[str, str] = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        out[k.strip()] = v
    return out
