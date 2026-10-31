"""Tick collector: public stream -> batched, idempotent writes to a TickStore."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from derivbot.collector.store import SqliteTickStore
from derivbot.deriv.messages import Tick
from derivbot.deriv.public_stream import StreamStats, stream_ticks

log = logging.getLogger(__name__)


@dataclass
class CollectorStats:
    ticks_received: int = 0
    ticks_stored: int = 0
    flushes: int = 0
    stream: StreamStats = field(default_factory=StreamStats)


async def run_collector(
    url: str,
    symbols: list[str],
    store: SqliteTickStore,
    halt: asyncio.Event,
    *,
    max_ticks: int | None = None,
    flush_every: int = 200,
    flush_interval: float = 2.0,
    on_tick: Callable[[Tick], None] | None = None,
    stream_stats: StreamStats | None = None,
    **stream_kwargs,
) -> CollectorStats:
    stats = CollectorStats(stream=stream_stats if stream_stats is not None else StreamStats())
    buffer: list[Tick] = []
    loop = asyncio.get_running_loop()
    last_flush = loop.time()

    async def flush() -> None:
        nonlocal last_flush
        if buffer:
            batch = buffer.copy()
            buffer.clear()
            stats.ticks_stored += await asyncio.to_thread(store.insert_many, batch)
            stats.flushes += 1
        last_flush = loop.time()

    try:
        async for tick in stream_ticks(url, symbols, halt, stats=stats.stream, **stream_kwargs):
            buffer.append(tick)
            stats.ticks_received += 1
            if on_tick:
                on_tick(tick)
            if len(buffer) >= flush_every or loop.time() - last_flush >= flush_interval:
                await flush()
            if max_ticks is not None and stats.ticks_received >= max_ticks:
                halt.set()
    finally:
        await flush()
    return stats
