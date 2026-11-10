"""Step 1 of docs/real-market-plan.md: which non-synthetic markets are open and what payout do they quote. Read-only, no credentials.

Uses the public endpoint only: active_symbols and one-shot proposal quotes. It never sends buy/sell and computes no strategy statistic.
Usage: python scripts/probe_markets.py
"""
import asyncio
import json
import sys
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from derivbot.deriv.public_stream import PUBLIC_WS_URL  # noqa: E402

DURATIONS = [(5, "t"), (1, "m"), (5, "m"), (10, "m")]


async def ask(ws, req):
    await ws.send(json.dumps(req))
    return json.loads(await asyncio.wait_for(ws.recv(), 20))


async def main() -> int:
    async with websockets.connect(PUBLIC_WS_URL, open_timeout=20) as ws:
        syms = (await ask(ws, {"active_symbols": "brief", "req_id": 1}))["active_symbols"]
        by_market: dict[str, list[dict]] = {}
        for s in syms:
            by_market.setdefault(s["market"], []).append(s)
        for m, lst in sorted(by_market.items()):
            open_n = sum(1 for s in lst if s["exchange_is_open"] and not s["is_trading_suspended"])
            print(f"market {m:22s} symbols {len(lst):3d} open {open_n:3d}")
        picks = []
        for m in ("forex", "commodities", "cryptocurrency", "indices"):
            top = sorted((s for s in by_market.get(m, []) if s["exchange_is_open"] and not s["is_trading_suspended"]),
                         key=lambda s: -s.get("trade_count", 0))[:3]
            picks += top
        print()
        rid = 10
        for s in picks:
            for dur, unit in DURATIONS:
                rid += 1
                r = await ask(ws, {"proposal": 1, "req_id": rid, "amount": 10, "basis": "stake", "contract_type": "CALL", "currency": "USD",
                                   "duration": dur, "duration_unit": unit, "underlying_symbol": s["underlying_symbol"]})
                if "error" in r:
                    print(f"{s['underlying_symbol']:12s} {dur}{unit}: {r['error'].get('code')}")
                    continue
                p = r["proposal"]
                payout = float(p["payout"])
                ratio = payout / 10 - 1
                print(f"{s['underlying_symbol']:12s} {s['market']:14s} {dur:>2}{unit}: payout ratio {ratio:.4f}  break-even {1/(1+ratio):.4f}")
                await asyncio.sleep(0.4)
    return 0


raise SystemExit(asyncio.run(main()))
