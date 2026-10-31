"""Per-day uptime and gap report plus total contiguous coverage. Reads EPOCHS ONLY, never quotes or digits.

    python scripts/coverage_report.py data/holdout_backfill.sqlite data/ticks.sqlite             # eligible ticks (> H0)
    python scripts/coverage_report.py data/ticks.sqlite --from-first-tick --label local-collector # whole file
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from derivbot.collector.report import coverage_summary, day_rows  # noqa: E402
from derivbot.common.evidence import stamp_file  # noqa: E402
from derivbot.common.timeutil import iso_from_epoch, utc_now_iso  # noqa: E402
from derivbot.gate1.holdout import holdout_start_epoch  # noqa: E402
from derivbot.gate1.registration import load_registration  # noqa: E402


def read_epochs(path: str, symbol: str) -> list[int]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:  # the SELECT names the epoch column only
        return [r[0] for r in conn.execute("SELECT epoch FROM ticks WHERE symbol = ?", (symbol,))]
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dbs", nargs="+")
    ap.add_argument("--from-first-tick", action="store_true", help="report from the first tick instead of from H0")
    ap.add_argument("--label", default="eligible-after-H0")
    ap.add_argument("--report-dir", default=str(ROOT / "docs" / "evidence"))
    args = ap.parse_args()

    reg = load_registration()
    h0 = holdout_start_epoch(ROOT)
    out: dict = {"label": args.label, "dbs": args.dbs, "h0_epoch": h0, "h0_utc": iso_from_epoch(h0), "symbols": {}}
    for sym in reg["confirmatory_symbols"]:
        interval = reg["nominal_interval_s"][sym]
        epochs = sorted({e for p in args.dbs for e in read_epochs(p, sym)})
        if not args.from_first_tick:
            epochs = [e for e in epochs if e > h0]
        if not epochs:
            out["symbols"][sym] = {"ticks": 0}
            print(f"{sym}: no ticks in scope")
            continue
        since = (epochs[0] - 1) if args.from_first_tick else h0
        until = epochs[-1]
        days = day_rows(epochs, interval, since, until)
        summ = coverage_summary(epochs, interval, reg["min_segment_len"])
        out["symbols"][sym] = {
            "interval_s": interval, "first_utc": iso_from_epoch(epochs[0]), "last_utc": iso_from_epoch(until),
            "days": [d.__dict__ for d in days], "summary": summ.__dict__,
            "needed_for_gate1": reg["min_ticks_confirmatory"],
        }
        print(f"\n{sym}  interval {interval}s  {iso_from_epoch(epochs[0])} .. {iso_from_epoch(until)}")
        print(f"  {'day (UTC)':<11}{'ticks':>9}{'expected':>10}{'uptime':>9}{'gaps':>6}{'missing':>9}{'longest gap s':>15}")
        for d in days:
            print(f"  {d.day:<11}{d.ticks:>9}{d.expected:>10}{d.uptime:>9.4f}{d.gap_count:>6}{d.missing:>9}{d.longest_gap_s:>15}")
        print(f"  total ticks {summ.ticks}; segments {summ.segments} (>= {reg['min_segment_len']} ticks: {summ.valid_segments});"
              f" contiguous coverage {summ.ticks_in_valid_segments} ticks = {summ.contiguous_fraction:.4f} of all;"
              f" longest segment {summ.longest_segment}; Gate 1 needs {reg['min_ticks_confirmatory']} eligible ticks")
    stamp = utc_now_iso()
    path = Path(args.report_dir) / f"coverage_report_{args.label}_{stamp.replace(':', '').replace('-', '')}.json"
    path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    stamp_file(path, captured_at_utc=stamp, provenance="recorded by coverage_report.py (UTC clock); epochs only",
               request={"dbs": args.dbs, "from_first_tick": args.from_first_tick})
    print(f"\nreport: {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
