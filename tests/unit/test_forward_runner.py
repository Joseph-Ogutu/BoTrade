import asyncio
from decimal import Decimal as D

import pytest

from derivbot.deriv import messages as m
from derivbot.forward.permit import ForwardApproval
from derivbot.forward.runner import ForwardRunner
from derivbot.forward.shadow import CONTROL_ID
from derivbot.signals.base import Signal

REG = {"live_order_cooldown_s": 3600, "n_eval": 7800, "family_size_k": 14}
APPROVAL = ForwardApproval("2026-11-06", "Joseph", REG)


class Always:
    id, version, duration_ticks, warmup_ticks = "always_call", "1", 5, 0
    params: dict = {}

    def signal(self, window):
        return Signal("CALL", None)


def tick(i, symbol="1HZ100V"):
    return m.Tick(symbol=symbol, epoch=1000 + i, quote=D("100.00") + i, pip_size=2)


def runner(clock, submit, cooldown=None):
    return ForwardRunner(APPROVAL, {"always_call": Always()}, ["1HZ100V"], engine=None, account_id="DEMO-ACC", submit=submit,
                         clock=clock, cooldown_s=cooldown)


async def test_a_signal_becomes_a_demo_order_with_the_expected_fields_and_a_unique_key():
    sent = []

    async def submit(req):
        sent.append(req)

    t = {"v": 0.0}
    r = runner(lambda: t["v"], submit)
    r.on_tick(tick(0))
    await asyncio.sleep(0)
    assert len(sent) == 1 and r.live_orders == 1
    q = sent[0]
    assert (q.mode, q.account_id, q.symbol, q.contract_type, q.strategy_id, q.strategy_version) == ("demo", "DEMO-ACC", "1HZ100V", "CALL", "always_call", "1")
    assert q.idempotency_key == "fwd:always_call:1HZ100V:1000"                          # variant key plus the decision tick's epoch


async def test_the_cooldown_spaces_live_orders_and_uses_the_registered_default():
    sent = []

    async def submit(req):
        sent.append(req)

    t = {"v": 0.0}
    r = runner(lambda: t["v"], submit)                                          # cooldown comes from the registration: 3600 s
    assert r.cooldown_s == 3600
    for i in range(0, 60):                                                      # many shadow signals within one cooldown window
        t["v"] = float(i)
        r.on_tick(tick(i))
    await asyncio.sleep(0)
    assert len(sent) == 1                                                       # only the first became an order
    t["v"] = 3601.0
    for i in range(60, 70):
        r.on_tick(tick(i))
    await asyncio.sleep(0)
    assert len(sent) == 2


async def test_the_coin_flip_control_never_places_an_order():
    sent = []

    async def submit(req):
        sent.append(req)

    r = ForwardRunner(APPROVAL, {}, ["1HZ100V"], engine=None, account_id="A", submit=submit, cooldown_s=30)
    assert [v.strategy_id for v in r.ledger.variants] == [CONTROL_ID]
    for i in range(400):
        r.on_tick(tick(i))
    await asyncio.sleep(0)
    assert sent == [] and sum(r.ledger.completed.values()) > 0                  # the control still produced ledger contracts


def test_a_runner_cannot_be_built_without_an_owner_approval():
    with pytest.raises(TypeError):
        ForwardRunner(None, {}, ["1HZ100V"], engine=None, account_id="A", submit=lambda r: None)       # type: ignore[arg-type]


async def test_a_disconnect_discards_open_shadow_contracts_and_is_counted(monkeypatch):
    monkeypatch.setattr("derivbot.forward.shadow.CONTROL_RATE", 0.0)                     # the random control must not open a second contract

    async def submit(req):
        pass

    r = runner(lambda: 0.0, submit)
    r.on_tick(tick(0))
    assert r.ledger.open_count() == 1
    await r.on_disconnected()
    assert r.ledger.open_count() == 0 and r.ledger.discarded["always_call:1HZ100V"] == 1 and r.store.pending() == 1
