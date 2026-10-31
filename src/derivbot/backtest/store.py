"""Persist a backtest run (backtest_runs + backtest_trades). Real-data splits need the permit; simulated runs do not."""

from __future__ import annotations

import dataclasses
import json
import uuid

from sqlalchemy import Connection, text

from derivbot.backtest.engine import BacktestResult
from derivbot.backtest.metrics import compute
from derivbot.backtest.permit import Approval

SPLITS = ("train", "validation", "holdout", "forward", "simulated")


def store_backtest(conn: Connection, result: BacktestResult, *, git_commit: str, split: str,
                   approval: Approval | None = None) -> str:
    if split not in SPLITS:
        raise ValueError(f"unknown split {split!r}")
    if split != "simulated" and approval is None:
        raise ValueError("storing a real-data backtest needs the owner's approval record")
    run_id = str(uuid.uuid4())
    conn.execute(
        text("INSERT INTO backtest_runs (id, strategy_id, strategy_version, params, data_range, split, payout_model, "
             "git_commit, metrics) VALUES (:id, :sid, :sv, CAST(:p AS jsonb), CAST(:d AS jsonb), :sp, CAST(:pm AS jsonb), "
             ":g, CAST(:m AS jsonb))"),
        {"id": run_id, "sid": result.strategy_id, "sv": result.strategy_version, "p": json.dumps(result.params, default=str),
         "d": json.dumps({"symbol": result.symbol, "ticks": result.ticks, "first_epoch": result.first_epoch,
                          "last_epoch": result.last_epoch}), "sp": split, "pm": json.dumps(result.payout_model),
         "g": git_commit, "m": json.dumps(dataclasses.asdict(compute(result.trades)), default=str)})
    for t in result.trades:
        conn.execute(
            text("INSERT INTO backtest_trades (run_id, seq, entry_epoch, exit_epoch, direction, stake, win, profit) "
                 "VALUES (:r, :s, :e, :x, :d, :st, :w, :p)"),
            {"r": run_id, "s": t.seq, "e": t.entry_epoch, "x": t.exit_epoch, "d": t.contract_type, "st": t.stake,
             "w": t.win, "p": t.profit})
    return run_id
