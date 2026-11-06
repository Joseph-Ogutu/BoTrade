import json
from decimal import Decimal
from pathlib import Path

import pytest

from derivbot.common import redaction
from derivbot.config.risk import (
    SET_BY_OWNER,
    RiskConfigError,
    StrategyApprovals,
    config_hash,
    load_risk_config,
    load_strategy_approvals,
)
from derivbot.config.settings import SettingsError, load_settings, parse_env_file

ROOT = Path(__file__).resolve().parents[2]

VALID = {
    "risk_per_trade_pct": "0.01", "max_stake_abs": "5", "min_stake_abs": None, "max_daily_loss_abs": "20",
    "max_daily_loss_pct_of_start_balance": None, "max_consecutive_losses": 4, "max_open_positions": 1,
    "max_trades_per_hour": 30, "max_proposal_price_slippage": "0.02", "stale_data_max_age_s": 10,
    "min_net_payout_ratio": "0.80", "reconcile_max_age_s": 120, "recent_duplicate_window_s": 30,
    "daily_reset_utc_hour": 0,
}


def write(tmp_path, data, name="risk.json"):
    p = tmp_path / name
    p.write_text(json.dumps(data))
    return p


# ---------------------------------------------------------------- risk config

def test_a_risk_file_with_every_owner_value_unset_fails_loudly_and_lists_them_all(tmp_path):
    names = ("risk_per_trade_pct", "max_stake_abs", "max_daily_loss_abs", "max_consecutive_losses",
             "max_open_positions", "max_trades_per_hour", "max_proposal_price_slippage",
             "stale_data_max_age_s", "min_net_payout_ratio", "reconcile_max_age_s",
             "recent_duplicate_window_s", "daily_reset_utc_hour")
    unset = {n: SET_BY_OWNER for n in names} | {"min_stake_abs": None, "max_daily_loss_pct_of_start_balance": None}
    with pytest.raises(RiskConfigError) as ei:
        load_risk_config(write(tmp_path, unset))
    msg = str(ei.value)
    for name in names:
        assert name in msg
    assert SET_BY_OWNER in msg


def test_the_committed_risk_file_holds_the_values_the_owner_approved():
    """Approved by the owner in chat (demo account). 2026-11-05: first values. Later that day the owner asked for a bigger demo stake
    for the day-long soak, so stake cap 25, then 100 (1 percent of the 10,000 demo balance, the most the 0.01 risk fraction allows), then 200 with the risk fraction raised to 2 percent, then on 2026-11-06 the owner asked for each bet to be 15 percent of the balance available, read fresh before the bet (fraction 0.15, the absolute cap 5000 no longer binds), with daily loss 4000 and 25 consecutive losses (a 3-loss lock cannot clear without a win and
    would stall the soak). These are DEMO soak values, to be reviewed before any real use. Changing a number is a deliberate edit."""
    cfg = load_risk_config(ROOT / "config" / "risk.json")
    assert (cfg.risk_per_trade_pct, cfg.max_stake_abs, cfg.max_daily_loss_abs) == (Decimal("0.15"), Decimal("5000.00"), Decimal("4000.00"))
    assert (cfg.max_consecutive_losses, cfg.max_open_positions, cfg.max_trades_per_hour) == (25, 1, 20)
    # 2026-11-06: the owner's option B rule for live demo bets: at most 5 a day, stop for the day at 3 losses
    assert (cfg.max_trades_per_day, cfg.max_losses_per_day) == (5, 3)
    assert cfg.max_consecutive_wins_per_day == 3                                  # 2026-11-06: stop for the day after 3 wins in a row
    assert cfg.max_proposal_price_slippage == Decimal("0.02") and cfg.stale_data_max_age_s == 10
    assert cfg.min_net_payout_ratio == Decimal("0.80") and cfg.reconcile_max_age_s == 120
    assert cfg.recent_duplicate_window_s == 30 and cfg.daily_reset_utc_hour == 0


def test_valid_config_loads_with_decimal_money(tmp_path):
    cfg = load_risk_config(write(tmp_path, VALID))
    assert cfg.risk_per_trade_pct == Decimal("0.01") and isinstance(cfg.max_stake_abs, Decimal)
    assert cfg.min_stake_abs is None


def test_missing_key_none_and_placeholder_are_all_unset(tmp_path):
    for bad in ({k: v for k, v in VALID.items() if k != "max_stake_abs"},
                {**VALID, "max_stake_abs": None},
                {**VALID, "max_stake_abs": SET_BY_OWNER}):
        with pytest.raises(RiskConfigError, match="max_stake_abs"):
            load_risk_config(write(tmp_path, bad))


def test_at_least_one_daily_loss_limit_is_required_and_both_may_be_set(tmp_path):
    neither = {**VALID, "max_daily_loss_abs": None, "max_daily_loss_pct_of_start_balance": None}
    with pytest.raises(RiskConfigError, match="daily loss"):
        load_risk_config(write(tmp_path, neither))
    pct_only = {**VALID, "max_daily_loss_abs": None, "max_daily_loss_pct_of_start_balance": "0.05"}
    assert load_risk_config(write(tmp_path, pct_only)).max_daily_loss_abs is None
    both = {**VALID, "max_daily_loss_pct_of_start_balance": "0.05"}
    assert load_risk_config(write(tmp_path, both)).max_daily_loss_pct_of_start_balance == Decimal("0.05")


@pytest.mark.parametrize("field,value", [
    ("risk_per_trade_pct", "0"), ("risk_per_trade_pct", "1.5"), ("risk_per_trade_pct", "-0.1"),
    ("max_stake_abs", "0"), ("max_stake_abs", "-1"), ("max_daily_loss_abs", "0"),
    ("max_consecutive_losses", 0), ("max_open_positions", 0), ("max_trades_per_hour", 0),
    ("max_proposal_price_slippage", "-0.01"), ("stale_data_max_age_s", 0), ("min_net_payout_ratio", "0"),
    ("daily_reset_utc_hour", 24), ("daily_reset_utc_hour", -1), ("max_stake_abs", "abc"),
])
def test_out_of_range_values_are_rejected(tmp_path, field, value):
    with pytest.raises(RiskConfigError):
        load_risk_config(write(tmp_path, {**VALID, field: value}))


def test_min_stake_floor_may_not_exceed_max_stake(tmp_path):
    with pytest.raises(RiskConfigError, match="min_stake_abs"):
        load_risk_config(write(tmp_path, {**VALID, "min_stake_abs": "10", "max_stake_abs": "5"}))


def test_unknown_keys_are_rejected_so_a_typo_cannot_silently_drop_a_limit(tmp_path):
    with pytest.raises(RiskConfigError, match="max_stake_abss"):
        load_risk_config(write(tmp_path, {**VALID, "max_stake_abss": "5"}))


def test_floats_are_refused_for_money_values(tmp_path):
    with pytest.raises(RiskConfigError, match="string"):
        load_risk_config(write(tmp_path, {**VALID, "risk_per_trade_pct": 0.01}))


def test_hash_is_stable_and_independent_of_key_order_and_decimal_spelling(tmp_path):
    a = load_risk_config(write(tmp_path, VALID, "a.json"))
    reordered = dict(reversed(list(VALID.items())))
    b = load_risk_config(write(tmp_path, {**reordered, "max_stake_abs": "5.00", "risk_per_trade_pct": "0.0100"}, "b.json"))
    assert config_hash(a) == config_hash(b) and len(config_hash(a)) == 64
    c = load_risk_config(write(tmp_path, {**VALID, "max_stake_abs": "6"}, "c.json"))
    assert config_hash(a) != config_hash(c)


def test_strategy_approvals_default_to_none_approved():
    assert load_strategy_approvals(ROOT / "config" / "strategies.json").approved == {}
    ap = StrategyApprovals(approved={"ema_trend": "1"})
    assert ap.is_approved("ema_trend", "1") and not ap.is_approved("ema_trend", "2") and not ap.is_approved("x", "1")


# ---------------------------------------------------------------- settings

ENV = {
    "TRADING_MODE": "demo", "ALLOW_REAL_TRADING": "false", "DERIV_APP_ID_DEMO": "12345",
    "DERIV_PAT_DEMO": "pat_FAKE_NOT_A_REAL_TOKEN_1234", "DERIV_ACCOUNT_ID_DEMO": "DEMO-ACC-1",
    "DATABASE_URL": "postgresql+psycopg://bot:FAKEPASSWORD99@db/derivbot", "REDIS_URL": "redis://r:6379/0",
    "TELEGRAM_BOT_TOKEN": "123456:FAKE-TELEGRAM-TOKEN-XYZ", "TELEGRAM_ALLOWED_CHAT_IDS": "111, 222",
    "ADMIN_API_TOKEN": "FAKE-ADMIN-TOKEN-ABCDEF", "LOG_LEVEL": "INFO",
}


@pytest.fixture(autouse=True)
def _clean_secrets():
    redaction.reset_secrets()
    yield
    redaction.reset_secrets()


def test_settings_parse_and_defaults():
    s = load_settings(ENV)
    assert s.trading_mode == "demo" and s.allow_real_trading is False
    assert s.telegram_allowed_chat_ids == [111, 222]
    assert s.deriv_app_id_demo == "12345"


def test_secrets_never_appear_in_repr_str_or_dump():
    s = load_settings(ENV)
    for text in (repr(s), str(s), json.dumps(s.safe_dump())):
        for secret in ("pat_FAKE_NOT_A_REAL_TOKEN_1234", "FAKEPASSWORD99", "FAKE-TELEGRAM-TOKEN-XYZ", "FAKE-ADMIN-TOKEN-ABCDEF"):
            assert secret not in text


def test_loading_settings_registers_secrets_with_the_log_redactor():
    load_settings(ENV)
    out = redaction.redact_text("using pat_FAKE_NOT_A_REAL_TOKEN_1234 and FAKE-ADMIN-TOKEN-ABCDEF")
    assert "FAKE_NOT_A_REAL" not in out and "FAKE-ADMIN" not in out


def test_missing_required_values_fail_loudly_and_list_all():
    with pytest.raises(SettingsError) as ei:
        load_settings({"TRADING_MODE": "demo"})
    msg = str(ei.value)
    assert "DATABASE_URL" in msg and "REDIS_URL" in msg


def test_invalid_mode_and_bool_and_chat_ids_are_rejected():
    for bad in ({**ENV, "TRADING_MODE": "paper"}, {**ENV, "ALLOW_REAL_TRADING": "maybe"},
                {**ENV, "TELEGRAM_ALLOWED_CHAT_IDS": "abc"}, {**ENV, "LOG_LEVEL": "LOUD"}):
        with pytest.raises(SettingsError):
            load_settings(bad)


def test_demo_credentials_are_optional_so_the_collector_and_tests_run_without_them():
    env = {k: v for k, v in ENV.items() if not k.startswith("DERIV_") and k != "ADMIN_API_TOKEN"}
    s = load_settings(env)
    assert s.deriv_pat_demo is None and s.admin_api_token is None


def test_env_file_parser_handles_comments_quotes_and_blank_lines(tmp_path):
    p = tmp_path / "x.cfg"
    p.write_text("# comment\n\nA=1\nB = 'two words'\nC=\"3\"\nBAD LINE\nD=\n")
    assert parse_env_file(p) == {"A": "1", "B": "two words", "C": "3", "D": ""}


# ---------------------------------------------------------------- docker secrets (NAME_FILE) and URL assembly

def test_file_secrets_supply_values_and_are_registered_with_the_redactor(tmp_path):
    f = tmp_path / "pat"
    f.write_text("pat_FAKE_FROM_FILE_9999999\n")
    s = load_settings({"DATABASE_URL": "postgresql+psycopg://u:p@h/d", "REDIS_URL": "redis://r/0", "DERIV_PAT_DEMO_FILE": str(f)})
    assert s.deriv_pat_demo.get_secret_value() == "pat_FAKE_FROM_FILE_9999999"
    assert "FROM_FILE" not in redaction.redact_text("x pat_FAKE_FROM_FILE_9999999 y")


def test_file_secret_errors_are_loud_and_never_echo_values(tmp_path):
    f = tmp_path / "s"
    f.write_text("VALUE-123456")
    base = {"DATABASE_URL": "postgresql+psycopg://u:p@h/d", "REDIS_URL": "redis://r/0"}
    with pytest.raises(SettingsError, match="both ADMIN_API_TOKEN and ADMIN_API_TOKEN_FILE"):
        load_settings({**base, "ADMIN_API_TOKEN": "x", "ADMIN_API_TOKEN_FILE": str(f)})
    with pytest.raises(SettingsError, match="does not exist"):
        load_settings({**base, "POSTGRES_PASSWORD_FILE": str(tmp_path / "missing")})
    empty = tmp_path / "e"
    empty.write_text("  \n")
    with pytest.raises(SettingsError, match="empty"):
        load_settings({**base, "POSTGRES_PASSWORD_FILE": str(empty)})


def test_urls_are_assembled_from_parts_with_quoted_passwords(tmp_path):
    pw = tmp_path / "pw"
    pw.write_text("p@ss:w/rd#1\n")
    rp = tmp_path / "rp"
    rp.write_text("redis:pw\n")
    s = load_settings({"POSTGRES_HOST": "postgres", "POSTGRES_USER": "bot", "POSTGRES_DB": "derivbot", "POSTGRES_PASSWORD_FILE": str(pw),
                       "REDIS_HOST": "redis", "REDIS_PASSWORD_FILE": str(rp)})
    assert s.database_url.get_secret_value() == "postgresql+psycopg://bot:p%40ss%3Aw%2Frd%231@postgres:5432/derivbot"
    assert s.redis_url.get_secret_value() == "redis://:redis%3Apw@redis:6379/0"
    assert "p%40ss" not in repr(s) and "redis%3Apw" not in repr(s)


def test_a_whole_url_wins_over_parts():
    s = load_settings({"DATABASE_URL": "postgresql+psycopg://a:b@whole/d", "POSTGRES_HOST": "parts", "REDIS_URL": "redis://r/0"})
    assert "whole" in s.database_url.get_secret_value()


def test_a_missing_optional_secret_file_means_not_provided_never_an_empty_secret(tmp_path):
    base = {"DATABASE_URL": "postgresql+psycopg://u:p@h/d", "REDIS_URL": "redis://r/0"}
    s = load_settings({**base, "ADMIN_API_TOKEN_FILE": str(tmp_path / "nope"), "DERIV_PAT_DEMO_FILE": str(tmp_path / "nope2"),
                       "TELEGRAM_BOT_TOKEN_FILE": str(tmp_path / "nope3")})
    assert s.admin_api_token is None and s.deriv_pat_demo is None and s.telegram_bot_token is None


def test_an_empty_optional_secret_file_means_not_provided(tmp_path):
    empty = tmp_path / "e"
    empty.write_text("\n")
    base = {"DATABASE_URL": "postgresql+psycopg://u:p@h/d", "REDIS_URL": "redis://r/0"}
    assert load_settings({**base, "ADMIN_API_TOKEN_FILE": str(empty)}).admin_api_token is None


def test_a_variable_that_only_ends_in_file_is_a_path_not_a_secret_reference(tmp_path):
    base = {"DATABASE_URL": "postgresql+psycopg://u:p@h/d", "REDIS_URL": "redis://r/0"}
    # KILL_SWITCH_FILE would have been read as "supply KILL_SWITCH from this file" and failed on a missing path
    assert load_settings({**base, "KILL_SWITCH_FILE": str(tmp_path / "does-not-exist")}).trading_mode == "demo"


def test_the_optional_day_limits_default_to_none_and_must_be_positive_integers(tmp_path):
    cfg = load_risk_config(write(tmp_path, VALID))
    assert cfg.max_trades_per_day is None and cfg.max_losses_per_day is None and cfg.max_consecutive_wins_per_day is None
    cfg = load_risk_config(write(tmp_path, {**VALID, "max_trades_per_day": 5, "max_losses_per_day": 3}))
    assert (cfg.max_trades_per_day, cfg.max_losses_per_day) == (5, 3)
    for bad in (0, -1, 2.5, "x"):
        with pytest.raises(RiskConfigError):
            load_risk_config(write(tmp_path, {**VALID, "max_trades_per_day": bad}))
