import datetime as dt
import json

import pytest

from derivbot.api.dashboard import build_summary
from tests.db.test_report import D0, seed

pytestmark = pytest.mark.postgres


def test_summary_matches_the_seeded_day_and_leaks_no_ids(conn):
    seed(conn)
    s = build_summary(conn, D0 + dt.timedelta(hours=12))
    assert s["today"]["settled"] == 3 and s["today"]["wins"] == 1 and s["today"]["losses"] == 2
    assert s["today"]["pnl"] == pytest.approx(-1.05) and s["today"]["win_rate"] == pytest.approx(1 / 3)
    assert s["open_exposure"] == 1
    assert s["risk_rejections_today"] == {"market_data_fresh": 1}
    assert s["last_reconciliation"]["status"] == "clean"
    assert s["recent_events"][0]["text"].startswith("engage by telegram")
    assert s["balance"]["value"] == pytest.approx(998.95) and s["balance_day_start"] == pytest.approx(1000.0)
    assert s["equity_curve"] == [1000.0, 998.95]
    assert len(s["recent_trades"]) <= 15 and all(set(t) == {"time", "symbol", "type", "stake", "state", "profit"} for t in s["recent_trades"])
    text = json.dumps(s)
    assert "'A'" not in text and "account" not in text and s["as_of"].endswith("Z")


def test_an_empty_database_gives_a_complete_empty_summary(conn):
    s = build_summary(conn, D0)
    assert s["balance"] is None and s["recent_trades"] == [] and s["today"]["settled"] == 0 and s["today"]["win_rate"] is None
    assert s["equity_curve"] == [] and s["last_reconciliation"] is None and s["recent_events"] == []
