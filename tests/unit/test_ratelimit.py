import asyncio

import pytest

from derivbot.deriv.ratelimit import (
    DEFAULT_LIMITS,
    LimitConfig,
    RateLimiter,
    group_for,
)


class FakeTime:
    def __init__(self):
        self.now = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def make(limits=None, margin=1.0):
    t = FakeTime()
    return RateLimiter(limits or DEFAULT_LIMITS, margin=margin, clock=t.clock, sleep=t.sleep), t


def test_group_mapping_matches_handbook():
    assert group_for("proposal") == "trading"
    assert group_for("proposal_open_contract") == "trading"
    assert group_for("buy") == "trading"
    assert group_for("sell") == "trading"
    assert group_for("balance") == "account"
    assert group_for("statement") == "account"
    assert group_for("portfolio") == "portfolio"
    assert group_for("profit_table") == "portfolio"
    assert group_for("ping") == "ping"
    assert group_for("ticks") == "other"
    assert group_for("something_new") == "other"


def test_default_limits_match_handbook_2_4():
    assert DEFAULT_LIMITS["trading"].per_minute == 360
    assert DEFAULT_LIMITS["trading"].per_hour == 14_400
    assert DEFAULT_LIMITS["account"].per_minute == 100
    assert DEFAULT_LIMITS["portfolio"].per_minute == 30
    assert DEFAULT_LIMITS["other"].per_minute == 220


async def test_calls_under_limit_do_not_wait():
    rl, t = make()
    for _ in range(30):
        await rl.acquire("portfolio")
    assert t.slept == []


async def test_call_over_per_minute_limit_waits_for_window_to_slide():
    rl, t = make({"other": LimitConfig(per_minute=3, per_hour=1000)})
    start = t.now
    for _ in range(3):
        await rl.acquire("ticks")
    await rl.acquire("ticks")
    assert t.now - start >= 60.0
    assert t.now - start < 61.0


async def test_per_hour_limit_is_enforced():
    rl, t = make({"other": LimitConfig(per_minute=1000, per_hour=5)})
    start = t.now
    for _ in range(5):
        await rl.acquire("ticks")
    await rl.acquire("ticks")
    assert t.now - start >= 3600.0


async def test_groups_are_independent():
    rl, t = make({"other": LimitConfig(2, 100), "trading": LimitConfig(2, 100)})
    await rl.acquire("ticks")
    await rl.acquire("ticks")
    await rl.acquire("buy")  # different group, no wait
    assert t.slept == []


async def test_margin_reduces_effective_limit():
    rl, t = make({"other": LimitConfig(per_minute=10, per_hour=1000)}, margin=0.5)
    start = t.now
    for _ in range(5):
        await rl.acquire("ticks")
    assert t.now == start
    await rl.acquire("ticks")
    assert t.now - start >= 60.0


async def test_ping_has_its_own_per_second_budget():
    rl, t = make()
    start = t.now
    for _ in range(10):
        await rl.acquire("ping")
    assert t.now == start
    await rl.acquire("ping")
    assert 0 < t.now - start <= 1.01


async def test_try_acquire_is_nonblocking():
    rl, t = make({"other": LimitConfig(2, 100)})
    assert rl.try_acquire("ticks") is True
    assert rl.try_acquire("ticks") is True
    assert rl.try_acquire("ticks") is False
    assert t.slept == []


async def test_penalty_blocks_group_after_server_rejection():
    rl, t = make()
    rl.penalize("trading", seconds=30)
    start = t.now
    await rl.acquire("buy")
    assert t.now - start >= 30.0
    # other groups unaffected
    before = t.now
    await rl.acquire("ticks")
    assert t.now == before


async def test_concurrent_acquires_never_exceed_limit():
    rl, t = make({"other": LimitConfig(per_minute=5, per_hour=1000)})
    stamps: list[float] = []

    async def worker():
        await rl.acquire("ticks")
        stamps.append(t.now)

    await asyncio.gather(*[worker() for _ in range(12)])
    for i, s in enumerate(stamps):
        in_window = [x for x in stamps if s - 60.0 < x <= s]
        assert len(in_window) <= 5, (i, s)


def test_invalid_config_rejected():
    with pytest.raises(ValueError):
        LimitConfig(per_minute=0, per_hour=10)
    with pytest.raises(ValueError):
        RateLimiter(DEFAULT_LIMITS, margin=0)
