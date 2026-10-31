"""Secret redaction for logs and audit payloads (HANDBOOK 11.3).

Three layers, because no single one is enough:
1. Registered secrets: exact values (PAT, admin token, ...) are removed wherever they appear.
2. Patterns: URL query strings (OTP URLs), Bearer tokens, otp=/token= parameters.
3. Sensitive key names in dicts: token, pat, authorization, otp, password, secret, api key, cookie.

The OTP WebSocket URL is a secret. Never log it; if one slips through, the query string is stripped.
"""

from __future__ import annotations

import logging
import re
from typing import Any

REDACTED = "[REDACTED]"
MIN_SECRET_LEN = 6

_secrets: set[str] = set()

_URL_QUERY = re.compile(r"((?:wss?|https?)://[^\s?\"'<>]+)\?[^\s\"'<>]*")
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=\-]+")
_PARAM = re.compile(r"(?i)\b(otp|token|api_token|pat|password|secret)=[^\s&\"']+")
_KEY_SUBSTR = ("token", "authorization", "password", "passwd", "secret", "apikey", "api_key", "api-key", "cookie")
_KEY_WORD = re.compile(r"(^|[_\-.])(pat|otp)($|[_\-.])")

_STD_ATTRS = frozenset(
    logging.LogRecord("x", 0, "x", 0, "", (), None).__dict__.keys()
) | {"message", "asctime", "taskName"}


def register_secret(value: str | None) -> None:
    """Remember an exact secret value so it is removed from all later output."""
    if value and len(value) >= MIN_SECRET_LEN:
        _secrets.add(value)


def reset_secrets() -> None:
    _secrets.clear()


def _is_sensitive_key(key: object) -> bool:
    k = str(key).lower()
    return any(s in k for s in _KEY_SUBSTR) or bool(_KEY_WORD.search(k))


def redact_text(text: str) -> str:
    for secret in sorted(_secrets, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    text = _URL_QUERY.sub(lambda m: f"{m.group(1)}?{REDACTED}", text)
    text = _BEARER.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    text = _PARAM.sub(lambda m: f"{m.group(1)}={REDACTED}", text)
    return text


def redact_obj(obj: Any) -> Any:
    """Return a redacted deep copy of dicts, lists, tuples and strings."""
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        return {
            k: (REDACTED if _is_sensitive_key(k) and v not in (None, "") else redact_obj(v))
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return type(obj)(redact_obj(v) for v in obj)
    return obj


class RedactingFilter(logging.Filter):
    """Handler filter that redacts message, args, extras and exception text in place."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # malformed format string or args: never fail logging
            message = f"{record.msg!r} {record.args!r}"
        record.msg = redact_text(message)
        record.args = ()
        for key in list(record.__dict__):
            if key not in _STD_ATTRS:
                value = record.__dict__[key]
                record.__dict__[key] = (
                    REDACTED if _is_sensitive_key(key) and value not in (None, "") else redact_obj(value)
                )
        if record.exc_info and not record.exc_text:
            record.exc_text = redact_text(logging.Formatter().formatException(record.exc_info))
        elif record.exc_text:
            record.exc_text = redact_text(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_text(record.stack_info)
        return True


def install_redaction() -> None:
    """Attach the filter to every root handler. Safe to call repeatedly."""
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())


def redact_event_dict(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog-compatible processor (HANDBOOK 11.3)."""
    return redact_obj(event_dict)
