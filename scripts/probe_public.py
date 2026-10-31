"""Send one request to the PUBLIC Deriv endpoint and save the raw response. No credentials.

Usage: python scripts/probe_public.py '{"active_symbols":"brief"}' docs/evidence/active_symbols_brief.json
Only read-only market-data requests and proposal quotes are meant to be sent. buy/sell are refused.
"""

import asyncio
import json
import sys
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from derivbot.deriv.public_stream import PUBLIC_WS_URL  # noqa: E402


async def main() -> int:
    req = json.loads(sys.argv[1])
    if any(k in req for k in ("buy", "sell", "cancel", "bulk_purchase", "authorize")):
        print("refused: not a read-only request", file=sys.stderr)
        return 2
    req.setdefault("req_id", 1)
    async with websockets.connect(PUBLIC_WS_URL, open_timeout=20) as ws:
        await ws.send(json.dumps(req))
        resp = json.loads(await asyncio.wait_for(ws.recv(), 20))
    text = json.dumps(resp, indent=2, sort_keys=True)
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(text + "\n")
    print(text[:1500])
    return 0


raise SystemExit(asyncio.run(main()))
