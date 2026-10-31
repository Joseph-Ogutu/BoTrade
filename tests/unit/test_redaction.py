import io
import logging

import pytest

from derivbot.common import redaction
from derivbot.common.redaction import (
    REDACTED,
    RedactingFilter,
    redact_obj,
    redact_text,
    register_secret,
    reset_secrets,
)

FAKE_PAT = "pat_FAKE_abcdef0123456789"
FAKE_OTP = "FAKEOTP9z8y7x6w5v4"
OTP_URL = f"wss://api.derivws.com/trading/v1/options/ws/demo?otp={FAKE_OTP}"


@pytest.fixture(autouse=True)
def _clean():
    reset_secrets()
    yield
    reset_secrets()


def test_registered_secret_is_removed_from_text():
    register_secret(FAKE_PAT)
    out = redact_text(f"auth with {FAKE_PAT} ok")
    assert FAKE_PAT not in out and REDACTED in out


def test_url_query_string_is_stripped():
    out = redact_text(f"connecting to {OTP_URL} now")
    assert FAKE_OTP not in out
    assert "otp=" not in out
    assert "wss://api.derivws.com/trading/v1/options/ws/demo" in out


def test_bearer_and_otp_param_patterns():
    out = redact_text("Authorization: Bearer abc.DEF-123_xyz and otp=ZZZ999 end")
    assert "abc.DEF-123_xyz" not in out and "ZZZ999" not in out


def test_sensitive_keys_are_redacted_recursively():
    data = {
        "headers": {"Authorization": f"Bearer {FAKE_PAT}", "Deriv-App-ID": "123"},
        "nested": [{"otp": FAKE_OTP}, {"api_token": "x1"}, {"keep": "visible"}],
        "url": OTP_URL,
    }
    out = redact_obj(data)
    flat = repr(out)
    assert FAKE_PAT not in flat and FAKE_OTP not in flat and "x1" not in flat
    assert out["nested"][2]["keep"] == "visible"


def test_redact_obj_does_not_mutate_input():
    data = {"token": "abc123"}
    redact_obj(data)
    assert data["token"] == "abc123"


def test_registered_secret_inside_non_sensitive_key():
    register_secret(FAKE_PAT)
    out = redact_obj({"note": f"used {FAKE_PAT} yesterday"})
    assert FAKE_PAT not in repr(out)


def test_short_secrets_are_ignored_to_avoid_mangling_text():
    register_secret("ab")
    assert redact_text("table about") == "table about"


def _logger_with_filter():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    handler.addFilter(RedactingFilter())
    log = logging.getLogger("test.redaction." + str(id(stream)))
    log.setLevel(logging.DEBUG)
    log.propagate = False
    log.addHandler(handler)
    return log, stream


def test_filter_redacts_message_args_and_extra():
    register_secret(FAKE_PAT)
    log, stream = _logger_with_filter()
    log.info("token=%s url=%s", FAKE_PAT, OTP_URL)
    log.info("plain %s", "x", extra={"otp": FAKE_OTP})
    text = stream.getvalue()
    assert FAKE_PAT not in text and FAKE_OTP not in text


def test_filter_redacts_exception_text():
    register_secret(FAKE_PAT)
    log, stream = _logger_with_filter()
    try:
        raise RuntimeError(f"failed connecting to {OTP_URL} with {FAKE_PAT}")
    except RuntimeError:
        log.exception("boom")
    text = stream.getvalue()
    assert "Traceback" in text
    assert FAKE_PAT not in text and FAKE_OTP not in text


def test_filter_survives_non_string_args():
    log, stream = _logger_with_filter()
    log.info("n=%d d=%s", 5, {"a": 1})
    assert "n=5" in stream.getvalue()


def test_install_redaction_adds_filter_to_root_handlers():
    root = logging.getLogger()
    handler = logging.StreamHandler(io.StringIO())
    root.addHandler(handler)
    try:
        redaction.install_redaction()
        redaction.install_redaction()  # idempotent
        assert sum(isinstance(f, RedactingFilter) for f in handler.filters) == 1
    finally:
        root.removeHandler(handler)


def test_structlog_style_processor():
    register_secret(FAKE_PAT)
    event = {"event": f"using {FAKE_PAT}", "otp": FAKE_OTP}
    out = redaction.redact_event_dict(None, "info", event)
    assert FAKE_PAT not in repr(out) and FAKE_OTP not in repr(out)
