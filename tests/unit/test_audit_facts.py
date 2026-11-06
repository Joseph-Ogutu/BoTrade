import asyncio
from decimal import Decimal as D

from derivbot.audit.__main__ import BACKOFF_S, FACT_KEY, MAX_TRIES, load_exchange
from derivbot.deriv.ratelimit import DEFAULT_LIMITS, group_for
from derivbot.kv.store import MemoryStore


def poc(entry="100.00", exit_="100.50", status="won", ctype="CALL"):
    return {"msg_type": "proposal_open_contract", "proposal_open_contract": {
        "contract_type": ctype, "entry_spot": entry, "exit_spot": exit_, "status": status}}


# OBSERVED-ERR: RateLimit, the error shape seen on the live API for proposal_open_contract (docs/api-notes.md, 2026-11-06)
RATE_LIMIT = {"error": {"code": "RateLimit", "message": "You have reached the rate limit for proposal_open_contract."}}


def lookups(session):
    """Only the contract lookups (the profit table request is always made once and is not under test here)."""
    return [r for r in session.requests if r.get("contract_id")]


class Session:
    def __init__(self, replies):
        self.replies, self.requests, self._n = list(replies), [], 0

    def next_req_id(self):
        self._n += 1
        return self._n

    async def request(self, req, timeout=10.0):
        self.requests.append(req)
        if req.get("profit_table"):
            return {"profit_table": {"transactions": []}}
        return self.replies.pop(0)


class Slept:
    def __init__(self):
        self.calls = []

    async def __call__(self, s):
        self.calls.append(s)


async def run(session, ids, kv=None, sleep=None):
    sleep = sleep or Slept()
    ex, facts = await load_exchange(session, ids, kv, sleep=sleep, spacing_s=0.0)
    return facts, sleep


async def test_a_normal_answer_gives_prices_and_is_cached_when_final():
    kv = MemoryStore()
    facts, _ = await run(Session([poc()]), [7], kv)
    assert facts[7].entry_spot == D("100.00") and facts[7].exit_spot == D("100.50") and kv.get(FACT_KEY + "7")


async def test_a_second_run_asks_the_exchange_about_nothing_already_known():
    kv = MemoryStore()
    await run(Session([poc(), poc("200", "199", "lost", "PUT")]), [7, 8], kv)
    s2 = Session([poc()])                                              # only the new contract 9 may be requested
    facts, _ = await run(s2, [7, 8, 9], kv)
    assert [r.get("contract_id") for r in s2.requests if r.get("contract_id")] == [9]
    assert facts[8].contract_type == "PUT" and facts[8].exit_spot == D("199")


async def test_a_rate_limit_reply_is_waited_out_and_retried_not_left_as_a_gap():
    s = Session([RATE_LIMIT, RATE_LIMIT, poc()])
    facts, slept = await run(s, [7])
    assert facts[7].entry_spot == D("100.00") and len(lookups(s)) == 3
    assert slept.calls[:2] == [BACKOFF_S, BACKOFF_S * 2]               # it waits longer each time


async def test_a_persistent_rate_limit_ends_as_a_missing_fact_and_is_never_cached():
    kv = MemoryStore()
    s = Session([RATE_LIMIT] * MAX_TRIES)
    facts, _ = await run(s, [7], kv)
    assert 7 not in facts and kv.get(FACT_KEY + "7") is None


async def test_another_error_is_not_retried_and_leaves_the_contract_unverified():
    s = Session([{"error": {"message": "an error without a code"}}])
    facts, _ = await run(s, [7])
    assert 7 not in facts and len(lookups(s)) == 1


async def test_a_contract_that_is_not_final_yet_is_not_cached():
    kv = MemoryStore()
    await run(Session([poc(status="open", exit_=None)]), [7], kv)
    assert kv.get(FACT_KEY + "7") is None


async def test_a_corrupt_cache_entry_is_ignored_and_refetched():
    kv = MemoryStore()
    kv.set(FACT_KEY + "7", "{not json")
    s = Session([poc()])
    facts, _ = await run(s, [7], kv)
    assert facts[7].entry_spot == D("100.00") and len(lookups(s)) == 1


def test_contract_lookups_have_their_own_lower_budget_than_the_published_trading_group():
    assert group_for("proposal_open_contract") == "contract" and group_for("buy") == "trading"
    assert DEFAULT_LIMITS["contract"].per_minute <= 60 < DEFAULT_LIMITS["trading"].per_minute
    asyncio.get_event_loop_policy()                                    # keep the import used under every runner
