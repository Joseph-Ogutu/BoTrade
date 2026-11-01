---
name: risk-management
description: Use when touching risk rules, stake sizing, the risk config or limits. Covers the handbook section 10 pre-trade checks, the config schema, and the exact steps to add a rule with tests and 100 percent branch coverage.
---

# risk-management

## When to use
Anything in `src/derivbot/risk/`, `src/derivbot/config/risk.py`, `config/risk.json`, or a request for a new limit.

## Design (HANDBOOK section 10)
- Every trade passes `RiskEngine.evaluate` before any buy message. All rules are ALWAYS evaluated and recorded (`risk_decisions.rule_results`), even after the first failure. Any unknown input fails closed.
- Rules (`risk/rules.py`): kill switch clear; mode and account consistent (demo only); connection and reconciliation healthy; market data fresh; open positions; daily loss; consecutive losses; trade rate; stake within bounds; duplicate protection; strategy approved; then after the proposal, price slippage and payout acceptability.
- Stake (`risk/stake.py`): fixed fraction of the current balance, capped by `max_stake_abs`, rounded DOWN to cents, NEVER raised to the minimum. Unknown balance or unknown API minimum means no trade. There is no parameter that scales a stake after a loss or win: no martingale, no anti-martingale. A hypothesis test proves the stake is non-decreasing in balance, so it cannot rise after a loss.
- Config (`config/risk.json`, loaded by `src/derivbot/config/risk.py`): every owner value is `SET_BY_OWNER` until the owner fills it; `load_risk_config` raises `RiskConfigError` listing the unset names and the bot will not start. Do not invent numbers. The config is versioned; `store_config` records the version used by each decision.

## How to add a rule
1. Write failing tests first in `tests/unit/test_risk_rules.py` for every branch (pass, fail, unknown input).
2. Add a pure function to `risk/rules.py` returning `RuleResult(name, passed, observed, limit, detail)`. No I/O, no clock.
3. If it needs config, add the field to `src/derivbot/config/risk.py` and `config/risk.json` as `SET_BY_OWNER` and say so in the owner to-do list in STATE.md.
4. Add it to the list in `risk/engine.py`; add the needed input to the context (`risk/state.py`) and the persistence in `risk/persist.py` if it is stateful.
5. Add an engine test showing the rule appears in `rule_results` and blocks a trade.
6. Gate: `scripts/check_risk_coverage.sh` must report 100 percent line AND branch on `rules.py` and `stake.py`.

## Validation
- `.venv/bin/python -m pytest tests/unit/test_risk_rules.py tests/unit/test_risk_engine.py tests/unit/test_stake.py tests/db/test_risk_state.py tests/db/test_risk_persist.py -o addopts="" -q`
- `make cov`

## Worked example: add a "max losses per symbol" rule
1. Test: with 3 losses on `R_10` and limit 3 the result is not ok; with 2 it is ok; with `None` (unknown) it is not ok.
2. Implement `symbol_losses_within_limit(losses: int | None, max_losses: int) -> RuleResult`.
3. Add `max_losses_per_symbol` as `SET_BY_OWNER`, wire it, and run `make cov`.
