"""Order state machine (HANDBOOK 7). Pure: no I/O, no clock, no randomness.

Rules enforced structurally, by the shape of the transition table:
- BUY_SENT can be entered only from PROPOSAL_REQUESTED, so a buy is sent at most once per intent.
- BUY_UNKNOWN has no edge back to BUY_SENT: it can only resolve by reconciliation (OPEN, NOT_PLACED or
  MANUAL_REVIEW). A lost reply is never a reason to send the buy again.
- Terminal states have no outgoing edges.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class State(str, Enum):
    INTENT_CREATED = "INTENT_CREATED"
    RISK_REJECTED = "RISK_REJECTED"
    RISK_APPROVED = "RISK_APPROVED"
    PROPOSAL_REQUESTED = "PROPOSAL_REQUESTED"
    PROPOSAL_REJECTED = "PROPOSAL_REJECTED"
    BUY_SENT = "BUY_SENT"
    OPEN = "OPEN"
    BUY_UNKNOWN = "BUY_UNKNOWN"
    NOT_PLACED = "NOT_PLACED"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    SETTLED = "SETTLED"
    SOLD = "SOLD"


class Event(str, Enum):
    RISK_FAIL = "RISK_FAIL"
    RISK_PASS = "RISK_PASS"
    PROPOSAL_REQUEST = "PROPOSAL_REQUEST"
    PROPOSAL_BAD = "PROPOSAL_BAD"                    # price moved, payout unacceptable, or invalid
    SEND_BUY = "SEND_BUY"                            # price within cap
    BUY_CONFIRMED = "BUY_CONFIRMED"                  # buy response with a contract id
    BUY_NO_REPLY = "BUY_NO_REPLY"                    # timeout or disconnect after the buy was sent
    FOUND_IN_PORTFOLIO = "FOUND_IN_PORTFOLIO"
    NOT_FOUND_AFTER_WINDOW = "NOT_FOUND_AFTER_WINDOW"
    AMBIGUOUS = "AMBIGUOUS"
    EXPIRY = "EXPIRY"
    EARLY_SELL = "EARLY_SELL"


S, E = State, Event
TRANSITIONS: dict[tuple[State, Event], State] = {
    (S.INTENT_CREATED, E.RISK_FAIL): S.RISK_REJECTED,
    (S.INTENT_CREATED, E.RISK_PASS): S.RISK_APPROVED,
    (S.RISK_APPROVED, E.PROPOSAL_REQUEST): S.PROPOSAL_REQUESTED,
    (S.PROPOSAL_REQUESTED, E.PROPOSAL_BAD): S.PROPOSAL_REJECTED,
    (S.PROPOSAL_REQUESTED, E.SEND_BUY): S.BUY_SENT,
    (S.BUY_SENT, E.BUY_CONFIRMED): S.OPEN,
    (S.BUY_SENT, E.BUY_NO_REPLY): S.BUY_UNKNOWN,
    (S.BUY_UNKNOWN, E.FOUND_IN_PORTFOLIO): S.OPEN,
    (S.BUY_UNKNOWN, E.NOT_FOUND_AFTER_WINDOW): S.NOT_PLACED,
    (S.BUY_UNKNOWN, E.AMBIGUOUS): S.MANUAL_REVIEW,
    (S.OPEN, E.EXPIRY): S.SETTLED,
    (S.OPEN, E.EARLY_SELL): S.SOLD,
}

TERMINAL = frozenset({S.RISK_REJECTED, S.PROPOSAL_REJECTED, S.NOT_PLACED, S.MANUAL_REVIEW, S.SETTLED, S.SOLD})

# States in which money may be at risk on the exchange. MANUAL_REVIEW is terminal for the state machine but a contract
# may exist, so it still counts as exposure until an operator resolves it.
EXPOSURE = frozenset({S.BUY_SENT, S.BUY_UNKNOWN, S.OPEN, S.MANUAL_REVIEW})


class IllegalTransition(Exception):
    def __init__(self, state: State, event: Event) -> None:
        super().__init__(f"illegal transition: {event.value} in state {state.value}")
        self.state, self.event = state, event


def next_state(state: State, event: Event) -> State:
    try:
        return TRANSITIONS[(state, event)]
    except KeyError:
        raise IllegalTransition(state, event) from None


def is_terminal(state: State) -> bool:
    return state in TERMINAL


def retry_allowed(state: State) -> bool:
    """A buy is never re-sent, whatever the state. Kept as a named function so callers ask instead of assuming."""
    return False


def needs_reconcile(state: State) -> bool:
    return state is S.BUY_UNKNOWN


@dataclass
class OrderMachine:
    state: State = S.INTENT_CREATED
    history: list[tuple[State, Event, State]] = field(default_factory=list)

    def can(self, event: Event) -> bool:
        return (self.state, event) in TRANSITIONS

    def apply(self, event: Event) -> State:
        nxt = next_state(self.state, event)       # raises before any mutation
        self.history.append((self.state, event, nxt))
        self.state = nxt
        return nxt

    @property
    def buy_sent_count(self) -> int:
        return sum(1 for _, _, to in self.history if to is S.BUY_SENT)

    @classmethod
    def restore(cls, state: State, history: list[tuple[State, Event, State]] | None = None) -> "OrderMachine":
        return cls(state=state, history=list(history or []))
