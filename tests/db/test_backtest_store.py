from decimal import Decimal as D

import pytest
from sqlalchemy import text

from derivbot.backtest.engine import run_backtest
from derivbot.backtest.payout import PayoutModel, Quote
from derivbot.backtest.permit import Approval
from derivbot.backtest.series import simulate_digits
from derivbot.backtest.store import store_backtest
from tests.unit.test_backtest import Const

pytestmark = pytest.mark.postgres

PM = PayoutModel([Quote("DIGITEVEN", D("1"), D("1.95"))])


def result():
    return run_backtest(simulate_digits(300, seed=9), lambda s: Const(duration=2), PM, check_causality=False)


def test_a_simulated_run_is_stored_with_trades_metrics_and_payout_model(conn):
    res = result()
    run_id = store_backtest(conn, res, git_commit="abc1234", split="simulated")
    row = conn.execute(text("SELECT strategy_id, split, git_commit, metrics, payout_model FROM backtest_runs WHERE id = :i"),
                       {"i": run_id}).one()
    assert (row.strategy_id, row.split, row.git_commit) == ("const", "simulated", "abc1234")
    assert row.metrics["n"] == len(res.trades) and row.payout_model["quotes"][0]["payout"] == "1.95"
    n = conn.execute(text("SELECT count(*) FROM backtest_trades WHERE run_id = :i"), {"i": run_id}).scalar_one()
    assert n == len(res.trades) > 20


def test_real_splits_are_refused_without_an_approval_record(conn):
    for split in ("train", "validation", "holdout", "forward"):
        with pytest.raises(ValueError, match="approval"):
            store_backtest(conn, result(), git_commit="x", split=split)
    run_id = store_backtest(conn, result(), git_commit="x", split="holdout", approval=Approval("2026-11-16", "J", "STRUCTURE_FOUND"))
    assert run_id


def test_unknown_split_is_refused(conn):
    with pytest.raises(ValueError):
        store_backtest(conn, result(), git_commit="x", split="whatever")
