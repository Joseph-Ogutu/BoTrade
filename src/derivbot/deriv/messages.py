"""Typed Deriv WebSocket messages and request builders.

Models are hand-written for the messages this bot uses and checked against the
vendored official schemas in schemas/deriv (see tests/unit/test_models.py).
Money fields are Decimal. Deriv sends some money fields as strings
(proposal_open_contract) and others as numbers; both parse the same way.

Request builders enforce bot rules at the edge:
- buy is by proposal id with a price cap only. Parameter buys (`buy: 1`) are not
  deduplicated by Deriv, so they are refused here.
- proposal uses basis=stake and `underlying_symbol` (the new API field name).
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from derivbot.deriv.errors import DerivApiError


_ID_RE = re.compile(r"^[\w-]{32,128}$")  # pattern from the official buy and forget schemas


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


# ---------------------------------------------------------------- requests

def req_ticks(symbol: str, req_id: int, subscribe: bool = True) -> dict[str, Any]:
    return {"ticks": symbol, "subscribe": 1 if subscribe else 0, "req_id": req_id}


def req_ticks_history(symbol: str, count: int, end: str | int, req_id: int,
                      start: int | None = None) -> dict[str, Any]:
    req: dict[str, Any] = {"ticks_history": symbol, "count": count, "end": end, "style": "ticks",
                           "req_id": req_id}
    if start is not None:
        req["start"] = start
    return req


def req_balance(req_id: int, subscribe: bool = True) -> dict[str, Any]:
    return {"balance": 1, "subscribe": 1 if subscribe else 0, "req_id": req_id}


def req_ping(req_id: int) -> dict[str, Any]:
    return {"ping": 1, "req_id": req_id}


def req_portfolio(req_id: int) -> dict[str, Any]:
    return {"portfolio": 1, "req_id": req_id}


def req_forget(subscription_id: str, req_id: int) -> dict[str, Any]:
    if not _ID_RE.match(subscription_id or ""):
        raise ValueError("invalid subscription id")
    return {"forget": subscription_id, "req_id": req_id}


def req_proposal(underlying_symbol: str, contract_type: str, amount: Decimal, duration: int,
                 duration_unit: str, currency: str, req_id: int,
                 barrier: str | None = None) -> dict[str, Any]:
    if amount <= 0:
        raise ValueError("stake amount must be positive")
    req: dict[str, Any] = {
        "proposal": 1,
        "subscribe": 1,
        "req_id": req_id,
        "amount": float(amount),
        "basis": "stake",
        "contract_type": contract_type,
        "currency": currency,
        "duration": duration,
        "duration_unit": duration_unit,
        "underlying_symbol": underlying_symbol,
    }
    if barrier is not None:
        req["barrier"] = barrier
    return req


def req_buy(proposal_id: str, max_price: Decimal, req_id: int) -> dict[str, Any]:
    if not isinstance(proposal_id, str) or not _ID_RE.match(proposal_id):
        raise ValueError("buy must reference a proposal id; parameter buys are not allowed")
    if max_price <= 0:
        raise ValueError("price cap must be positive")
    return {"buy": proposal_id, "price": float(max_price), "req_id": req_id}


# ---------------------------------------------------------------- response bodies

class Tick(_Model):
    symbol: str
    epoch: int
    quote: Decimal
    pip_size: int
    ask: Decimal | None = None
    bid: Decimal | None = None
    id: str | None = None


class Balance(_Model):
    balance: Decimal
    currency: str
    loginid: str
    id: str | None = None


class Proposal(_Model):
    id: str
    ask_price: Decimal
    payout: Decimal
    spot: Decimal | None = None
    spot_time: int | None = None
    date_start: int | None = None
    longcode: str | None = None

    @property
    def r(self) -> Decimal:
        """Profit per unit stake on a win: (payout - stake) / stake."""
        return (self.payout - self.ask_price) / self.ask_price


class BuyResult(_Model):
    contract_id: int
    buy_price: Decimal
    payout: Decimal
    balance_after: Decimal
    transaction_id: int
    purchase_time: int | None = None
    start_time: int | None = None
    longcode: str | None = None
    shortcode: str | None = None


class OpenContract(_Model):
    contract_id: int
    is_sold: int = 0
    is_expired: int = 0
    status: str | None = None
    buy_price: Decimal | None = None
    payout: Decimal | None = None
    profit: Decimal | None = None
    sell_price: Decimal | None = None
    underlying_symbol: str | None = None
    contract_type: str | None = None
    date_expiry: int | None = None

    @property
    def is_settled(self) -> bool:
        """Deriv docs: is_sold and is_expired flip when the contract settles.

        UNVERIFIED which flag Deriv sets first for tick contracts; treat either as
        settled but confirm profit and sell_price are present before recording a result.
        """
        return self.is_sold == 1 or self.is_expired == 1


class PortfolioContract(_Model):
    contract_id: int
    buy_price: Decimal | None = None
    payout: Decimal | None = None
    contract_type: str | None = None
    underlying_symbol: str | None = None
    transaction_id: int | None = None
    purchase_time: int | None = None
    expiry_time: int | None = None


# ---------------------------------------------------------------- envelopes

class _Msg(_Model):
    msg_type: str
    req_id: int | None = None


class TickMsg(_Msg):
    msg_type: Literal["tick"] = "tick"
    tick: Tick
    subscription_id: str | None = None


class BalanceMsg(_Msg):
    msg_type: Literal["balance"] = "balance"
    balance: Balance
    subscription_id: str | None = None


class ProposalMsg(_Msg):
    msg_type: Literal["proposal"] = "proposal"
    proposal: Proposal
    subscription_id: str | None = None


class BuyMsg(_Msg):
    msg_type: Literal["buy"] = "buy"
    buy: BuyResult


class OpenContractMsg(_Msg):
    msg_type: Literal["proposal_open_contract"] = "proposal_open_contract"
    contract: OpenContract
    subscription_id: str | None = None


class PortfolioMsg(_Msg):
    msg_type: Literal["portfolio"] = "portfolio"
    contracts: list[PortfolioContract] = Field(default_factory=list)


class PongMsg(_Msg):
    msg_type: Literal["ping"] = "ping"


class UnknownMsg(_Msg):
    raw: dict[str, Any] = Field(default_factory=dict)


Message = TickMsg | BalanceMsg | ProposalMsg | BuyMsg | OpenContractMsg | PortfolioMsg | PongMsg | UnknownMsg


def parse_message(raw: dict[str, Any]) -> Message:
    """Parse one inbound message. Raises DerivApiError if it carries an `error`."""
    # UNVERIFIED-ERR: errors are assumed to arrive as a top-level "error" key (observed on the public endpoint only).
    if "error" in raw:
        raise DerivApiError.from_message(raw)
    msg_type = raw.get("msg_type")
    req_id = raw.get("req_id")
    sub = (raw.get("subscription") or {}).get("id") if isinstance(raw.get("subscription"), dict) else None
    if msg_type == "tick":
        return TickMsg(req_id=req_id, tick=raw["tick"], subscription_id=sub)
    if msg_type == "balance":
        return BalanceMsg(req_id=req_id, balance=raw["balance"], subscription_id=sub)
    if msg_type == "proposal":
        return ProposalMsg(req_id=req_id, proposal=raw["proposal"], subscription_id=sub)
    if msg_type == "buy":
        return BuyMsg(req_id=req_id, buy=raw["buy"])
    if msg_type == "proposal_open_contract":
        return OpenContractMsg(req_id=req_id, contract=raw["proposal_open_contract"], subscription_id=sub)
    if msg_type == "portfolio":
        return PortfolioMsg(req_id=req_id, contracts=(raw.get("portfolio") or {}).get("contracts", []))
    if msg_type == "ping":
        return PongMsg(req_id=req_id)
    return UnknownMsg(msg_type=str(msg_type), req_id=req_id, raw=raw)
