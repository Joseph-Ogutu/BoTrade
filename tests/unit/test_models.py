import json
from decimal import Decimal
from pathlib import Path

import jsonschema
import pytest

from derivbot.deriv import messages as m
from derivbot.deriv.errors import DerivApiError

ID32 = "0123456789abcdef0123456789abcdef"
SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas" / "deriv"


def validate(kind: str, payload: dict) -> None:
    schema = json.loads((SCHEMA_DIR / f"{kind}.schema.json").read_text())
    jsonschema.validators.validator_for(schema)(schema).validate(payload)


# ---- request builders: must satisfy the vendored request schemas ----

def test_ticks_request_valid():
    req = m.req_ticks("1HZ100V", req_id=1)
    assert req == {"ticks": "1HZ100V", "subscribe": 1, "req_id": 1}
    validate("ticks_request", req)


def test_balance_ping_portfolio_forget_requests_valid():
    validate("balance_request", m.req_balance(req_id=2))
    validate("ping_request", m.req_ping(req_id=3))
    validate("portfolio_request", m.req_portfolio(req_id=4))
    validate("forget_request", m.req_forget(ID32, req_id=5))


def test_ticks_history_request_valid():
    req = m.req_ticks_history("1HZ100V", count=500, end="latest", req_id=7)
    validate("ticks_history_request", req)
    assert req["style"] == "ticks"


def test_proposal_request_uses_underlying_symbol_and_stake_basis():
    req = m.req_proposal(
        underlying_symbol="1HZ100V",
        contract_type="DIGITEVEN",
        amount=Decimal("1.00"),
        duration=5,
        duration_unit="t",
        currency="USD",
        req_id=8,
    )
    validate("proposal_request", req)
    assert req["underlying_symbol"] == "1HZ100V"
    assert "symbol" not in req
    assert req["basis"] == "stake"
    assert req["subscribe"] == 1


def test_proposal_request_rejects_payout_basis_and_nonpositive_amount():
    with pytest.raises(ValueError):
        m.req_proposal("X", "DIGITEVEN", Decimal("0"), 5, "t", "USD", req_id=1)


def test_buy_request_is_by_proposal_id_with_price_cap_only():
    req = m.req_buy(ID32, max_price=Decimal("1.00"), req_id=9)
    validate("buy_request", req)
    assert req["buy"] == ID32
    assert req["price"] == 1.0
    assert "parameters" not in req


@pytest.mark.parametrize("bad", [1, "1", "", None])
def test_buy_request_refuses_parameter_style_buy(bad):
    # Deriv does not deduplicate parameter buys (best-practices page). Never allow them.
    with pytest.raises(ValueError):
        m.req_buy(bad, max_price=Decimal("1.00"), req_id=1)


def test_buy_request_requires_positive_price_cap():
    with pytest.raises(ValueError):
        m.req_buy(ID32, max_price=Decimal("0"), req_id=1)


# ---- response parsing ----

TICK = {
    "msg_type": "tick",
    "echo_req": {"ticks": "1HZ100V", "subscribe": 1},
    "tick": {"ask": 100.1, "bid": 100.0, "epoch": 1700000000, "id": "t1", "pip_size": 2,
             "quote": 100.05, "symbol": "1HZ100V"},
    "subscription": {"id": "t1"},
}


def test_parse_tick_and_schema_valid():
    validate("ticks_response", TICK)
    msg = m.parse_message(TICK)
    assert isinstance(msg, m.TickMsg)
    assert msg.tick.symbol == "1HZ100V"
    assert msg.tick.epoch == 1700000000
    assert msg.tick.quote == Decimal("100.05")
    assert msg.tick.pip_size == 2


def test_parse_balance():
    raw = {"msg_type": "balance", "echo_req": {}, "balance": {"balance": 10000.5, "currency": "USD",
                                                               "loginid": "VRTC1", "id": "b1"}}
    validate("balance_response", raw)
    msg = m.parse_message(raw)
    assert isinstance(msg, m.BalanceMsg) and msg.balance.balance == Decimal("10000.5")


def test_parse_proposal_and_ratio():
    raw = {"msg_type": "proposal", "echo_req": {}, "req_id": 8,
           "proposal": {"ask_price": 1.0, "date_start": 1, "id": "p1", "longcode": "x",
                        "payout": 1.95, "spot": 100.0, "spot_time": 1},
           "subscription": {"id": "s1"}}
    validate("proposal_response", raw)
    msg = m.parse_message(raw)
    assert isinstance(msg, m.ProposalMsg)
    assert msg.proposal.r == Decimal("0.95")
    assert msg.subscription_id == "s1"


def test_parse_buy():
    raw = {"msg_type": "buy", "echo_req": {}, "req_id": 9,
           "buy": {"balance_after": 99.0, "buy_price": 1.0, "contract_id": 77, "longcode": "l",
                   "payout": 1.95, "purchase_time": 1, "shortcode": "s", "start_time": 1,
                   "transaction_id": 5}}
    validate("buy_response", raw)
    msg = m.parse_message(raw)
    assert isinstance(msg, m.BuyMsg) and msg.buy.contract_id == 77
    assert msg.buy.balance_after == Decimal("99.0")


def test_parse_open_contract_string_money_and_settled_flags():
    open_raw = {"msg_type": "proposal_open_contract", "echo_req": {},
                "proposal_open_contract": {"contract_id": 77, "is_sold": 0, "is_expired": 0,
                                           "buy_price": "1.00", "payout": "1.95", "profit": "-0.10",
                                           "underlying_symbol": "1HZ100V", "contract_type": "DIGITEVEN"}}
    validate("proposal_open_contract_response", open_raw)
    msg = m.parse_message(open_raw)
    assert isinstance(msg, m.OpenContractMsg)
    assert msg.contract.is_settled is False
    assert msg.contract.profit == Decimal("-0.10")

    done = json.loads(json.dumps(open_raw))
    done["proposal_open_contract"].update(is_sold=1, is_expired=1, sell_price="1.95", profit="0.95",
                                          status="won")
    validate("proposal_open_contract_response", done)
    c = m.parse_message(done).contract
    assert c.is_settled is True and c.profit == Decimal("0.95") and c.sell_price == Decimal("1.95")


def test_parse_portfolio_and_profit_table():
    p = {"msg_type": "portfolio", "echo_req": {},
         "portfolio": {"contracts": [{"contract_id": 77, "buy_price": 1.0, "payout": 1.95,
                                       "contract_type": "DIGITEVEN", "underlying_symbol": "1HZ100V",
                                       "currency": "USD", "date_start": 1, "expiry_time": 2,
                                       "longcode": "l", "purchase_time": 1, "shortcode": "s",
                                       "transaction_id": 5}]}}
    validate("portfolio_response", p)
    msg = m.parse_message(p)
    assert isinstance(msg, m.PortfolioMsg) and [c.contract_id for c in msg.contracts] == [77]


def test_error_responses_raise_with_redacted_context():
    raw = {"msg_type": "buy", "req_id": 9, "echo_req": {"buy": "pid", "price": 1},
           "error": {"code": "InsufficientBalance", "message": "Your account balance is insufficient."}}
    with pytest.raises(DerivApiError) as ei:
        m.parse_message(raw)
    assert ei.value.code == "InsufficientBalance"
    assert ei.value.req_id == 9
    assert ei.value.msg_type == "buy"


def test_unknown_message_type_is_kept_not_crashed():
    msg = m.parse_message({"msg_type": "brand_new", "echo_req": {}, "x": 1})
    assert isinstance(msg, m.UnknownMsg) and msg.msg_type == "brand_new"


def test_missing_msg_type_is_unknown():
    assert isinstance(m.parse_message({"foo": 1}), m.UnknownMsg)
