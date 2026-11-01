"""Batch of read-only probes on the PUBLIC endpoint. No credentials. Saves docs/evidence/public_probe_batch.json."""
import asyncio
import json
import sys
from pathlib import Path

import websockets

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from derivbot.common.evidence import stamp_file  # noqa: E402
from derivbot.common.timeutil import iso_from_epoch, utc_now_iso  # noqa: E402
from derivbot.deriv.public_stream import PUBLIC_WS_URL  # noqa: E402


def prop(symbol, ctype, dur=5, unit="t", barrier=None, amount=1, **extra):
    r = {"proposal": 1, "amount": amount, "basis": "stake", "contract_type": ctype, "currency": "USD",
         "duration": dur, "duration_unit": unit, "underlying_symbol": symbol, **extra}
    if barrier is not None:
        r["barrier"] = barrier
    return r

CASES = {}
for sym in ("1HZ100V", "R_100"):
    for ct, b in (("DIGITEVEN", None), ("DIGITODD", None), ("DIGITOVER", "5"), ("DIGITUNDER", "5"),
                  ("DIGITMATCH", "5"), ("DIGITDIFF", "5"), ("CALL", None), ("PUT", None)):
        CASES[f"{sym}:{ct}:{b}"] = prop(sym, ct, barrier=b)
CASES["1HZ100V:DIGITEVEN:stake0.35"] = prop("1HZ100V", "DIGITEVEN", amount=0.35)
CASES["1HZ100V:DIGITEVEN:stake0.10"] = prop("1HZ100V", "DIGITEVEN", amount=0.10)
CASES["1HZ100V:DIGITEVEN:stake2000"] = prop("1HZ100V", "DIGITEVEN", amount=2000)
CASES["frxEURUSD:CALL:15m (market closed?)"] = prop("frxEURUSD", "CALL", 15, "m", barrier="+0.001")
CASES["cryBTCUSD:MULTUP"] = {"proposal": 1, "amount": 10, "basis": "stake", "contract_type": "MULTUP",
                              "currency": "USD", "multiplier": 10, "underlying_symbol": "cryBTCUSD"}
CASES["ERR bad symbol"] = prop("NOPE", "DIGITEVEN")
CASES["ERR bad contract type"] = prop("1HZ100V", "NOTATYPE")
CASES["ERR missing required"] = {"proposal": 1, "underlying_symbol": "1HZ100V"}
CASES["ERR unknown field"] = {"ping": 1, "bogus_field": 1}
CASES["ERR balance on public (needs auth)"] = {"balance": 1}
CASES["ERR portfolio on public (needs auth)"] = {"portfolio": 1}
CASES["ticks_history count=5000"] = {"ticks_history": "1HZ100V", "count": 5000, "end": "latest", "style": "ticks"}
CASES["ticks_history count=100000"] = {"ticks_history": "1HZ100V", "count": 100000, "end": "latest", "style": "ticks"}
CASES["time"] = {"time": 1}
CASES["ping"] = {"ping": 1}

async def main():
    out, lat = {}, {}
    loop = asyncio.get_running_loop()
    futs = {}
    async with websockets.connect(PUBLIC_WS_URL, open_timeout=20) as ws:
        async def reader():
            async for raw in ws:
                msg = json.loads(raw)
                f = futs.get(msg.get("req_id"))
                if f and not f.done():
                    f.set_result((msg, loop.time()))

        rt = asyncio.create_task(reader())
        for i, (name, req) in enumerate(CASES.items(), start=1):
            futs[i] = loop.create_future()
            t0 = loop.time()
            await ws.send(json.dumps({**req, "req_id": i}))
            try:
                msg, t1 = await asyncio.wait_for(futs[i], 45)
                out[name], lat[name] = msg, round(t1 - t0, 2)
            except TimeoutError:
                out[name], lat[name] = {"TIMEOUT": True}, None
            await asyncio.sleep(0.15)
        rt.cancel()
    path = Path("docs/evidence/public_probe_batch.json")
    path.write_text(json.dumps({"responses": out, "latency_s": lat}, indent=1, sort_keys=True) + "\n")
    srv = out.get("time", {}).get("time")
    stamp_file(path, captured_at_utc=utc_now_iso(), provenance="recorded by probe_public_batch.py at end of batch (UTC clock)",
               server_time_utc=iso_from_epoch(srv) if srv else None, note="Individual replies were received over the preceding minutes.")

asyncio.run(main())
