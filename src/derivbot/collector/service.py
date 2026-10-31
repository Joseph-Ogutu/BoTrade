"""Long-running collector service: stream -> store, with heartbeat file, heartbeat log line and a silence watchdog.

Exit codes: 0 on a requested stop, 1 if required symbols were silent for max_silence_exit_s
(so the container restart policy gets a clean process restart instead of a zombie).
"""

from __future__ import annotations

import asyncio
import logging
import time

from derivbot.collector.collector import run_collector
from derivbot.collector.config import CollectorConfig
from derivbot.collector.heartbeat import Heartbeat
from derivbot.collector.store import SqliteTickStore
from derivbot.deriv.public_stream import StreamStats
from derivbot.deriv.ratelimit import DEFAULT_LIMITS, RateLimiter

log = logging.getLogger(__name__)


async def run_service(cfg: CollectorConfig, halt: asyncio.Event) -> int:
    store = SqliteTickStore(cfg.db_path)
    hb = Heartbeat(cfg.symbol_names, cfg.heartbeat_path)
    stream_stats = StreamStats()
    required = cfg.required_symbols
    exit_code = 0

    async def beat() -> None:
        nonlocal exit_code
        started = time.time()
        while not halt.is_set():
            hb.write()
            log.info(hb.log_line(stream_stats.connects, stream_stats.reconnects, stream_stats.errors))
            if required:
                newest = max([started] + [t for t in (hb.last_tick(s) for s in required) if t is not None])
                if time.time() - newest > cfg.max_silence_exit_s:
                    log.error("no ticks on required symbols for %.0fs, exiting for restart", cfg.max_silence_exit_s)
                    exit_code = 1
                    halt.set()
                    return
            try:
                await asyncio.wait_for(halt.wait(), timeout=cfg.heartbeat_interval_s)
            except asyncio.TimeoutError:
                pass

    beat_task = asyncio.create_task(beat())
    try:
        stats = await run_collector(
            cfg.url, cfg.symbol_names, store, halt,
            flush_every=cfg.flush_every, flush_interval=cfg.flush_interval_s,
            on_tick=lambda t: hb.record(t.symbol),
            stream_stats=stream_stats,
            rate_limiter=RateLimiter(DEFAULT_LIMITS),
            stale_after=cfg.stale_after_s, backoff_base=cfg.backoff_base_s, backoff_cap=cfg.backoff_cap_s,
        )
        log.info("collector stopped: received=%d stored=%d", stats.ticks_received, stats.ticks_stored)
    finally:
        halt.set()
        await beat_task
        hb.write()
        store.close()
    return exit_code
