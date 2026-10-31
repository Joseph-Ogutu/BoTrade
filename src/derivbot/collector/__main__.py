"""Entry point: python -m derivbot.collector [--config PATH]. No secrets are read or needed."""

import argparse
import asyncio
import json
import logging
import os
import signal
from pathlib import Path

from derivbot.collector.config import CollectorConfig
from derivbot.collector.service import run_service
from derivbot.common.logs import setup_logging

DEFAULT_CONFIG = os.environ.get("COLLECTOR_CONFIG", "/app/config/collector.json")


async def amain(config_path: str) -> int:
    cfg = CollectorConfig.model_validate(json.loads(Path(config_path).read_text()))
    log = logging.getLogger("collector")
    log.info("starting: symbols=%s required=%s db=%s", cfg.symbol_names, cfg.required_symbols, cfg.db_path)
    halt = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, halt.set)
    return await run_service(cfg, halt)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    setup_logging(args.log_level)
    raise SystemExit(asyncio.run(amain(args.config)))


if __name__ == "__main__":
    main()
