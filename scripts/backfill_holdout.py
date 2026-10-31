"""Backfill holdout ticks (epoch > H0) for the confirmatory symbols from the PUBLIC endpoint via ticks_history.

No credentials. Reads quotes and epochs only; computes no digit statistic (kill-criterion Amendment 1, A1.4).
Rows are stored with source='backfill' in a separate SQLite file. Writes a stamped report to docs/evidence/.

    python scripts/backfill_holdout.py --db data/holdout_backfill.sqlite
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from derivbot.collector.backfill import backfill_symbol, coverage, decimals_from_pip, quote_from_price  # noqa: E402
from derivbot.collector.store import SqliteTickStore  # noqa: E402
from derivbot.common.evidence import stamp_file  # noqa: E402
from derivbot.common.logs import setup_logging  # noqa: E402
from derivbot.common.timeutil import iso_from_epoch, utc_now_iso  # noqa: E402
from derivbot.deriv.public_stream import PUBLIC_WS_URL  # noqa: E402
from derivbot.deriv.ratelimit import DEFAULT_LIMITS, RateLimiter  # noqa: E402
from derivbot.gate1.holdout import holdout_start_epoch  # noqa: E402
from derivbot.gate1.registration import load_registration  # noqa: E402

log = logging.getLogger("backfill")


async def crosscheck(ws, symbol: str, seconds: float, rid: int) -> dict:
    """Compare quotes seen on the live stream with the same epochs from ticks_history. Equality only."""
    await ws.send(json.dumps({"ticks": symbol, "subscribe": 1, "req_id": rid}))
    seen: dict[int, str] = {}
    sub_id = None
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    while loop.time() - t0 < seconds:
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), 2))
        except asyncio.TimeoutError:
            continue
        if m.get("msg_type") == "tick" and m["tick"]["symbol"] == symbol:
            t = m["tick"]
            seen[int(t["epoch"])] = str(quote_from_price(float(t["quote"]), decimals_from_pip(float(t["pip_size"]))))
            sub_id = (m.get("subscription") or {}).get("id", sub_id)
    if sub_id:
        await ws.send(json.dumps({"forget": sub_id, "req_id": rid + 1}))
    await ws.send(json.dumps({"ticks_history": symbol, "count": 200, "end": "latest", "style": "ticks",
                              "req_id": rid + 2}))
    while True:
        m = json.loads(await ws.recv())
        if m.get("req_id") == rid + 2:
            break
    dec = decimals_from_pip(float(m["pip_size"]))
    hist = {int(t): str(quote_from_price(p, dec)) for t, p in zip(m["history"]["times"], m["history"]["prices"])}
    overlap = sorted(set(seen) & set(hist))
    mismatches = [e for e in overlap if seen[e] != hist[e]]
    return {"stream_ticks": len(seen), "overlap": len(overlap), "mismatches": len(mismatches)}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/holdout_backfill.sqlite")
    ap.add_argument("--h0", type=int, default=None, help="override; default is computed from git")
    ap.add_argument("--crosscheck-seconds", type=float, default=12.0)
    ap.add_argument("--report-dir", default=str(ROOT / "docs" / "evidence"))
    args = ap.parse_args()
    setup_logging("INFO")

    reg = load_registration()
    h0 = holdout_start_epoch(ROOT)
    if args.h0 is not None and args.h0 != h0:
        log.error("--h0 %d disagrees with the holdout start computed from git (%d); refusing", args.h0, h0)
        return 2
    symbols = reg["confirmatory_symbols"]
    interval = reg["nominal_interval_s"]
    store = SqliteTickStore(args.db)
    limiter = RateLimiter(DEFAULT_LIMITS)
    report = {"h0_epoch": h0, "h0_utc": iso_from_epoch(h0), "symbols": {}}
    async with websockets.connect(PUBLIC_WS_URL, open_timeout=30) as ws:
        rid = 5000
        for sym in symbols:
            res = await backfill_symbol(ws, sym, h0, store, rate_limiter=limiter, req_id_start=rid)
            rid += 1000
            epochs = store.epochs(sym)
            cov = coverage(epochs, h0, res.max_epoch, interval[sym])
            by_source = store.count_by_source(sym)
            checks = {
                "all_stored_epochs_after_h0": bool(epochs) and min(epochs) > h0,
                "no_page_rejected": not any(not p["accepted"] for p in res.page_log),
                "reached_h0": res.reached_h0,
                "no_problems": not res.problems,
            }
            xc = await crosscheck(ws, sym, args.crosscheck_seconds, rid)
            rid += 10
            report["symbols"][sym] = {
                "pages": res.pages, "fetched": res.fetched, "stored_new": res.stored,
                "rows_in_db": len(epochs), "rows_by_source": by_source,
                "dropped_not_after_h0": res.dropped_not_after_h0,
                "first_epoch_utc": iso_from_epoch(min(epochs)) if epochs else None,
                "last_epoch_utc": iso_from_epoch(max(epochs)) if epochs else None,
                "coverage": {"expected": cov.expected, "rows": cov.rows, "ratio": round(cov.ratio, 6),
                             "gap_count": cov.gap_count, "missing": cov.missing, "longest_gap_s": cov.longest_gap_s},
                "checks": checks, "problems": res.problems, "page_log": res.page_log,
                "stream_vs_history": xc,
            }
    store.close()
    stamp = utc_now_iso()
    out = Path(args.report_dir) / f"backfill_report_{stamp.replace(':', '').replace('-', '')}.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    stamp_file(out, captured_at_utc=stamp, provenance="recorded by backfill_holdout.py (UTC clock)",
               request={"endpoint": PUBLIC_WS_URL, "method": "ticks_history", "h0": h0})
    print(f"report: {out}")
    for sym, r in report["symbols"].items():
        c = r["coverage"]
        print(f"{sym}: stored_new={r['stored_new']} rows={r['rows_in_db']} {r['first_epoch_utc']}..{r['last_epoch_utc']} "
              f"coverage={c['rows']}/{c['expected']} ({c['ratio']:.4f}) gaps={c['gap_count']} missing={c['missing']} "
              f"checks={r['checks']} stream_vs_history={r['stream_vs_history']} problems={r['problems']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
