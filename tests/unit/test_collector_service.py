import json

import pytest
from pydantic import ValidationError

from derivbot.collector.config import CollectorConfig, SymbolSpec
from derivbot.collector.heartbeat import Heartbeat, check_health

CFG = {
    "symbols": [
        {"symbol": "1HZ100V", "expected_interval": 1, "always_open": True},
        {"symbol": "R_100", "expected_interval": 2, "always_open": True},
        {"symbol": "frxEURUSD", "expected_interval": 0, "always_open": False},
    ]
}


def test_config_parses_and_exposes_helpers():
    cfg = CollectorConfig.model_validate(CFG)
    assert cfg.symbol_names == ["1HZ100V", "R_100", "frxEURUSD"]
    assert cfg.required_symbols == ["1HZ100V", "R_100"]
    assert cfg.url.startswith("wss://api.derivws.com/")
    assert cfg.health_max_age_s > 0


def test_config_rejects_empty_duplicate_and_bad_values():
    with pytest.raises(ValidationError):
        CollectorConfig.model_validate({"symbols": []})
    with pytest.raises(ValidationError):
        CollectorConfig.model_validate({"symbols": [{"symbol": "A", "always_open": True},
                                                      {"symbol": "A", "always_open": True}]})
    with pytest.raises(ValidationError):
        CollectorConfig.model_validate({**CFG, "health_max_age_s": 0})
    with pytest.raises(ValidationError):
        SymbolSpec(symbol="bad symbol!", expected_interval=1, always_open=True)


def test_config_refuses_authenticated_urls():
    # The collector uses the public endpoint only. Never accept a URL with credentials in it.
    with pytest.raises(ValidationError):
        CollectorConfig.model_validate({**CFG, "url": "wss://api.derivws.com/trading/v1/options/ws/demo?otp=x"})
    with pytest.raises(ValidationError):
        CollectorConfig.model_validate({**CFG, "url": "wss://api.derivws.com/trading/v1/options/ws/real"})


def test_default_config_file_is_valid_and_has_no_secrets():
    from pathlib import Path

    raw = (Path(__file__).resolve().parents[2] / "config" / "collector.json").read_text()
    cfg = CollectorConfig.model_validate(json.loads(raw))
    assert "1HZ100V" in cfg.symbol_names and "R_100" in cfg.symbol_names
    for word in ("token", "otp", "password", "secret", "pat_"):
        assert word not in raw.lower()


class Clock:
    def __init__(self, t=1_000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_heartbeat_records_per_symbol_and_writes_atomically(tmp_path):
    clock = Clock()
    hb = Heartbeat(["1HZ100V", "R_100"], path=tmp_path / "hb.json", clock=clock)
    hb.record("1HZ100V")
    clock.t += 5
    hb.record("1HZ100V")
    hb.record("R_100")
    hb.write()
    data = json.loads((tmp_path / "hb.json").read_text())
    assert data["ticks_total"] == 3
    assert data["last_tick"]["1HZ100V"] == 1005.0
    assert not list(tmp_path.glob("*.tmp"))


def test_heartbeat_log_line_has_counts_and_ages_but_no_secrets():
    clock = Clock()
    hb = Heartbeat(["1HZ100V"], path=None, clock=clock)
    hb.record("1HZ100V")
    clock.t += 3
    line = hb.log_line(connects=2, reconnects=1, errors=0)
    assert line.startswith("HEARTBEAT ")
    assert "ticks_total=1" in line and "age_1HZ100V=3.0" in line and "reconnects=1" in line


def test_health_ok_when_required_symbols_fresh(tmp_path):
    clock = Clock()
    hb = Heartbeat(["1HZ100V", "R_100"], path=tmp_path / "hb.json", clock=clock)
    hb.record("1HZ100V")
    hb.record("R_100")
    hb.write()
    ok, why = check_health(tmp_path / "hb.json", ["1HZ100V", "R_100"], max_age_s=60, now=clock.t + 10)
    assert ok, why


def test_health_fails_when_a_required_symbol_is_stale(tmp_path):
    clock = Clock()
    hb = Heartbeat(["1HZ100V", "R_100"], path=tmp_path / "hb.json", clock=clock)
    hb.record("1HZ100V")
    hb.record("R_100")
    clock.t += 100
    hb.record("1HZ100V")
    hb.write()
    ok, why = check_health(tmp_path / "hb.json", ["1HZ100V", "R_100"], max_age_s=60, now=clock.t)
    assert not ok and "R_100" in why


def test_health_fails_if_required_symbol_never_ticked(tmp_path):
    hb = Heartbeat(["1HZ100V"], path=tmp_path / "hb.json", clock=Clock())
    hb.write()
    ok, why = check_health(tmp_path / "hb.json", ["1HZ100V"], max_age_s=60, now=1_001.0)
    assert not ok and "1HZ100V" in why


def test_health_ignores_closed_market_symbols(tmp_path):
    clock = Clock()
    hb = Heartbeat(["1HZ100V", "frxEURUSD"], path=tmp_path / "hb.json", clock=clock)
    hb.record("1HZ100V")
    hb.write()
    ok, _ = check_health(tmp_path / "hb.json", ["1HZ100V"], max_age_s=60, now=clock.t + 5)
    assert ok


def test_health_fails_on_missing_corrupt_or_stale_file(tmp_path):
    assert not check_health(tmp_path / "nope.json", ["A"], 60, now=1.0)[0]
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert not check_health(bad, ["A"], 60, now=1.0)[0]
    # The file itself must be fresh: a dead process leaves a file that looks fine but old.
    clock = Clock()
    hb = Heartbeat(["A"], path=tmp_path / "hb.json", clock=clock)
    hb.record("A")
    hb.write()
    ok, why = check_health(tmp_path / "hb.json", ["A"], max_age_s=60, now=clock.t + 500)
    assert not ok
