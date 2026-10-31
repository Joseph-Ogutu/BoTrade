"""Run a registered strategy through the contract-level backtester.

    python scripts/run_backtest.py --simulated --strategy ema_trend --ticks 50000 --seed 1 --rho 0.0

Simulated data only unless the owner has approved real-data backtests (docs/approvals.md and a Gate 1 run record);
without approval the real-data flags are refused. Payouts come from the stored proposal quotes.
"""
import argparse
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from derivbot.backtest import metrics as mt  # noqa: E402
from derivbot.backtest.engine import run_backtest  # noqa: E402
from derivbot.backtest.payout import PayoutModel  # noqa: E402
from derivbot.backtest.permit import RealDataNotApproved, load_real_series, require_real_data_approval  # noqa: E402
from derivbot.backtest.series import simulate_random_walk  # noqa: E402
from derivbot.signals.strategies import default_strategies  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True, choices=sorted(default_strategies()))
    ap.add_argument("--simulated", action="store_true")
    ap.add_argument("--ticks", type=int, default=50_000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--rho", type=float, default=0.0, help="planted AR(1) momentum in the simulated walk")
    ap.add_argument("--real-db", help="REFUSED unless the owner approved real-data backtests")
    ap.add_argument("--symbol", default="1HZ100V")
    ap.add_argument("--since", type=int, default=0)
    ap.add_argument("--until", type=int, default=0)
    args = ap.parse_args(argv)
    payout = PayoutModel.from_evidence_batch(ROOT / "docs/evidence/public_probe_batch.json")
    if args.real_db:
        try:
            approval = require_real_data_approval(ROOT)
        except RealDataNotApproved as exc:
            print(f"REFUSED: real-data backtests are not approved ({exc})")
            return 3
        series = load_real_series(args.real_db, args.symbol, args.since, args.until, approval)
        label = f"REAL {args.symbol} (approval {approval.date})"
    elif args.simulated:
        series = simulate_random_walk(args.ticks, args.seed, rho=args.rho)
        label = f"SIMULATED random walk, rho={args.rho}, seed={args.seed}"
    else:
        print("choose --simulated, or --real-db (which needs the owner's approval)")
        return 2
    strat = default_strategies()[args.strategy]
    res = run_backtest(series, lambda s: strat, payout)
    m = mt.compute(res.trades)
    print(f"{label}: strategy {strat.id}@{strat.version} on {len(series)} ticks")
    print(f"trades={m.n} win_rate={m.win_rate:.4f} breakeven={m.breakeven_p:.4f} wilson95_lower={m.wilson95_lower_one_sided:.4f}")
    print(f"expectancy/unit={m.expectancy_per_unit:+.4f} at r={m.mean_r:.4f}; at r-0.05: "
          f"{mt.expectancy_at_shifted_r(res.trades, 0.05):+.4f}; max_drawdown={m.max_drawdown:.2f}; "
          f"longest_loss_streak={m.longest_loss_streak}; trades/day={m.trades_per_day:.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
