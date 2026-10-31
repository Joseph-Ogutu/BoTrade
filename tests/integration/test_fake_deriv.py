"""Tests of the fake server itself, so adapter tests can trust its failure injection."""

import asyncio
import json

import pytest
import websockets

from tests.fakes.fake_deriv import Fault, FakeDeriv


async def send_recv(ws, req, timeout=2.0):
    await ws.send(json.dumps(req))
    return json.loads(await asyncio.wait_for(ws.recv(), timeout))


async def proposal(ws, amount=1.0, req_id=1):
    return await send_recv(ws, {"proposal": 1, "subscribe": 1, "req_id": req_id, "amount": amount,
                                "basis": "stake", "contract_type": "DIGITEVEN", "currency": "USD",
                                "duration": 5, "duration_unit": "t", "underlying_symbol": "1HZ100V"})


async def test_ping_balance_and_tick_stream():
    async with FakeDeriv() as fake:
        async with websockets.connect(fake.url) as ws:
            assert (await send_recv(ws, {"ping": 1, "req_id": 1}))["msg_type"] == "ping"
            bal = await send_recv(ws, {"balance": 1, "subscribe": 1, "req_id": 2})
            assert bal["balance"]["currency"] == "USD" and bal["req_id"] == 2
            await ws.send(json.dumps({"ticks": "1HZ100V", "subscribe": 1, "req_id": 3}))
            epochs = [json.loads(await asyncio.wait_for(ws.recv(), 2))["tick"]["epoch"] for _ in range(5)]
            assert epochs == sorted(epochs) and len(set(epochs)) == 5


async def test_proposal_buy_portfolio_settle_flow():
    async with FakeDeriv(r=0.95, settle_after=0.05) as fake:
        async with websockets.connect(fake.url) as ws:
            p = await proposal(ws, amount=2.0)
            assert p["proposal"]["payout"] == pytest.approx(3.9)
            pid = p["proposal"]["id"]
            buy = await send_recv(ws, {"buy": pid, "price": 2.0, "req_id": 2})
            cid = buy["buy"]["contract_id"]
            pf = await send_recv(ws, {"portfolio": 1, "req_id": 3})
            assert [c["contract_id"] for c in pf["portfolio"]["contracts"]] == [cid]
            await ws.send(json.dumps({"proposal_open_contract": 1, "contract_id": cid, "subscribe": 1,
                                      "req_id": 4}))
            final = None
            for _ in range(50):
                msg = json.loads(await asyncio.wait_for(ws.recv(), 2))
                if msg["proposal_open_contract"]["is_sold"]:
                    final = msg["proposal_open_contract"]
                    break
            assert final and final["status"] == "won" and final["profit"] == "1.90"
            pf2 = await send_recv(ws, {"portfolio": 1, "req_id": 5})
            assert pf2["portfolio"]["contracts"] == []


async def test_proposal_cannot_be_bought_twice():
    async with FakeDeriv() as fake:
        async with websockets.connect(fake.url) as ws:
            pid = (await proposal(ws))["proposal"]["id"]
            ok = await send_recv(ws, {"buy": pid, "price": 1.0, "req_id": 2})
            again = await send_recv(ws, {"buy": pid, "price": 1.0, "req_id": 3})
            assert "buy" in ok and "error" in again
            assert len(fake.contracts) == 1


async def test_drop_after_processing_loses_reply_but_purchase_survives_reconnect():
    """The BUY_UNKNOWN scenario: server accepted the buy, client never saw the reply."""
    async with FakeDeriv() as fake:
        fake.inject(Fault("buy", "drop_after"))
        async with websockets.connect(fake.url) as ws:
            pid = (await proposal(ws))["proposal"]["id"]
            await ws.send(json.dumps({"buy": pid, "price": 1.0, "req_id": 2}))
            with pytest.raises(websockets.ConnectionClosed):
                await asyncio.wait_for(ws.recv(), 2)
        assert len(fake.contracts) == 1
        async with websockets.connect(fake.url) as ws2:
            pf = await send_recv(ws2, {"portfolio": 1, "req_id": 1})
            assert len(pf["portfolio"]["contracts"]) == 1
        assert fake.connections == 2


async def test_drop_before_processing_places_nothing():
    async with FakeDeriv() as fake:
        fake.inject(Fault("buy", "drop_before"))
        async with websockets.connect(fake.url) as ws:
            pid = (await proposal(ws))["proposal"]["id"]
            await ws.send(json.dumps({"buy": pid, "price": 1.0, "req_id": 2}))
            with pytest.raises(websockets.ConnectionClosed):
                await asyncio.wait_for(ws.recv(), 2)
        assert fake.contracts == {}


async def test_injected_error_and_skip_counter():
    async with FakeDeriv() as fake:
        fake.inject(Fault("proposal", "error", skip=1, code="FakeRateLimit", message="slow down"))
        async with websockets.connect(fake.url) as ws:
            first = await proposal(ws, req_id=1)
            second = await proposal(ws, req_id=2)
            third = await proposal(ws, req_id=3)
            assert "proposal" in first and "proposal" in third
            assert second["error"]["code"] == "FakeRateLimit" and second["req_id"] == 2


async def test_duplicate_reply_is_sent_twice():
    async with FakeDeriv() as fake:
        fake.inject(Fault("ping", "duplicate"))
        async with websockets.connect(fake.url) as ws:
            await ws.send(json.dumps({"ping": 1, "req_id": 1}))
            a = json.loads(await asyncio.wait_for(ws.recv(), 2))
            b = json.loads(await asyncio.wait_for(ws.recv(), 2))
            assert a == b


async def test_delay_fault_delays_reply():
    async with FakeDeriv() as fake:
        fake.inject(Fault("ping", "delay", seconds=0.3))
        async with websockets.connect(fake.url) as ws:
            loop = asyncio.get_running_loop()
            t0 = loop.time()
            await send_recv(ws, {"ping": 1, "req_id": 1})
            assert loop.time() - t0 >= 0.29


async def test_hold_fault_reorders_replies():
    async with FakeDeriv() as fake:
        fake.inject(Fault("ping", "hold"))
        async with websockets.connect(fake.url) as ws:
            await ws.send(json.dumps({"ping": 1, "req_id": 1}))
            await ws.send(json.dumps({"balance": 1, "req_id": 2}))
            first = json.loads(await asyncio.wait_for(ws.recv(), 2))
            second = json.loads(await asyncio.wait_for(ws.recv(), 2))
            assert (first["req_id"], second["req_id"]) == (2, 1)


async def test_tick_stream_drops_after_n_ticks():
    async with FakeDeriv() as fake:
        fake.drop_ticks_after = 3
        async with websockets.connect(fake.url) as ws:
            await ws.send(json.dumps({"ticks": "1HZ100V", "subscribe": 1, "req_id": 1}))
            got = 0
            with pytest.raises(websockets.ConnectionClosed):
                while True:
                    await asyncio.wait_for(ws.recv(), 2)
                    got += 1
            assert got == 3


async def test_handshake_rejection_returns_http_status():
    async with FakeDeriv() as fake:
        fake.reject_handshake_status = 401
        with pytest.raises(websockets.InvalidStatusCode) as ei:
            async with websockets.connect(fake.url):
                pass
        assert ei.value.status_code == 401
