import random
from decimal import Decimal as D

import pytest

from derivbot.backtest.engine import run_backtest
from derivbot.backtest.payout import PayoutModel, Quote
from derivbot.backtest.series import simulate_random_walk
from derivbot.forward.shadow import CONTROL_ID, ShadowLedger, build_variants, wins
from derivbot.signals.base import Signal
from derivbot.signals.strategies import default_strategies

SYM = "1HZ100V"


class Rec:
    def __init__(self):
        self.outcomes, self.discards = [], []

    def out(self, o):
        self.outcomes.append(o)

    def dis(self, key, epoch, reason):
        self.discards.append((key, epoch, reason))


def feed(ledger, series, symbol=SYM):
    for i in range(len(series)):
        ledger.on_tick(symbol, D(repr(float(series.quotes[i]))), int(series.digits[i]), int(series.epochs[i]))


def test_the_contract_rule_a_tie_loses_for_both_sides():
    assert wins("CALL", D("100"), D("100.01")) and not wins("CALL", D("100"), D("100")) and not wins("CALL", D("100"), D("99.99"))
    assert wins("PUT", D("100"), D("99.99")) and not wins("PUT", D("100"), D("100")) and not wins("PUT", D("100"), D("100.01"))


@pytest.mark.parametrize("seed,rho", [(3, 0.0), (11, 0.25), (23, -0.25)])
def test_the_live_ledger_makes_exactly_the_contracts_the_backtest_engine_makes(seed, rho):
    """The equivalence the amendment promises: same series, same contracts (decision, entry, exit, side, win) for EVERY strategy."""
    series = simulate_random_walk(2400, seed, rho=rho, symbol=SYM)
    strategies = default_strategies()
    rec = Rec()
    ledger = ShadowLedger(build_variants(strategies, [SYM], controls=False), rec.out, rec.dis)
    feed(ledger, series)
    assert rec.discards == []
    payout = PayoutModel([Quote("CALL", D("1"), D("1.9")), Quote("PUT", D("1"), D("1.9"))])
    total = 0
    for key, strat in strategies.items():
        bt = run_backtest(series, lambda s, strat=strat: strat, payout, check_causality=False)
        live = [(o.signal_epoch, o.entry_epoch, o.exit_epoch, o.side, o.won) for o in rec.outcomes if o.variant == f"{key}:{SYM}"]
        want = [(t.decision_epoch, t.entry_epoch, t.exit_epoch, t.contract_type, t.win) for t in bt.trades]
        assert live == want, key
        assert ledger.completed[f"{key}:{SYM}"] == len(want)
        total += len(want)
    assert total > 20                                    # the comparison was not vacuous


class OneShot:
    """A strategy that signals once, on a chosen tick, and otherwise never."""
    id, version, duration_ticks, warmup_ticks = "oneshot", "1", 5, 0
    params: dict = {}

    def __init__(self, at):
        self.at = set(at)

    def signal(self, window):
        return Signal("CALL", None) if window.index - 1 in self.at else None


def ledger_for(strategy, rec, controls=False):
    return ShadowLedger(build_variants({"oneshot": strategy}, [SYM], controls=controls), rec.out, rec.dis)


def tick(ledger, i, q, interval=1, base=1000):
    ledger.on_tick(SYM, D(str(q)), 0, base + i * interval)


def test_entry_is_the_next_tick_exit_is_five_ticks_later_and_the_outcome_is_recorded_once():
    rec = Rec()
    lg = ledger_for(OneShot({2}), rec)
    quotes = [10, 10, 10, 11, 12, 13, 14, 15, 16, 17, 18]          # signal on tick 2; entry = tick 3 (11); exit = tick 8 (16)
    for i, q in enumerate(quotes):
        tick(lg, i, q)
    assert len(rec.outcomes) == 1
    o = rec.outcomes[0]
    assert (o.entry_quote, o.exit_quote, o.won, o.side) == (D("11"), D("16"), True, "CALL")
    assert (o.signal_epoch, o.entry_epoch, o.exit_epoch) == (1002, 1003, 1008)


def test_a_variant_never_holds_two_contracts_and_may_decide_on_the_exit_tick():
    rec = Rec()
    lg = ledger_for(OneShot({2, 3, 4, 8}), rec)                      # 3 and 4 fall inside the open contract and are ignored
    for i in range(20):
        tick(lg, i, 10 + i)
    assert [o.signal_epoch for o in rec.outcomes] == [1002, 1008]    # the decision on tick 8 is the exit tick of the first contract


def test_a_gap_inside_an_open_contract_discards_it_and_clears_the_history():
    rec = Rec()
    lg = ledger_for(OneShot({2, 30}), rec)
    for i in range(5):
        tick(lg, i, 10 + i)
    lg.on_tick(SYM, D("99"), 0, 1000 + 9)                           # epochs 1004 then 1009: a hole
    assert rec.discards == [(f"oneshot:{SYM}", 1002, "gap")] and rec.outcomes == []
    assert lg.discarded[f"oneshot:{SYM}"] == 1 and lg.completed[f"oneshot:{SYM}"] == 0 and lg.resets == 1


def test_a_disconnect_discards_open_contracts_and_decisions_wait_for_the_warmup_again():
    class Slow(OneShot):
        warmup_ticks = 3

    rec = Rec()
    lg = ledger_for(Slow({3, 4, 7}), rec)
    for i in range(6):
        tick(lg, i, 10 + i)
    lg.reset(reason="disconnect")
    assert [d[2] for d in rec.discards] == ["disconnect"]
    for i in range(6, 9):                                            # fresh history: indices 0 to 2, below the warm-up of 3
        tick(lg, i, 20 + i)
    assert lg.open_count() == 0 and rec.outcomes == []


def test_a_duplicate_or_out_of_order_tick_is_ignored_not_treated_as_a_gap():
    rec = Rec()
    lg = ledger_for(OneShot({1}), rec)
    tick(lg, 0, 10)
    tick(lg, 1, 11)
    tick(lg, 1, 11)                                                  # the same epoch again
    tick(lg, 0, 10)                                                  # an older epoch
    assert lg.resets == 0
    for i in range(2, 10):
        tick(lg, i, 11 + i)
    assert len(rec.outcomes) == 1


def test_r100_uses_its_own_two_second_interval_and_a_one_second_step_there_is_a_gap():
    rec = Rec()
    lg = ShadowLedger(build_variants({"oneshot": OneShot({0})}, ["R_100"], controls=False), rec.out, rec.dis)
    lg.on_tick("R_100", D("10"), 0, 1000)
    lg.on_tick("R_100", D("11"), 0, 1002)                            # exactly 2 s: contiguous
    assert lg.resets == 0
    lg.on_tick("R_100", D("12"), 0, 1003)                            # 1 s: not the nominal interval
    assert lg.resets == 1


def test_the_control_variant_flips_a_coin_with_no_price_input_and_is_reproducible_with_a_seed():
    def run(seed):
        rec = Rec()
        lg = ShadowLedger(build_variants({}, [SYM], controls=True), rec.out, rec.dis, rng=random.Random(seed))
        for i in range(3000):
            tick(lg, i, 100 + (i % 7))
        return [(o.signal_epoch, o.side) for o in rec.outcomes]

    a, b = run(5), run(5)
    assert a == b and len(a) > 50 and {s for _, s in a} == {"CALL", "PUT"}
    c = ShadowLedger(build_variants({}, [SYM], controls=True), lambda o: None, lambda *x: None).variants
    assert [v.strategy_id for v in c] == [CONTROL_ID] and c[0].strategy is None


def test_build_variants_makes_fourteen_registered_variants_plus_two_controls():
    vs = build_variants(default_strategies(), ["1HZ100V", "R_100"])
    assert len([v for v in vs if v.strategy_id != CONTROL_ID]) == 14 and len([v for v in vs if v.strategy_id == CONTROL_ID]) == 2
    assert len({v.key for v in vs}) == 16


def test_the_ledger_module_reads_no_stored_data():
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "src" / "derivbot" / "forward" / "shadow.py").read_text()
    code = re.sub(r'""".*?"""', "", src, flags=re.S)
    for banned in ("sqlite", "collector", "permit", "gate1", "load_real_series", "open(", "sqlalchemy"):
        assert banned not in code, banned
