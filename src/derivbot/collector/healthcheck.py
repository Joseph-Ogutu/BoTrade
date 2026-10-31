"""Docker healthcheck: exit 0 if healthy, 1 otherwise. python -m derivbot.collector.healthcheck [--config PATH]"""

import argparse
import json
import os
import sys
from pathlib import Path

from derivbot.collector.config import CollectorConfig
from derivbot.collector.heartbeat import check_health

DEFAULT_CONFIG = os.environ.get("COLLECTOR_CONFIG", "/app/config/collector.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=DEFAULT_CONFIG)
    args = ap.parse_args()
    try:
        cfg = CollectorConfig.model_validate(json.loads(Path(args.config).read_text()))
    except Exception as exc:  # unreadable config is unhealthy, not a crash
        print(f"UNHEALTHY: config error {type(exc).__name__}")
        return 1
    ok, why = check_health(cfg.heartbeat_path, cfg.required_symbols, cfg.health_max_age_s)
    print(("healthy: " if ok else "UNHEALTHY: ") + why)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
