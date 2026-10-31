import pytest

from derivbot.kv.store import HeartbeatKV, IntentLock, MemoryStore, RateCounter, RedisStore


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _redis_store():
    redis = pytest.importorskip("redis")
    import uuid

    client = redis.Redis(host="127.0.0.1", port=56379, decode_responses=False, socket_connect_timeout=1)
    try:
        client.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"local Redis not reachable ({type(exc).__name__}): run scripts/dev_up.sh")
    return RedisStore(client, prefix=f"test:{uuid.uuid4().hex}:")


@pytest.fixture(params=["memory", "redis"])
def kv(request):
    if request.param == "memory":
        clock = Clock()
        store = MemoryStore(clock)
        store.clock = clock
        return store
    return _redis_store()


def advance(kv, seconds):
    if hasattr(kv, "clock"):
        kv.clock.t += seconds
    else:
        import time

        time.sleep(seconds)


def test_set_get_delete(kv):
    assert kv.get("a") is None
    kv.set("a", "1")
    assert kv.get("a") == "1"
    kv.delete("a")
    assert kv.get("a") is None


def test_ttl_expires(kv):
    kv.set("a", "1", ttl_s=0.3)
    assert kv.get("a") == "1" and 0 < kv.ttl("a") <= 0.3
    advance(kv, 0.4)
    assert kv.get("a") is None


def test_set_if_absent_only_first_wins(kv):
    assert kv.set_if_absent("k", "first") is True
    assert kv.set_if_absent("k", "second") is False
    assert kv.get("k") == "first"


def test_incr_counts_and_keeps_the_first_expiry(kv):
    assert [kv.incr("c", ttl_s=0.5) for _ in range(3)] == [1, 2, 3]
    advance(kv, 0.6)
    assert kv.incr("c", ttl_s=0.5) == 1


def test_intent_lock_blocks_second_holder_and_only_owner_can_release(kv):
    lock = IntentLock(kv, ttl_s=5)
    t1 = lock.acquire("key-1")
    assert t1 is not None and lock.acquire("key-1") is None
    assert lock.release("key-1", "not-the-token") is False
    assert lock.acquire("key-1") is None
    assert lock.release("key-1", t1) is True
    assert lock.acquire("key-1") is not None


def test_intent_lock_expires_so_a_crashed_holder_cannot_block_forever(kv):
    lock = IntentLock(kv, ttl_s=0.3)
    assert lock.acquire("k") is not None
    advance(kv, 0.4)
    assert lock.acquire("k") is not None


def test_rate_counter_window(kv):
    rc = RateCounter(kv, window_s=0.4)
    assert [rc.hit("buy") for _ in range(3)] == [1, 2, 3]
    advance(kv, 0.5)
    assert rc.hit("buy") == 1


def test_heartbeat_age_and_disappearance(kv):
    clock = Clock()
    hb = HeartbeatKV(kv, ttl_s=0.4, clock=clock)
    assert hb.age("executor") is None
    hb.beat("executor")
    clock.t += 5
    assert hb.age("executor") == pytest.approx(5.0)
    advance(kv, 0.5)
    assert hb.age("executor") is None


def test_memory_store_delete_if_equals():
    kv = MemoryStore(Clock())
    kv.set("a", "x")
    assert kv.delete_if_equals("a", "y") is False and kv.get("a") == "x"
    assert kv.delete_if_equals("a", "x") is True and kv.get("a") is None
