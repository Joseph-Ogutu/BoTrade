"""State machine tests. The ORACLE below is a literal copy of the HANDBOOK 7 diagram, written separately from the
implementation so that a typo in the transition table is caught rather than mirrored."""

from collections import deque

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from derivbot.execution.state_machine import (
    EXPOSURE,
    TERMINAL,
    TRANSITIONS,
    Event,
    IllegalTransition,
    OrderMachine,
    State,
    is_terminal,
    needs_reconcile,
    next_state,
    retry_allowed,
)

# (from, event-name, to) exactly as drawn in the handbook state diagram
DIAGRAM = {
    ("INTENT_CREATED", "RISK_FAIL", "RISK_REJECTED"),
    ("INTENT_CREATED", "RISK_PASS", "RISK_APPROVED"),
    ("RISK_APPROVED", "PROPOSAL_REQUEST", "PROPOSAL_REQUESTED"),
    ("PROPOSAL_REQUESTED", "PROPOSAL_BAD", "PROPOSAL_REJECTED"),
    ("PROPOSAL_REQUESTED", "SEND_BUY", "BUY_SENT"),
    ("BUY_SENT", "BUY_CONFIRMED", "OPEN"),
    ("BUY_SENT", "BUY_NO_REPLY", "BUY_UNKNOWN"),
    ("BUY_UNKNOWN", "FOUND_IN_PORTFOLIO", "OPEN"),
    ("BUY_UNKNOWN", "NOT_FOUND_AFTER_WINDOW", "NOT_PLACED"),
    ("BUY_UNKNOWN", "AMBIGUOUS", "MANUAL_REVIEW"),
    ("OPEN", "EXPIRY", "SETTLED"),
    ("OPEN", "EARLY_SELL", "SOLD"),
}
DIAGRAM_TERMINALS = {"RISK_REJECTED", "PROPOSAL_REJECTED", "NOT_PLACED", "MANUAL_REVIEW", "SETTLED", "SOLD"}


def as_triples():
    return {(a.value, e.value, b.value) for (a, e), b in TRANSITIONS.items()}


def test_table_equals_the_handbook_diagram_exactly():
    assert as_triples() == DIAGRAM
    assert {s.value for s in TERMINAL} == DIAGRAM_TERMINALS
    assert len(State) == 12


def test_every_state_and_event_pair_outside_the_diagram_is_illegal():
    legal = {(a, e) for a, e, _ in DIAGRAM}
    for s in State:
        for e in Event:
            if (s.value, e.value) in legal:
                assert next_state(s, e).value in {b for a, ev, b in DIAGRAM if (a, ev) == (s.value, e.value)}
            else:
                with pytest.raises(IllegalTransition):
                    next_state(s, e)


def test_reachability_every_state_reachable_and_every_path_ends_in_a_terminal():
    graph = {}
    for (a, _), b in TRANSITIONS.items():
        graph.setdefault(a, set()).add(b)
    seen, q = {State.INTENT_CREATED}, deque([State.INTENT_CREATED])
    while q:
        for nxt in graph.get(q.popleft(), ()):
            if nxt not in seen:
                seen.add(nxt)
                q.append(nxt)
    assert seen == set(State)                      # nothing is unreachable
    for s in State:                                # no dead ends: every non-terminal has a way out
        assert is_terminal(s) or graph.get(s)
        assert not is_terminal(s) or not graph.get(s)


def test_buy_unknown_can_never_reach_buy_sent_by_any_path():
    graph = {}
    for (a, _), b in TRANSITIONS.items():
        graph.setdefault(a, set()).add(b)
    seen, q = {State.BUY_UNKNOWN}, deque([State.BUY_UNKNOWN])
    while q:
        for nxt in graph.get(q.popleft(), ()):
            if nxt not in seen:
                seen.add(nxt)
                q.append(nxt)
    assert State.BUY_SENT not in seen and State.PROPOSAL_REQUESTED not in seen
    assert seen == {State.BUY_UNKNOWN, State.OPEN, State.NOT_PLACED, State.MANUAL_REVIEW, State.SETTLED, State.SOLD}


def test_buy_sent_is_entered_from_exactly_one_state():
    assert {a for (a, _), b in TRANSITIONS.items() if b is State.BUY_SENT} == {State.PROPOSAL_REQUESTED}


def test_retry_is_never_allowed_and_buy_unknown_needs_reconciliation():
    assert all(retry_allowed(s) is False for s in State)
    assert needs_reconcile(State.BUY_UNKNOWN) and not any(needs_reconcile(s) for s in State if s is not State.BUY_UNKNOWN)


def test_exposure_states_include_everything_that_may_hold_a_contract():
    assert EXPOSURE == {State.BUY_SENT, State.BUY_UNKNOWN, State.OPEN, State.MANUAL_REVIEW}
    assert not EXPOSURE & {State.NOT_PLACED, State.RISK_REJECTED, State.PROPOSAL_REJECTED, State.SETTLED, State.SOLD}


def test_machine_does_not_mutate_on_an_illegal_event():
    m = OrderMachine()
    with pytest.raises(IllegalTransition):
        m.apply(Event.BUY_CONFIRMED)
    assert m.state is State.INTENT_CREATED and m.history == []


def test_happy_path_and_the_lost_reply_path():
    m = OrderMachine()
    for ev in (Event.RISK_PASS, Event.PROPOSAL_REQUEST, Event.SEND_BUY, Event.BUY_CONFIRMED, Event.EXPIRY):
        m.apply(ev)
    assert m.state is State.SETTLED and m.buy_sent_count == 1
    m = OrderMachine()
    for ev in (Event.RISK_PASS, Event.PROPOSAL_REQUEST, Event.SEND_BUY, Event.BUY_NO_REPLY):
        m.apply(ev)
    assert m.state is State.BUY_UNKNOWN
    with pytest.raises(IllegalTransition):
        m.apply(Event.SEND_BUY)                    # the blind retry the old bot could do
    m.apply(Event.FOUND_IN_PORTFOLIO)
    assert m.state is State.OPEN and m.buy_sent_count == 1


def test_restore_continues_from_a_stored_state():
    m = OrderMachine.restore(State.BUY_UNKNOWN)
    assert m.can(Event.FOUND_IN_PORTFOLIO) and not m.can(Event.SEND_BUY)


# ------------------------------------------------------------------ property tests

events = st.sampled_from(list(Event))


@given(st.lists(events, max_size=40))
@settings(max_examples=400, deadline=None)
def test_no_event_sequence_ever_produces_an_illegal_transition(seq):
    m = OrderMachine()
    for ev in seq:
        before = (m.state, list(m.history))
        try:
            m.apply(ev)
        except IllegalTransition:
            assert (m.state, m.history) == before   # a refused event changes nothing
    # the recorded path is a path in the handbook diagram
    for a, ev, b in m.history:
        assert (a.value, ev.value, b.value) in DIAGRAM
    assert m.buy_sent_count <= 1


@given(st.lists(events, max_size=40))
@settings(max_examples=300, deadline=None)
def test_terminal_states_are_absorbing(seq):
    m = OrderMachine()
    for ev in seq:
        was_terminal = is_terminal(m.state)
        state = m.state
        try:
            m.apply(ev)
            assert not was_terminal
        except IllegalTransition:
            pass
        if was_terminal:
            assert m.state is state


@given(st.lists(events, max_size=40))
@settings(max_examples=300, deadline=None)
def test_after_buy_unknown_the_only_possible_futures_are_resolutions(seq):
    m = OrderMachine()
    visited_unknown = False
    for ev in seq:
        try:
            m.apply(ev)
        except IllegalTransition:
            continue
        if visited_unknown:
            assert m.state in {State.OPEN, State.NOT_PLACED, State.MANUAL_REVIEW, State.SETTLED, State.SOLD, State.BUY_UNKNOWN}
            assert m.state is not State.BUY_SENT
        if m.state is State.BUY_UNKNOWN:
            visited_unknown = True


class MachineModel(RuleBasedStateMachine):
    """Stateful model: fire any event at any time, check invariants after every step."""

    def __init__(self):
        super().__init__()
        self.m = OrderMachine()

    @rule(ev=events)
    def fire(self, ev):
        try:
            self.m.apply(ev)
        except IllegalTransition:
            pass

    @invariant()
    def at_most_one_buy(self):
        assert self.m.buy_sent_count <= 1

    @invariant()
    def history_is_contiguous(self):
        prev = State.INTENT_CREATED
        for a, _, b in self.m.history:
            assert a is prev
            prev = b
        assert prev is self.m.state


TestMachineModel = MachineModel.TestCase
TestMachineModel.settings = settings(max_examples=100, stateful_step_count=30, deadline=None)


def test_database_state_check_constraint_matches_the_enum():
    import importlib.util
    from pathlib import Path

    p = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0001_initial_schema.py"
    spec = importlib.util.spec_from_file_location("mig0001", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert set(mod.STATES) == {s.value for s in State}
