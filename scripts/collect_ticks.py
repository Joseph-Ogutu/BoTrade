"""Collect ticks from the PUBLIC Deriv endpoint into a local SQLite file.

No token, OTP or App ID is used or read. Example:
    python scripts/collect_ticks.py --symbols 1HZ100V --db data/ticks.sqlite --seconds 60
Stop with Ctrl-C or SIGTERM; buffered ticks are flushed on exit.
"""

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from derivbot.collector.collector import run_collector  # noqa: E402
from derivbot.collector.gaps import find_gaps  # noqa: E402
from derivbot.collector.store import SqliteTickStore  # noqa: E402
from derivbot.common.logs import setup_logging  # noqa: E402
from derivbot.deriv.public_stream import PUBLIC_WS_URL  # noqa: E402
from derivbot.deriv.ratelimit import DEFAULT_LIMITS, RateLimiter  # noqa: E402


def expected_interval(symbol: str) -> int:
    return 1 if symbol.startswith("1HZ") else 2


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="1HZ100V", help="comma separated")
    ap.add_argument("--db", default="data/ticks.sqlite")
    ap.add_argument("--url", default=PUBLIC_WS_URL)
    ap.add_argument("--seconds", type=float, default=None, help="stop after this many seconds")
    ap.add_argument("--max-ticks", type=int, default=None)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()

    setup_logging(args.log_level)
    log = logging.getLogger("collect")
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    store = SqliteTickStore(args.db)
    halt = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, halt.set)
    if args.seconds:
        loop.call_later(args.seconds, halt.set)

    stats = await run_collector(
        args.url, symbols, store, halt, max_ticks=args.max_ticks,
        rate_limiter=RateLimiter(DEFAULT_LIMITS),
    )
    log.info("done: received=%d stored=%d flushes=%d connects=%d reconnects=%d errors=%d stalls=%d",
             stats.ticks_received, stats.ticks_stored, stats.flushes, stats.stream.connects,
             stats.stream.reconnects, stats.stream.errors, stats.stream.stalls)
    for sym in symbols:
        rng = store.epoch_range(sym)
        gaps = find_gaps(store.epochs(sym), expected_interval(sym))
        log.info("%s: rows=%d range=%s gaps=%d missing_est=%d", sym, store.count(sym), rng,
                 len(gaps), sum(g.missing for g in gaps))
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
