"""Event-loop-owned ephemeral transcript state, not a confirmation/case engine."""

from datetime import datetime, timezone
from typing import Callable

from app.models.live import (
    MAX_TRANSCRIPT_CHARS,
    CommittedTurn,
    LivePhase,
    LiveSnapshot,
    PartialReason,
)


class LiveStateConflict(ValueError):
    """Stale revision, out-of-order transcript or terminal session."""


class LiveIntakeState:
    def __init__(
        self,
        session_id: str,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        if not session_id or len(session_id) > 128:
            raise ValueError("session_id must contain 1..128 characters")
        self._state = LiveSnapshot(session_id=session_id, created_at=clock())

    def snapshot(self) -> LiveSnapshot:
        return self._state.model_copy(deep=True)

    def _editable(self, expected_revision: int):
        if self._state.phase != LivePhase.PENDING:
            raise LiveStateConflict("session is terminal")
        if expected_revision != self._state.revision:
            raise LiveStateConflict("stale revision")

    def _advance(self, **changes) -> LiveSnapshot:
        self._state = self._state.model_copy(
            update={**changes, "revision": self._state.revision + 1}
        )
        return self.snapshot()

    def commit_turn(
        self, turn: CommittedTurn, *, expected_revision: int
    ) -> LiveSnapshot:
        self._editable(expected_revision)
        turn = CommittedTurn.model_validate(turn.model_dump())
        if turn.turn_id <= self._state.last_turn_id:
            if self._state.turns[turn.turn_id - 1] == turn:
                return self.snapshot()
            raise LiveStateConflict("committed turns cannot be rewritten")
        if turn.turn_id != self._state.last_turn_id + 1:
            raise LiveStateConflict("turns must be committed in order")
        turns = (*self._state.turns, turn)
        if (
            sum(len(item.text) for item in turns) + len(turns) - 1
            > MAX_TRANSCRIPT_CHARS
        ):
            raise ValueError("transcript limit reached")
        # A committed transcript does not establish completeness of the input tail.
        return self._advance(turns=turns)

    def set_input_pending(
        self, pending: bool, *, expected_revision: int
    ) -> LiveSnapshot:
        self._editable(expected_revision)
        if type(pending) is not bool:
            raise ValueError("pending must be boolean")
        return self._advance(input_pending=pending)

    def stop(self, reason: PartialReason = PartialReason.STOPPED) -> LiveSnapshot:
        reason = PartialReason(reason)
        if self._state.phase != LivePhase.PENDING:
            return self.snapshot()
        return self._advance(phase=LivePhase.PARTIAL, partial_reason=reason)
