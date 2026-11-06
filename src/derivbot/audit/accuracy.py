"""Accuracy audit: does what the bot RECORDED equal what the exchange RECORDED, and does the arithmetic hold?

Read only. Every check is a pure function over plain data, so each one is tested by injecting the exact fault it must catch.
A check that cannot fail proves nothing; tests/unit/test_accuracy_audit.py breaks the data one way per check.

Checks (all exact Decimal comparisons, no tolerance on money):
  exchange_has_every_settled_trade   each settled local order exists in the exchange's profit table
  no_unknown_trades_on_the_account   no exchange trade in the audit window that the bot has no order for
  money_fields_match_exactly         buy price, payout and profit equal the exchange's (profit = sell price - buy price)
  profit_formula_holds               a win pays payout - buy price, a loss loses the buy price, nothing else
  outcome_matches_contract_rule      CALL wins only if exit spot > entry spot, PUT only if exit spot < entry spot (tie loses)
  balance_chain_is_consistent        balance after buy n = balance after buy n-1 + sell price n-1 - buy price n
  stake_matches_sizing_rule          stake = min(max stake, balance * risk fraction), rounded down to cents
  state_transitions_are_legal        every recorded transition is in the order state machine's table
  exposed_intents_were_risk_approved every intent that reached a buy has an approved risk decision
  no_duplicate_contracts             one contract id belongs to one order
  outcome_coverage_complete          every settled trade could be checked against the exchange's entry and exit prices
  clock_skew_within_tolerance        our clock vs the exchange's purchase time stays inside the executor's tolerance
  nothing_stuck                      no intent left in BUY_SENT, BUY_UNKNOWN or MANUAL_REVIEW
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal

from derivbot.execution.state_machine import TRANSITIONS, Event, State

CENT = Decimal("0.01")
STUCK = {"BUY_SENT", "BUY_UNKNOWN", "MANUAL_REVIEW"}


@dataclass(frozen=True)
class LocalTrade:
    intent_id: str
    contract_type: str
    stake: Decimal
    state: str
    contract_id: int | None = None
    buy_price: Decimal | None = None
    payout: Decimal | None = None
    profit: Decimal | None = None
    opened_at: dt.datetime | None = None
    balance_after_buy: Decimal | None = None
    balance_before_decision: Decimal | None = None
    risk_approved: bool | None = None
    created_at: dt.datetime | None = None
    max_stake: Decimal | None = None          # the stake cap and risk fraction IN FORCE when this trade was decided
    risk_fraction: Decimal | None = None      # (from the config version stored with its risk decision)


@dataclass(frozen=True)
class ExchangeTrade:
    contract_id: int
    buy_price: Decimal
    payout: Decimal
    sell_price: Decimal
    purchase_time: int


@dataclass(frozen=True)
class ContractFacts:
    contract_id: int
    contract_type: str
    entry_spot: Decimal | None
    exit_spot: Decimal | None
    status: str | None


@dataclass
class AuditData:
    local: list[LocalTrade]
    exchange: list[ExchangeTrade]
    facts: dict[int, ContractFacts]
    events: dict[str, list[tuple[str | None, str, str]]]            # intent id -> [(from_state, event, to_state)] in order
    risk_fraction: Decimal
    max_stake: Decimal
    window_start_epoch: int
    skew_tolerance_s: int = 10
    now: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    checked: int
    detail: str = ""


def _ids(items: Iterable[object], limit: int = 3) -> str:
    xs = [str(i) for i in items]
    return ", ".join(xs[:limit]) + (f" (+{len(xs) - limit} more)" if len(xs) > limit else "")


def _settled(d: AuditData) -> list[LocalTrade]:
    return [t for t in d.local if t.state == "SETTLED" and t.contract_id is not None and t.profit is not None]


def check_exchange_has_every_settled_trade(d: AuditData) -> Check:
    have = {e.contract_id for e in d.exchange}
    s = _settled(d)
    missing = [t.contract_id for t in s if t.contract_id not in have]
    return Check("exchange_has_every_settled_trade", not missing, len(s), "" if not missing else f"missing at exchange: {_ids(missing)}")


def check_no_unknown_trades(d: AuditData) -> Check:
    local_ids = {t.contract_id for t in d.local if t.contract_id is not None}
    window = [e for e in d.exchange if e.purchase_time >= d.window_start_epoch]
    foreign = [e.contract_id for e in window if e.contract_id not in local_ids]
    return Check("no_unknown_trades_on_the_account", not foreign, len(window),
                 "" if not foreign else f"exchange trades with no local order: {_ids(foreign)}")


def check_money_fields_match(d: AuditData) -> Check:
    ex = {e.contract_id: e for e in d.exchange}
    bad: list[str] = []
    n = 0
    for t in _settled(d):
        e = ex.get(t.contract_id)               # type: ignore[arg-type]
        if e is None:
            continue
        n += 1
        if t.buy_price != e.buy_price or t.payout != e.payout or t.profit != e.sell_price - e.buy_price:
            bad.append(f"{t.contract_id}: local buy/payout/profit {t.buy_price}/{t.payout}/{t.profit} vs exchange "
                       f"{e.buy_price}/{e.payout}/{e.sell_price - e.buy_price}")
    return Check("money_fields_match_exactly", not bad, n, _ids(bad, 2))


def check_profit_formula(d: AuditData) -> Check:
    bad = []
    s = [t for t in _settled(d) if t.buy_price is not None and t.payout is not None]
    for t in s:
        assert t.profit is not None and t.buy_price is not None and t.payout is not None
        if t.profit not in (t.payout - t.buy_price, -t.buy_price):
            bad.append(f"{t.contract_id}: profit {t.profit} is neither a win ({t.payout - t.buy_price}) nor a loss ({-t.buy_price})")
    return Check("profit_formula_holds", not bad, len(s), _ids(bad, 2))


def check_outcome_coverage(d: AuditData) -> Check:
    """A check that silently examines fewer trades than exist proves less than it says. Found on the live account: a rate limit
    left 24 of 114 trades without prices and the outcome check still passed. Missing coverage is now its own visible failure."""
    s = _settled(d)
    missing = [t.contract_id for t in s if (f := d.facts.get(t.contract_id)) is None or f.entry_spot is None or f.exit_spot is None]  # type: ignore[arg-type]
    return Check("outcome_coverage_complete", not missing, len(s),
                 "" if not missing else f"{len(missing)} of {len(s)} trades could not be verified (no entry or exit price from the exchange; "
                                        f"retry later), e.g. {_ids(missing)}")


def check_outcome_matches_rule(d: AuditData) -> Check:
    bad, n = [], 0
    for t in _settled(d):
        f = d.facts.get(t.contract_id)          # type: ignore[arg-type]
        if f is None or f.entry_spot is None or f.exit_spot is None or t.profit is None:
            continue
        n += 1
        if f.contract_type == "CALL":
            should_win = f.exit_spot > f.entry_spot
        elif f.contract_type == "PUT":
            should_win = f.exit_spot < f.entry_spot
        else:
            continue
        if should_win != (t.profit > 0):
            bad.append(f"{t.contract_id}: {f.contract_type} entry {f.entry_spot} exit {f.exit_spot} but recorded profit {t.profit}")
    return Check("outcome_matches_contract_rule", not bad, n, _ids(bad, 2))


def check_balance_chain(d: AuditData) -> Check:
    rows = sorted((t for t in d.local if t.opened_at is not None and t.balance_after_buy is not None and t.buy_price is not None),
                  key=lambda t: t.opened_at)                                     # type: ignore[arg-type,return-value]
    bad, n = [], 0
    for prev, cur in zip(rows, rows[1:], strict=False):
        if prev.profit is None or prev.buy_price is None or cur.buy_price is None or cur.balance_after_buy is None:
            continue                                                         # the previous contract had not settled: no claim
        n += 1
        want = prev.balance_after_buy + (prev.buy_price + prev.profit) - cur.buy_price    # type: ignore[operator]
        if want != cur.balance_after_buy:
            bad.append(f"{cur.contract_id}: balance after buy {cur.balance_after_buy}, expected {want} "
                       f"(any other activity on the account breaks this chain)")
    return Check("balance_chain_is_consistent", not bad, n, _ids(bad, 2))


def expected_stake(balance: Decimal, fraction: Decimal, cap: Decimal) -> Decimal:
    return min(cap, balance * fraction).quantize(CENT, rounding=ROUND_DOWN)


def check_stake_sizing(d: AuditData) -> Check:
    bad, n = [], 0
    for t in d.local:
        if t.balance_before_decision is None or t.risk_approved is not True:
            continue
        n += 1
        # Judge each trade by the config that was in force when it was decided, not today's: limits change over time.
        want = expected_stake(t.balance_before_decision, t.risk_fraction or d.risk_fraction, t.max_stake or d.max_stake)
        if t.stake != want:
            bad.append(f"{t.intent_id[:8]}: stake {t.stake}, expected {want} from balance {t.balance_before_decision}")
    return Check("stake_matches_sizing_rule", not bad, n, _ids(bad, 2))


def check_transitions_legal(d: AuditData) -> Check:
    bad, n = [], 0
    for iid, evs in d.events.items():
        prev: str | None = None
        for i, (frm, ev, to) in enumerate(evs):
            n += 1
            if i == 0:
                ok = frm is None and to == State.INTENT_CREATED.value
            else:
                try:
                    ok = frm == prev and TRANSITIONS.get((State(frm), Event(ev))) == State(to)
                except ValueError:
                    ok = False
            if not ok:
                bad.append(f"{iid[:8]}: {frm} -[{ev}]-> {to}")
            prev = to
    return Check("state_transitions_are_legal", not bad, n, _ids(bad, 2))


def check_exposed_were_approved(d: AuditData) -> Check:
    exposed = [t for t in d.local if t.state in ("BUY_SENT", "OPEN", "SETTLED", "SOLD", "BUY_UNKNOWN", "MANUAL_REVIEW") or t.contract_id]
    bad = [t.intent_id[:8] for t in exposed if t.risk_approved is not True]
    return Check("exposed_intents_were_risk_approved", not bad, len(exposed), "" if not bad else f"no approved decision: {_ids(bad)}")


def check_no_duplicate_contracts(d: AuditData) -> Check:
    seen: dict[int, int] = {}
    for t in d.local:
        if t.contract_id is not None:
            seen[t.contract_id] = seen.get(t.contract_id, 0) + 1
    dup = [c for c, k in seen.items() if k > 1]
    return Check("no_duplicate_contracts", not dup, len(seen), "" if not dup else f"used twice: {_ids(dup)}")


def check_clock_skew(d: AuditData) -> Check:
    ex = {e.contract_id: e for e in d.exchange}
    skews = []
    for t in d.local:
        e = ex.get(t.contract_id) if t.contract_id is not None else None
        if e is not None and t.opened_at is not None:
            skews.append(t.opened_at.timestamp() - e.purchase_time)
    if not skews:
        return Check("clock_skew_within_tolerance", True, 0, "no trades to measure")
    worst = max(skews, key=abs)
    mean = sum(skews) / len(skews)
    return Check("clock_skew_within_tolerance", abs(worst) <= d.skew_tolerance_s, len(skews),
                 f"worst {worst:+.2f}s, mean {mean:+.2f}s, tolerance {d.skew_tolerance_s}s (local minus exchange)")


def check_nothing_stuck(d: AuditData) -> Check:
    stuck = [t for t in d.local if t.state in STUCK]
    return Check("nothing_stuck", not stuck, len(d.local), "" if not stuck else f"left in {_ids(f'{t.state}:{t.intent_id[:8]}' for t in stuck)}")


ALL_CHECKS = (check_exchange_has_every_settled_trade, check_no_unknown_trades, check_money_fields_match, check_profit_formula,
              check_outcome_coverage, check_outcome_matches_rule, check_balance_chain, check_stake_sizing, check_transitions_legal,
              check_exposed_were_approved, check_no_duplicate_contracts, check_clock_skew, check_nothing_stuck)


def run_checks(d: AuditData) -> list[Check]:
    return [c(d) for c in ALL_CHECKS]


def render(checks: list[Check], trades: int) -> str:
    lines = [f"ACCURACY AUDIT over {trades} recorded trades", ""]
    for c in checks:
        lines.append(f"  {'PASS' if c.passed else 'FAIL'}  {c.name:<36} checked {c.checked:>4}  {c.detail}".rstrip())
    failed = [c for c in checks if not c.passed]
    lines += ["", f"RESULT: {'ALL PASSED' if not failed else str(len(failed)) + ' FAILED'}"]
    return "\n".join(lines)
