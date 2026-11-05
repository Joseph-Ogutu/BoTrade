import json
import urllib.error
from decimal import Decimal as D
from pathlib import Path

import pytest

from derivbot.backtest.permit import Approval, RealDataNotApproved
from derivbot.config.risk import StrategyApprovals
from derivbot.deriv import messages as m
from derivbot.execution.feeds import SignalRunner, TickFreshness
from derivbot.healthcheck_bot import evaluate
from derivbot.kv.store import MemoryStore
from derivbot.opsctl import call
from derivbot.opsctl import main as ops_main
from derivbot.signals.base import Signal

APPROVAL = Approval("2026-11-16", "Joseph", "STRUCTURE_FOUND")


def tick_msg(symbol="1HZ100V", epoch=100, quote="100.50"):
    return {"msg_type": "tick", "echo_req": {}, "tick": {"symbol": symbol, "epoch": epoch, "quote": float(quote), "pip_size": 2}}


def test_tick_freshness_records_arrival_times_only():
    now = {"t": 1000.0}
    kv = MemoryStore()
    f = TickFreshness(["1HZ100V"], clock=lambda: now["t"], kv=kv)
    assert f.age("1HZ100V") is None
    f.handle(tick_msg(epoch=123))
    now["t"] += 4.5
    assert f.age("1HZ100V") == pytest.approx(4.5) and kv.get("tick:last:1HZ100V") == "123"
    f.handle({"msg_type": "tick", "error": {"message": "boom"}})            # an error message is ignored, not fatal
    f.handle({"msg_type": "balance"})
    assert f.age("R_100") is None


class Fixed:
    id, version, duration_ticks, warmup_ticks, params = "fixed", "1", 5, 0, {}

    def signal(self, w):
        return Signal("CALL") if w.index % 3 == 0 else None


def test_the_signal_runner_cannot_exist_without_the_owners_real_data_approval():
    for bad in (None, "yes", True):
        with pytest.raises(RealDataNotApproved):
            SignalRunner(bad, StrategyApprovals(approved={"fixed": "1"}), {"fixed": Fixed()}, "1HZ100V", "ACC", lambda r: None)


def test_with_approval_only_approved_strategies_run_and_intent_keys_are_deterministic():
    got = []
    r = SignalRunner(APPROVAL, StrategyApprovals(approved={"fixed": "1"}), {"fixed": Fixed(), "other": type("O", (Fixed,), {"id": "other"})()},
                     "1HZ100V", "ACC", got.append)
    assert r.active_strategies == ["fixed"]
    for i in range(1, 7):
        r.on_tick(m.Tick(symbol="1HZ100V", epoch=1000 + i, quote=D("100.50"), pip_size=2))
    assert [g.idempotency_key for g in got] == ["fixed:1:1HZ100V:1003", "fixed:1:1HZ100V:1006"]
    assert all(g.mode == "demo" and g.strategy_id == "fixed" for g in got)


def test_other_symbols_are_ignored_and_nothing_is_approved_by_default():
    got = []
    r = SignalRunner(APPROVAL, StrategyApprovals(), {"fixed": Fixed()}, "1HZ100V", "ACC", got.append)
    assert r.active_strategies == []
    for i in range(1, 10):
        r.on_tick(m.Tick(symbol="1HZ100V", epoch=i, quote=D("1.00"), pip_size=2))
    r2 = SignalRunner(APPROVAL, StrategyApprovals(approved={"fixed": "1"}), {"fixed": Fixed()}, "1HZ100V", "ACC", got.append)
    for i in range(1, 10):
        r2.on_tick(m.Tick(symbol="R_100", epoch=i, quote=D("1.00"), pip_size=2))
    assert got == []


def test_signal_runner_module_is_the_only_live_tick_consumer_and_needs_the_permit():
    text = (Path(__file__).resolve().parents[2] / "src/derivbot/execution/feeds.py").read_text()
    assert "RealDataNotApproved" in text and "isinstance(approval, Approval)" in text


# ------------------------------------------------------------------ opsctl and bot healthcheck

class Resp:
    def __init__(self, status, body):
        self.status, self._b = status, body

    def read(self):
        return self._b.encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_opsctl_sends_the_bearer_token_and_the_reason_and_never_prints_the_token(capsys):
    seen = {}

    def opener(req, timeout):
        seen.update(url=req.full_url, method=req.get_method(), auth=req.headers["Authorization"], body=req.data)
        return Resp(200, '{"engaged": true}')

    rc = ops_main(["kill", "--reason", "drill"], env={"ADMIN_API_TOKEN": "FAKE-TOKEN-XYZ-123456"}, opener=opener)
    out = capsys.readouterr()
    assert rc == 0 and seen["url"] == "http://127.0.0.1:8787/kill" and seen["method"] == "POST"
    assert seen["auth"] == "Bearer FAKE-TOKEN-XYZ-123456" and json.loads(seen["body"]) == {"reason": "drill"}
    assert "FAKE-TOKEN" not in out.out + out.err


def test_opsctl_reads_the_token_from_a_file_and_fails_clearly_without_one(tmp_path, capsys):
    f = tmp_path / "tok"
    f.write_text("FAKE-FILE-TOKEN-0000\n")
    assert ops_main(["status"], env={"ADMIN_API_TOKEN_FILE": str(f)}, opener=lambda r, timeout: Resp(200, "{}")) == 0
    assert ops_main(["status"], env={}) == 2
    assert "not set" in capsys.readouterr().err


def test_opsctl_reports_http_errors_and_unreachable_api():
    def forbidden(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, __import__("io").BytesIO(b'{"detail":"invalid token"}'))

    assert call("GET", "/status", "t", opener=forbidden)[0] == 401
    assert ops_main(["status"], env={"ADMIN_API_TOKEN": "t"}, opener=forbidden) == 1

    def down(req, timeout):
        raise urllib.error.URLError("refused")

    code, body = call("GET", "/status", "t", opener=down)
    assert code == 0 and "cannot reach" in body


def test_bot_healthcheck_decision():
    assert evaluate(True, 5.0) == (True, "ok")
    assert evaluate(False, 5.0)[0] is False and evaluate(True, None)[0] is False and evaluate(True, 61.0)[0] is False


def test_the_runner_digit_keeps_the_trailing_zero():
    seen = []

    class Spy(Fixed):
        def signal(self, w):
            seen.append(w.last_digit)
            return None

    r = SignalRunner(APPROVAL, StrategyApprovals(approved={"fixed": "1"}), {"fixed": Spy()}, "1HZ100V", "ACC", lambda q: None)
    r.on_tick(m.Tick(symbol="1HZ100V", epoch=1, quote=D("1310.7"), pip_size=2))       # arrives as 1310.7 but is 1310.70
    r.on_tick(m.Tick(symbol="1HZ100V", epoch=2, quote=D("1310.75"), pip_size=2))
    assert seen == [0, 5]


def test_last_tick_keeps_the_quote_at_exactly_pip_size_decimals_for_display():
    f = TickFreshness(["1HZ100V"])
    f.handle(tick_msg(epoch=5, quote="1310.70"))
    assert f.last_tick["1HZ100V"] == ("1310.70", 5, 2)         # the trailing zero is kept (it is the last digit)
    f.handle(tick_msg(epoch=6, quote="1310"))
    assert f.last_tick["1HZ100V"][0] == "1310.00"
