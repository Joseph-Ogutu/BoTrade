# Gates

Owner sign-off record (HANDBOOK 13). All times UTC. Values here are cross-checks; the binding definitions are in docs/kill-criterion.md.

## Gate 1: randomness structure (kill-criterion Amendment 1)

| Item | Value |
|---|---|
| Amendment commit | `f0d24e5` |
| Holdout start H0 (Unix seconds) | 1793486149 |
| H0 (UTC) | 2026-10-31T22:35:49Z |
| How H0 was obtained | `python scripts/holdout_start.py` (first commit whose docs/kill-criterion.md contains AMENDMENT-1-MARKER), copied here after the amendment commit |
| Amendment 2 commit | `eeca178`, 2026-10-31T22:45:03Z (epoch 1793486703), before any harness freeze and before any digit-level statistic on real ticks. Does not move H0 |
| Amendment 3 commit | `24af02b`, 2026-10-31T22:52:19Z (epoch 1793487139), before any harness freeze and before any digit-level statistic on real ticks. Fixes the uniformity validity rule and registers the tick validity rule. Does not move H0 |
| Eligible ticks | server epoch strictly greater than H0 and at most 1794700800 (2026-11-15T00:00:00Z) |
| Confirmatory symbols | 1HZ100V, R_100 |
| Exploratory symbols | cryBTCUSD, cryETHUSD, frxEURUSD, frxGBPUSD, frxUSDJPY, frxXAUUSD |
| Tests | 4 per symbol: uniformity_chi2, parity_runs, autocorrelation_portmanteau_10, pair_independence_chi2 |
| Confirmatory family | 8 tests, Holm at 0.05 |
| Exploratory family | 24 tests, Holm at 0.05, never decisive |
| Harness commit (frozen) | NOT YET. Must be recorded here before 2026-11-14T00:00:00Z after the control experiment passes |
| Control experiment | NOT YET RUN |
| Run date | 2026-11-15, run once |
| Result | NOT RUN |
| Owner sign-off | |

Digit-level analysis of any tick with epoch greater than H0 is forbidden until the run (Amendment 1, A1.4). Allowed before then: row counts, minimum and maximum epoch, gap counts, health checks.

Not part of Gate 1 and unaffected by H0: the 628 local ticks collected before the commit and the old bot's 1,160 ticks stay unanalysed and can never confirm a hypothesis.

## Gate 1b: edge test (kill-criterion v1 section 2)

Not started. Evaluated only if Gate 1 returns "Structure found" and only on ticks collected after a further amendment.

## Gate 2 and Gate 3

Not started.
