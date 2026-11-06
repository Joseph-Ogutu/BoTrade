"""Forward test report (Amendment 5, A5.5). It enforces the rule that nobody sees results early.

* Default run: COUNTS ONLY (completed and discarded contracts per variant), however many there are.
* `--evaluate`: evaluates each non-control variant that has reached N_EVAL completed contracts and has NOT been evaluated before,
  exactly once, using its FIRST N_EVAL contracts by id, and records the evaluation in the audit trail (FORWARD_EVALUATION, ref = variant).
  A variant already evaluated is refused. Variants below N_EVAL are never shown anything but their counts.
* Decision per variant: conditions 1, 2, 3 and 5 of section 2 of kill-criterion v1 (see A5.5), Holm over a family padded to 14.
* Controls (coin flip) are evaluated the same way; if one rejects (p <= 0.05 / 2) the whole evaluation is flagged VOID.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, text

from derivbot.db.audit import append_audit
from derivbot.forward.store import counts
from derivbot.stats.edge import binomial_sf, holm_reject, wilson_lower

ROOT = Path(__file__).resolve().parents[3]
CONTROL_PREFIX = "control_random:"


def stats_for(won: list[bool], ft: dict[str, Any]) -> dict[str, Any]:
    """Everything about one variant's first N_EVAL contracts except the Holm step. Pure."""
    n = len(won)
    x = sum(won)
    r, pbe, k = ft["ratio_r"], ft["p_break_even"], ft["family_size_k"]
    p_hat = x / n
    e = lambda p, rr: p * rr - (1.0 - p)                                          # noqa: E731 - expectancy per unit stake
    half = n // 2
    blocks = [sum(won[:half]) / half, sum(won[half:]) / (n - half)]
    return {
        "n": n, "wins": x, "win_rate": p_hat, "p_value": binomial_sf(x, n, pbe),
        "wilson_lower": wilson_lower(x, n, 1 - ft["alpha_family"] / k, one_sided=True),
        "expectancy": e(p_hat, r), "expectancy_r_minus": e(p_hat, r - ft["sensitivity_r_minus"]),
        "block_expectancy": [e(b, r) for b in blocks],
    }


def decide(s: dict[str, Any], holm_rejected: bool, ft: dict[str, Any]) -> bool:
    return bool(s["n"] >= ft["n_eval"] and s["wilson_lower"] > ft["p_break_even"] and holm_rejected
                and s["expectancy"] > 0 and s["expectancy_r_minus"] > 0 and all(b > 0 for b in s["block_expectancy"]))


def evaluated_before(conn: Connection, variant: str) -> dict[str, Any] | None:
    row = conn.execute(text("SELECT payload FROM audit_events WHERE event_type = 'FORWARD_EVALUATION' AND ref_id = :v ORDER BY id LIMIT 1"),
                       {"v": variant}).first()
    return None if row is None else row[0]


def run_report(conn: Connection, ft: dict[str, Any], evaluate: bool = False) -> dict[str, Any]:
    c = counts(conn)
    n_eval = ft["n_eval"]
    out: dict[str, Any] = {"n_eval": n_eval, "variants": [], "evaluations": []}
    variants = [f"{s}:{sym}" for s in ft["strategies"] for sym in ft["symbols"]] + [f"{CONTROL_PREFIX}{sym}" for sym in ft["symbols"]]
    for v in variants:
        row = c.get(v, {"completed": 0, "discarded": 0})
        out["variants"].append({"variant": v, "completed": row["completed"], "discarded": row["discarded"],
                                "ready": row["completed"] >= n_eval, "evaluated": evaluated_before(conn, v) is not None})
    if not evaluate:
        return out                                                              # COUNTS ONLY, however much data exists

    fresh: dict[str, dict[str, Any]] = {}
    for item in out["variants"]:
        if item["ready"] and not item["evaluated"]:
            rows = conn.execute(text("SELECT won FROM shadow_outcomes WHERE variant = :v ORDER BY id LIMIT :n"),
                                {"v": item["variant"], "n": n_eval}).all()
            fresh[item["variant"]] = stats_for([bool(r[0]) for r in rows], ft)
    registered = [v for v in variants if not v.startswith(CONTROL_PREFIX)]
    for v, s in fresh.items():
        if v.startswith(CONTROL_PREFIX):
            s["control_rejects"] = s["p_value"] <= ft["alpha_family"] / len(ft["symbols"])
            s["passed"] = None
            continue
        padded = [1.0] * len(registered)                                       # not yet evaluated variants enter the family with p = 1
        padded[registered.index(v)] = s["p_value"]
        s["holm_rejected"] = holm_reject(padded, ft["alpha_family"])[registered.index(v)]
        s["passed"] = decide(s, s["holm_rejected"], ft)
    void = any(s.get("control_rejects") for s in fresh.values())
    for v, s in fresh.items():
        s["void"] = void
        append_audit(conn, "forward_report", "FORWARD_EVALUATION", {k: s[k] for k in s}, ref_id=v)
        out["evaluations"].append({"variant": v, **s})
    out["void"] = void
    return out


def render(rep: dict[str, Any]) -> str:
    lines = [f"FORWARD TEST (Amendment 5): evaluation at {rep['n_eval']} completed contracts per variant", ""]
    for v in rep["variants"]:
        mark = "evaluated" if v["evaluated"] else ("READY (run with --evaluate, once)" if v["ready"] else "collecting")
        lines.append(f"  {v['variant']:<42} completed {v['completed']:>6}  discarded {v['discarded']:>5}  {mark}")
    for e in rep.get("evaluations", []):
        detail = (f"wins {e['wins']}/{e['n']} ({e['win_rate']:.4f}), p={e['p_value']:.4g}, wilson lower {e['wilson_lower']:.4f}, "
                  f"E {e['expectancy']:+.4f}, E(r-0.05) {e['expectancy_r_minus']:+.4f}, blocks {[round(x, 4) for x in e['block_expectancy']]}")
        if e["passed"] is None:
            verdict = "CONTROL REJECTS (harness fault)" if e.get("control_rejects") else "CONTROL, no rejection"
        else:
            verdict = "PASS" if e["passed"] else "FAIL"
        lines += ["", f"  EVALUATION {e['variant']}: {detail}  ->  {verdict}"]
    if rep.get("void"):
        lines += ["", "VOID: a control rejected H0, so the harness is considered faulty (A5.5). Do not use these results."]
    return "\n".join(lines)


def main() -> int:  # pragma: no cover - exercised on the server
    from derivbot.config.settings import load_settings, parse_env_file
    from derivbot.db.engine import make_engine
    from derivbot.gate1.registration import load_registration

    ap = argparse.ArgumentParser(description="Forward test report. Counts only unless --evaluate.")
    ap.add_argument("--evaluate", action="store_true", help="evaluate each variant that reached N_EVAL, once, and record it")
    args = ap.parse_args()
    env = dict(os.environ)
    if os.environ.get("ENV_FILE"):
        env = {**parse_env_file(os.environ["ENV_FILE"]), **env}
    engine = make_engine(load_settings(env).database_url.get_secret_value())
    ft = load_registration(ROOT / "docs" / "kill-criterion.md")["forward_test"]
    with engine.begin() as conn:
        print(render(run_report(conn, ft, args.evaluate)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
