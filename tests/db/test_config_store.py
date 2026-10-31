import json

import pytest
from sqlalchemy import text

from derivbot.config.risk import config_hash, parse_risk_config
from derivbot.db.config_store import store_config
from tests.unit.test_config import VALID

pytestmark = pytest.mark.postgres


def test_same_config_is_stored_once_and_a_change_makes_a_new_version(conn):
    a = parse_risk_config(VALID)
    id1 = store_config(conn, "risk", a)
    id2 = store_config(conn, "risk", parse_risk_config({**VALID, "max_stake_abs": "5.00"}))   # same limit, other spelling
    assert id1 == id2
    id3 = store_config(conn, "risk", parse_risk_config({**VALID, "max_stake_abs": "6"}))
    assert id3 != id1
    n = conn.execute(text("SELECT count(*) FROM config_versions WHERE content_hash = :h"), {"h": config_hash(a)}).scalar_one()
    assert n == 1


def test_stored_content_is_the_canonical_form_and_hash_matches(conn):
    cfg = parse_risk_config(VALID)
    i = store_config(conn, "risk", cfg)
    content, h = conn.execute(text("SELECT content, content_hash FROM config_versions WHERE id = :i"), {"i": i}).one()
    assert h == config_hash(cfg) and content["max_stake_abs"] == "5" and content["max_consecutive_losses"] == 4
    assert "SET_BY_OWNER" not in json.dumps(content)


def test_kind_mismatch_for_identical_content_is_refused(conn):
    cfg = parse_risk_config({**VALID, "max_stake_abs": "7"})
    store_config(conn, "risk", cfg)
    with pytest.raises(ValueError, match="kind"):
        store_config(conn, "system", cfg)
