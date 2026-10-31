"""Run the Gate 1 harness controls on SIMULATED data only and write a stamped evidence file.

    python scripts/run_gate1_control.py

Reads no database and no real tick. The seed is fixed in derivbot.gate1.controls (kill-criterion A1.8).
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from derivbot.common.evidence import stamp_file  # noqa: E402
from derivbot.common.timeutil import utc_now_iso  # noqa: E402
from derivbot.gate1 import controls  # noqa: E402
from derivbot.gate1.registration import load_registration  # noqa: E402


def main() -> int:
    reg = load_registration()
    t0 = time.time()
    rep = controls.run_all(reg, progress=lambda m: print(f"[{time.time() - t0:6.0f}s] {m}", flush=True))
    body = {"seed": rep.seed, "negative": rep.negative, "positive": rep.positive, "validity": rep.validity,
            "all_passed": rep.passed, "seconds": round(time.time() - t0, 1)}
    stamp = utc_now_iso()
    path = ROOT / "docs" / "evidence" / f"gate1_control_{stamp.replace(':', '').replace('-', '')}.json"
    path.write_text(json.dumps(body, indent=2, sort_keys=True, default=float) + "\n")
    stamp_file(path, captured_at_utc=stamp, provenance="recorded by run_gate1_control.py (UTC clock); simulated data only",
               request={"seed": rep.seed, "registration_amendments": reg["amendments"]})

    print("\n== NEGATIVE CONTROL (iid uniform digits)")
    for name, neg in rep.negative.items():
        print(f"  [{name}] reps={neg['reps']} length={neg['length']} band={neg['band']} pass={neg['pass']}")
        for t, v in neg["per_test"].items():
            print(f"     {t:<34} FPR(symbol A)={v['fpr_symbol_a']:.4f} (symbol B {v['fpr_symbol_b_reported_only']:.4f})"
                  f"  KS D={v['ks_d']:.4f} (limit {v['ks_critical_0.01']:.4f})  pass={v['pass']}")
        print(f"     any Holm rejection in the family of 8: {neg['family_any_holm_rejection']:.4f} (limit {neg['family_limit']})")
    print("\n== POSITIVE CONTROL (planted structure, unadjusted alpha 0.05)")
    for kind, v in rep.positive.items():
        print(f"  {kind:<20} detection={ {k: round(x, 3) for k, x in v['detection_rate'].items()} } required>=0.95 pass={v['pass']}")
    print("\n== RUN-VALIDITY MACHINERY (200 copies; serial tests shuffled, uniformity simulated)")
    for name, v in rep.validity.items():
        print(f"  [{name}] band={v['band']} pass={v['pass']} " + " ".join(f"{t}={x['rate']:.4f}" for t, x in v["per_test"].items()))
    print(f"\nALL PASSED: {rep.passed}   evidence: {path.name}   ({body['seconds']} s)")
    return 0 if rep.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
