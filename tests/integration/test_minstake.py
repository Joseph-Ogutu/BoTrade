from decimal import Decimal as D

import pytest

from derivbot.deriv.session import ConnectionLost, DerivSession, RequestTimeout
from derivbot.execution.minstake import MinStakeCache, parse_min_stake
from tests.fakes.fake_deriv import FakeDeriv


def reply(min_value="0.35", sub="5024a4e8-cd23-4cf0-9da8-dd43cf75e321"):
    # shape OBSERVED on the demo endpoint (docs/api-notes.md, first authenticated run)
    return {"msg_type": "proposal", "proposal": {"id": "x", "validation_params": {"stake": {"min": min_value}}},
            "subscription": {"id": sub}}


@pytest.mark.parametrize("r,expected", [
    (reply("0.35"), D("0.35")),
    (reply("1"), D("1")),
    # OBSERVED-ERR: ContractBuyValidationError on a too small stake (docs/api-notes.md, first authenticated run)
    ({"error": {"code": "ContractBuyValidationError"}, "proposal": {}}, None),
    ({"msg_type": "proposal", "proposal": {"id": "x"}}, None),
    ({"msg_type": "proposal"}, None),
    (reply(0.35), None),                 # a float is refused: money is never a float
    (reply(True), None),
    (reply("0"), None),
    (reply("-1"), None),
    (reply("NaN"), None),
    (reply("abc"), None),
    (reply(None), None),
])
def test_parse_min_stake(r, expected):
    assert parse_min_stake(r) == expected


class StubSession:
    def __init__(self, replies):
        self.replies, self.sent, self.requests, self._n = list(replies), [], [], 0

    def next_req_id(self):
        self._n += 1
        return self._n

    async def request(self, req, timeout=10.0):
        self.requests.append(req)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    async def send(self, req):
        self.sent.append(req)


async def test_keeps_the_highest_minimum_across_contract_types():
    s = StubSession([reply("0.35"), reply("0.50")])
    c = MinStakeCache(["A"])
    await c.on_connected(s)
    assert c.get("A") == D("0.50")
    assert [r["contract_type"] for r in s.requests] == ["CALL", "PUT"]
    assert all(r["proposal"] == 1 and r["underlying_symbol"] == "A" for r in s.requests)
    assert not any("buy" in r for r in s.requests + s.sent)       # a probe never buys


async def test_every_probe_subscription_is_forgotten():
    s = StubSession([reply(sub="63e3d750-32bb-4196-9054-361c9855db94"), reply(sub="5024a4e8-cd23-4cf0-9da8-dd43cf75e321")])
    await MinStakeCache(["A"]).on_connected(s)
    assert [x["forget"] for x in s.sent] == ["63e3d750-32bb-4196-9054-361c9855db94", "5024a4e8-cd23-4cf0-9da8-dd43cf75e321"]


async def test_one_unusable_probe_leaves_the_symbol_unknown_not_partially_known():
    s = StubSession([reply("0.35"), {"error": {"message": "boom"}, "proposal": {}}])
    c = MinStakeCache(["A"])
    await c.on_connected(s)
    assert c.get("A") is None


async def test_a_timeout_leaves_the_symbol_unknown_and_other_symbols_are_still_probed():
    s = StubSession([RequestTimeout("t"), reply("0.35"), reply("0.35"), reply("0.40")])
    c = MinStakeCache(["A", "B"])
    await c.on_connected(s)
    assert c.get("A") is None and c.get("B") == D("0.40")


async def test_a_lost_connection_stops_probing_and_leaves_everything_unknown():
    s = StubSession([reply("0.35"), ConnectionLost("gone")])
    c = MinStakeCache(["A", "B"])
    await c.on_connected(s)
    assert c.get("A") is None and c.get("B") is None and len(s.requests) == 2


async def test_disconnect_forgets_what_was_learned():
    c = MinStakeCache(["A"])
    await c.on_connected(StubSession([reply("0.35"), reply("0.35")]))
    assert c.get("A") == D("0.35")
    await c.on_disconnected()
    assert c.get("A") is None


async def test_a_reconnect_replaces_the_old_values():
    c = MinStakeCache(["A"])
    await c.on_connected(StubSession([reply("0.35"), reply("0.35")]))
    await c.on_connected(StubSession([{"error": {"message": "boom"}, "proposal": {}}, reply("0.35")]))
    assert c.get("A") is None


async def _url(u):
    return u


async def test_against_the_fake_server_over_a_real_websocket():
    fake = await FakeDeriv().start()
    try:
        s = DerivSession(lambda: _url(fake.url))
        await s.connect()
        c = MinStakeCache(["1HZ100V", "R_100"])
        await c.on_connected(s)
        assert c.get("1HZ100V") == D("0.35") and c.get("R_100") == D("0.35") and fake.buys_received == 0
        fake.min_stake = None                 # a server that does not state a minimum
        await c.on_connected(s)
        assert c.get("1HZ100V") is None
        await s.close()
    finally:
        await fake.stop()
