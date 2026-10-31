"""Process logging setup: redaction on, noisy transport loggers capped.

The `websockets` library logs the full request URL (including ?otp=...) at DEBUG,
which would write an OTP URL to the logs. quiet_transport_loggers() prevents that
regardless of the application's own log level.
"""

from __future__ import annotations

import logging
import sys

from derivbot.common.redaction import RedactingFilter, install_redaction

_TRANSPORT_LOGGERS = ("websockets", "websockets.client", "websockets.server", "asyncio")


def quiet_transport_loggers() -> None:
    for name in _TRANSPORT_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s %(message)s"))
    handler.addFilter(RedactingFilter())
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), handlers=[handler], force=True)
    install_redaction()
    quiet_transport_loggers()
