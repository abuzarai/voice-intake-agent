"""Single-event-loop, bounded RAM admission; no legacy sessions or provider work."""

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import uuid4

from app.models.live import LivePhase, LiveSnapshot, PartialReason
from app.services.live_state import LiveIntakeState
from app.services.live_transcript import LiveTranscript


class LiveSessionUnauthorized(ValueError):
    """Unknown, expired, or mismatched capability (intentionally indistinguishable)."""


class LiveSessionClaimConflict(ValueError):
    """The session already has an offer or is no longer pending."""


class LiveAdmissionFull(ValueError):
    """Registry is at capacity, including retained stopped sessions."""


class LiveAdmissionRateLimited(ValueError):
    """The app-owned admission window is exhausted."""


@dataclass(frozen=True)
class IssuedLiveSession:
    session_id: str
    created_at: datetime
    expires_at: datetime
    capability: str = field(repr=False)


@dataclass
class _Session:
    state: LiveIntakeState
    transcript: LiveTranscript
    expires_at: datetime
    deadline: float
    capability_hash: bytes = field(repr=False)
    offer_started: bool = False


class LiveSessionRegistry:
    """Synchronous owner only; not thread-safe or shared across workers.

    Only successful creates consume the fixed-window quota. There is no per-IP
    tracking. Stopping retains a session until expiry for idempotent access.
    """

    def __init__(
        self,
        *,
        ttl_seconds: int,
        max_sessions: int,
        admissions_per_minute: int,
        monotonic: Callable[[], float] = time.monotonic,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        if (
            not 30 <= ttl_seconds <= 300
            or not 1 <= max_sessions <= 64
            or not 1 <= admissions_per_minute <= 60
        ):
            raise ValueError("invalid Live admission bounds")
        self._ttl = ttl_seconds
        self._max_sessions = max_sessions
        self._rate = admissions_per_minute
        self._monotonic = monotonic
        self._clock = clock
        self._sessions: dict[str, _Session] = {}
        self._window_start = monotonic()
        self._admissions = 0
        self.on_terminal: Callable[[str, PartialReason], None] | None = None

    @staticmethod
    def _digest(session_id: str, expires_at: datetime, capability: str) -> bytes:
        # Bind even the stored verifier to the identity and absolute expiry.
        return hashlib.sha256(
            f"{session_id}\n{expires_at.isoformat()}\n{capability}".encode()
        ).digest()

    def cleanup_expired(self) -> None:
        now = self._monotonic()
        for session_id in list(self._sessions):
            if now >= self._sessions[session_id].deadline:
                self._sessions.pop(session_id).state.stop(PartialReason.TIMEOUT)
                if self.on_terminal:
                    self.on_terminal(session_id, PartialReason.TIMEOUT)

    def create(self) -> IssuedLiveSession:
        self.cleanup_expired()
        now = self._monotonic()
        if now - self._window_start >= 60:
            self._window_start, self._admissions = now, 0
        if self._admissions >= self._rate:
            raise LiveAdmissionRateLimited()
        if len(self._sessions) >= self._max_sessions:
            raise LiveAdmissionFull()
        session_id = uuid4().hex
        capability = secrets.token_urlsafe(32)
        state = LiveIntakeState(session_id, clock=self._clock)
        created_at = state.snapshot().created_at
        expires_at = created_at + timedelta(seconds=self._ttl)
        self._sessions[session_id] = _Session(
            state=state,
            transcript=LiveTranscript(session_id, clock=self._clock),
            expires_at=expires_at,
            deadline=now + self._ttl,
            capability_hash=self._digest(session_id, expires_at, capability),
        )
        self._admissions += 1
        return IssuedLiveSession(session_id, created_at, expires_at, capability)

    def _authorized(self, session_id: str, capability: str) -> _Session:
        self.cleanup_expired()
        session = self._sessions.get(session_id)
        # Bound caller-controlled hashing work even outside the HTTP adapter.
        if session is None or not 1 <= len(capability) <= 128:
            raise LiveSessionUnauthorized()
        if not hmac.compare_digest(
            session.capability_hash,
            self._digest(session_id, session.expires_at, capability),
        ):
            raise LiveSessionUnauthorized()
        return session

    def snapshot(self, session_id: str, capability: str) -> LiveSnapshot:
        return self._authorized(session_id, capability).state.snapshot()

    def transcript(self, session_id: str, capability: str) -> LiveTranscript:
        return self._authorized(session_id, capability).transcript

    def claim_runtime(
        self, session_id: str, capability: str
    ) -> tuple[LiveIntakeState, float]:
        """Internal owner hook, never exposed as a state mutation command."""
        session = self._authorized(session_id, capability)
        if session.offer_started or session.state.snapshot().phase != LivePhase.PENDING:
            raise LiveSessionClaimConflict()
        session.offer_started = True
        return session.state, session.deadline

    def stop(self, session_id: str, capability: str) -> LiveSnapshot:
        session = self._authorized(session_id, capability)
        snapshot = session.state.stop()
        if self.on_terminal:
            self.on_terminal(session_id, PartialReason.STOPPED)
        session.transcript.seal("stopped")
        return snapshot

    def clear(self) -> None:
        for session_id, session in self._sessions.items():
            session.state.stop()
            if self.on_terminal:
                self.on_terminal(session_id, PartialReason.STOPPED)
        self._sessions.clear()
