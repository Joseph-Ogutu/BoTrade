"""Container healthcheck for the trading service: the loopback API answers AND the Redis heartbeat is fresh."""

from __future__ import annotations

import os
import sys
import urllib.request

from derivbot.config.settings import resolve_file_secrets

MAX_AGE_S = 60.0


def evaluate(api_ok: bool, heartbeat_age_s: float | None) -> tuple[bool, str]:
    if not api_ok:
        return False, "admin API does not answer"
    if heartbeat_age_s is None:
        return False, "no heartbeat"
    if heartbeat_age_s > MAX_AGE_S:
        return False, f"heartbeat {heartbeat_age_s:.0f}s old"
    return True, "ok"


def main() -> int:  # pragma: no cover - exercised in the container
    env = resolve_file_secrets(dict(os.environ))
    try:
        with urllib.request.urlopen("http://127.0.0.1:8787/health", timeout=3) as r:
            api_ok = r.status == 200
    except OSError:
        api_ok = False
    age = None
    try:

        import redis

        from derivbot.config.settings import assemble_urls
        from derivbot.kv.store import HeartbeatKV, RedisStore

        url = assemble_urls(env).get("REDIS_URL", "")
        age = HeartbeatKV(RedisStore(redis.Redis.from_url(url, socket_connect_timeout=2))).age(os.environ.get("HEARTBEAT_NAME", "bot"))
    except Exception:  # noqa: BLE001
        age = None
    ok, why = evaluate(api_ok, age)
    print(("healthy: " if ok else "UNHEALTHY: ") + why)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
