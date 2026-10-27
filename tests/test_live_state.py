"""Ephemeral state invariants; no private confirmation or finalize engine."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.live import (
    MAX_TRANSCRIPT_CHARS,
    MAX_TURN_CHARS,
    MAX_TURNS,
    CommittedTurn,
    LiveSnapshot,
    PartialReason,
)
from app.services.live_state import LiveIntakeState, LiveStateConflict

pytestmark = pytest.mark.unit


@pytest.fixture
def state():
    return LiveIntakeState(
        "fictional-session", clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)
    )


def commit(state, text="invented", turn_id=None):
    snapshot = state.snapshot()
    return state.commit_turn(
        CommittedTurn(turn_id=turn_id or snapshot.last_turn_id + 1, text=text),
        expected_revision=snapshot.revision,
    )


@pytest.mark.parametrize("session_id", ["", "x" * 129])
def test_session_id_bounds(session_id):
    with pytest.raises(ValueError):
        LiveIntakeState(session_id)


def test_snapshot_shape_is_ephemeral_not_a_case_result(state):
    snapshot = state.snapshot()
    assert snapshot.revision == 0 and snapshot.last_turn_id == 0
    assert snapshot.phase == "pending" and not snapshot.input_pending
    assert set(snapshot.model_dump()) == {
        "session_id",
        "created_at",
        "revision",
        "phase",
        "turns",
        "input_pending",
        "partial_reason",
    }
    assert LiveSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot
    for method in (
        "finalize",
        "confirm_review",
        "confirm_fact",
        "propose_analysis",
        "acknowledge_extraction",
    ):
        assert not hasattr(state, method)


@pytest.mark.parametrize(
    "field", ["facts", "analysis_proposal", "result", "reviewed_revision"]
)
def test_removed_confirmation_fields_are_not_accepted(state, field):
    with pytest.raises(ValidationError):
        LiveSnapshot.model_validate({**state.snapshot().model_dump(), field: None})


def test_commits_are_ordered_idempotent_and_immutable(state):
    first = commit(state, " فرضی مسئلہ ")
    assert first.turns[0].text == "فرضی مسئلہ" and first.revision == 1
    assert commit(state, "فرضی مسئلہ", turn_id=1) == first
    for turn_id, text in ((1, "rewritten"), (3, "gap")):
        with pytest.raises(LiveStateConflict):
            commit(state, text, turn_id)
    assert state.snapshot() == first
    second = commit(state, "Fictional correction")
    assert second.last_turn_id == 2 and second.revision == 2
    assert first.last_turn_id == 1
    object.__setattr__(second.turns[0], "text", "mutated-copy")
    assert state.snapshot().turns[0].text == "فرضی مسئلہ"


def test_stale_revision_rejected_without_mutation(state):
    before = commit(state)
    with pytest.raises(LiveStateConflict):
        state.commit_turn(
            CommittedTurn(turn_id=2, text="invented"), expected_revision=0
        )
    with pytest.raises(LiveStateConflict):
        state.set_input_pending(True, expected_revision=0)
    assert state.snapshot() == before


def test_committed_text_is_not_tail_completeness_proof(state):
    state.set_input_pending(True, expected_revision=0)
    snapshot = commit(state)
    assert snapshot.input_pending
    with pytest.raises(ValueError):
        state.set_input_pending("false", expected_revision=snapshot.revision)
    assert state.snapshot() == snapshot


@pytest.mark.parametrize(
    "turn_id,text",
    [
        (0, "invented"),
        (MAX_TURNS + 1, "invented"),
        (1, ""),
        (1, " "),
        (1, "x" * (MAX_TURN_CHARS + 1)),
    ],
)
def test_turn_validation_bounds(turn_id, text):
    with pytest.raises(ValidationError):
        CommittedTurn(turn_id=turn_id, text=text)


def test_total_transcript_limit_is_atomic(state):
    text = "x" * MAX_TURN_CHARS
    for _ in range(MAX_TRANSCRIPT_CHARS // (MAX_TURN_CHARS + 1)):
        commit(state, text)
    before = state.snapshot()
    with pytest.raises(ValueError, match="transcript limit"):
        commit(state, text)
    assert state.snapshot() == before


def test_turn_count_limit_is_bounded(state):
    for _ in range(MAX_TURNS):
        commit(state)
    before = state.snapshot()
    with pytest.raises(ValidationError):
        commit(state)
    assert state.snapshot() == before


@pytest.mark.parametrize("reason", list(PartialReason))
def test_all_terminal_reasons_are_partial_and_first_reason_wins(state, reason):
    commit(state)
    stopped = state.stop(reason)
    assert stopped.phase == "partial" and stopped.partial_reason == reason
    assert state.stop() == stopped
    with pytest.raises(LiveStateConflict):
        commit(state)
    with pytest.raises(LiveStateConflict):
        state.set_input_pending(False, expected_revision=stopped.revision)
    assert state.snapshot() == stopped
