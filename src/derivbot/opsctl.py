"""Operator CLI for the loopback-only admin API. Run INSIDE the container (the API is not published to the host):

    docker compose exec bot python -m derivbot.opsctl status
    docker compose exec bot python -m derivbot.opsctl kill --reason "why"
    docker compose exec bot python -m derivbot.opsctl release --reason "what was checked"

The token comes from ADMIN_API_TOKEN or ADMIN_API_TOKEN_FILE and is never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from derivbot.config.settings import SettingsError, resolve_file_secrets

BASE = "http://127.0.0.1:8787"


def call(method: str, path: str, token: str, body: dict[str, Any] | None = None,
         opener: Callable[..., Any] = urllib.request.urlopen, base: str = BASE) -> tuple[int, str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with opener(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()
    except (urllib.error.URLError, OSError) as exc:
        return 0, f"cannot reach the admin API: {type(exc).__name__}"


def main(argv: list[str] | None = None, env: dict[str, str] | None = None, opener: Callable[..., Any] = urllib.request.urlopen) -> int:
    ap = argparse.ArgumentParser(prog="opsctl")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    for name in ("kill", "release"):
        p = sub.add_parser(name)
        p.add_argument("--reason", required=True)
    args = ap.parse_args(argv)
    try:
        token = resolve_file_secrets(env if env is not None else dict(os.environ)).get("ADMIN_API_TOKEN", "")
    except SettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not token:
        print("error: ADMIN_API_TOKEN is not set", file=sys.stderr)
        return 2
    if args.cmd == "status":
        code, body = call("GET", "/status", token, opener=opener)
    else:
        code, body = call("POST", f"/{args.cmd}", token, {"reason": args.reason}, opener=opener)
    print(body)
    return 0 if code == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
