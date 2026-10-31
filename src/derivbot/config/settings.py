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


def load_settings(env: Mapping[str, str]) -> Settings:
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
