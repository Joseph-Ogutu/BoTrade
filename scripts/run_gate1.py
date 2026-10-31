"""THE Gate 1 run (kill-criterion A1.9). Run once, on or after the registered run date, with the frozen harness.

    python scripts/run_gate1.py --execute-gate1 --dbs <stream.sqlite> <backfill.sqlite> [...]

It refuses to start before the cutoff, without a frozen harness commit in docs/gates.md, or if any harness file has
changed since that commit. It must not be run for any other purpose. Controls on simulated data live in
scripts/run_gate1_control.py.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from derivbot.common.evidence import stamp_file  # noqa: E402
from derivbot.common.timeutil import iso_from_epoch, utc_now_iso  # noqa: E402
from derivbot.gate1 import pipeline as pl  # noqa: E402
from derivbot.gate1.controls import SEED  # noqa: E402
from derivbot.gate1.holdout import holdout_start_epoch  # noqa: E402
from derivbot.gate1.loader import GateNotOpen, load_series, permit_real_run  # noqa: E402
from derivbot.gate1.registration import load_registration  # noqa: E402
from derivbot.stats.edge import holm_reject  # noqa: E402


def main(argv: list[str] | None = None, now_epoch: int | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute-gate1", action="store_true", help="required; confirms this is THE run")
    ap.add_argument("--dbs", nargs="+", required=True)
    ap.add_argument("--out-dir", default=str(ROOT / "docs" / "evidence"))
    args = ap.parse_args(argv)
    if not args.execute_gate1:
        print("refusing: pass --execute-gate1 to confirm this is the single registered run")
        return 2
    reg = load_registration()
    try:
        permit = permit_real_run(ROOT, reg, now_epoch=now_epoch)
    except GateNotOpen as exc:
        print(f"GATE CLOSED: {exc}")
        return 3

    h0, cutoff = holdout_start_epoch(ROOT), reg["data_cutoff_epoch"]
    analyses, loads = [], {}
    rng_idx = 0
    for tier, symbols in ((pl.CONFIRMATORY, reg["confirmatory_symbols"]), (pl.EXPLORATORY, reg["exploratory_symbols"])):
        for sym in symbols:
            series, rep = load_series(args.dbs, sym, h0, cutoff, permit)
            loads[sym] = {k: (dict(v) if hasattr(v, "items") else v) for k, v in rep.__dict__.items()}
            rng_idx += 1
            if series.epochs.size == 0:
                continue
            analyses.append(pl.analyze_symbol(series, reg, np.random.default_rng([SEED, 99, rng_idx]), tier))
    conf = [a for a in analyses if a.tier == pl.CONFIRMATORY]
    expl = [a for a in analyses if a.tier == pl.EXPLORATORY]
    decision = pl.decide(conf, reg)

    # EXPLORATORY table: own Holm family of 24, never used in the decision.
    cells, p = pl._family(expl, reg, reg["exploratory_family_size"])
    rej = holm_reject(p, reg["alpha_family"])
    exploratory = [{"symbol": s, "test": t, "p": p[i], "holm_rejected": rej[i], "tier": "EXPLORATORY"}
                   for i, (s, t) in enumerate(cells)]

    def dump(a):
        return {"symbol": a.symbol, "n_ticks": a.n_ticks, "segments": a.n_segments, "valid_segments": a.n_valid_segments,
                "sufficient": a.sufficient, "parity": a.parity, "validity_rates": a.validity_rates,
                "results": {t: r.__dict__ for t, r in a.results.items()},
                "halves": [{t: r.__dict__ for t, r in h.items()} for h in a.halves]}

    body = {"h0_epoch": h0, "h0_utc": iso_from_epoch(h0), "cutoff_utc": reg["data_cutoff_utc"],
            "outcome": decision.outcome, "reason": decision.reason, "replicated": decision.replicated,
            "confirmatory_table": decision.table, "EXPLORATORY_table": exploratory,
            "analyses": [dump(a) for a in analyses], "load_reports": loads, "dbs": args.dbs}
    stamp = utc_now_iso()
    out = Path(args.out_dir) / f"gate1_RUN_{stamp.replace(':', '').replace('-', '')}.json"
    out.write_text(json.dumps(body, indent=2, sort_keys=True, default=str) + "\n")
    stamp_file(out, captured_at_utc=stamp, provenance="THE Gate 1 run, recorded by run_gate1.py (UTC clock)",
               request={"permit": permit.reason})
    print(f"OUTCOME: {decision.outcome}  ({decision.reason})")
    for row in decision.table:
        print(f"  {row['symbol']:<8} {row['test']:<34} p={row['p']:.6g}  holm_rejected={row['holm_rejected']}")
    print(f"evidence: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
