"""One event-loop owner for a single startup-inclusive, memory-only Live call.

No optional SDK imports occur here. The slot is retained until cleanup finishes;
callback notifications schedule cleanup rather than cancelling their own stack.
"""

import asyncio
import re
import sys
import time
from dataclasses import dataclass, field
from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from app.models.live import CommittedTurn, LivePhase, PartialReason
from app.services.live_audio_draft import AudioDraftController
from app.services.live_sessions import LiveSessionClaimConflict, LiveSessionRegistry
from app.services.live_state import LiveIntakeState
from app.services.live_transcript import LiveTranscript

if TYPE_CHECKING:
    from app.settings import Settings


COMPLETION_FORWARD_TIMEOUT = 5
DRAIN_WAIT_SECONDS = 3
SHUTDOWN_WAIT_SECONDS = 20


class LiveRuntimeConflict(ValueError):
    """Busy, terminal, replayed offer, or wrong peer (never retry this session)."""


class LiveRuntimeUnavailable(ValueError):
    """Explicit Live-only configuration or the supported optional runtime is missing."""


class LiveRuntimeFailure(ValueError):
    """Sanitized signaling/provider startup failure."""


class LiveCleanupPending(ValueError):
    """Cleanup is incomplete; resource ownership remains reserved."""


@dataclass(frozen=True)
class LiveProviderConfig:
    api_key: str = field(repr=False)
    model: str
    stun_url: str | None = field(default=None, repr=False)

    @classmethod
    def from_settings(cls, settings: "Settings") -> "LiveProviderConfig":
        key = settings.LIVE_API_KEY
        model = settings.LIVE_MODEL
        if (
            not settings.LIVE_FREE_TIER_ATTESTED
            or key is None
            or not key.get_secret_value().strip()
            or not isinstance(model, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", model) is None
        ):
            raise LiveRuntimeUnavailable()
        return cls(key.get_secret_value().strip(), model, settings.LIVE_STUN_URL)


@dataclass
class _Call:
    session_id: str
    state: LiveIntakeState
    deadline: float
    transcript: LiveTranscript | None = None
    connection: Any = None  # Optional SDK peer; imported only in the adapter.
    pipeline: Any = None
    adapter: Any = None
    pc_id: str | None = None
    startup: asyncio.Task | None = None
    runner: asyncio.Task | None = None
    timer: asyncio.Task | None = None
    closing: asyncio.Task | None = None
    audio_draft: AudioDraftController | None = None
    preparing: asyncio.Task | None = None
    completion_reserved: bool = False
    completion_forwarded: asyncio.Event = field(default_factory=asyncio.Event)
    completion_ok: bool = False
    completion_sent: bool = False
    candidate_count: int = 0
    patches: set[asyncio.Task] = field(default_factory=set)


def require_live_dependencies() -> None:
    """Tested support policy, not an upstream Python incompatibility claim."""
    if sys.version_info[:2] != (3, 12):
        raise LiveRuntimeUnavailable()
    try:
        if version("pipecat-ai") != "1.12.0" or version("google-genai") != "2.19.0":
            raise LiveRuntimeUnavailable()
    except PackageNotFoundError:
        raise LiveRuntimeUnavailable() from None


class LiveRuntime:
    def __init__(
        self,
        sessions: LiveSessionRegistry,
        *,
        adapter_factory=None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep=asyncio.sleep,
    ):
        self.sessions = sessions
        self._adapter_factory = adapter_factory
        self._monotonic = monotonic
        self._sleep = sleep
        self._active: _Call | None = None
        self._tasks: set[asyncio.Task] = set()
        self._shutting_down = False
        sessions.on_terminal = self._terminated

    def preload(self):
        """Load optional dependencies before readiness, without creating a call."""
        if self._adapter_factory is None:
            require_live_dependencies()
            import_module("app.adapters.pipecat_live.pipeline")

    def _task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _terminated(self, session_id, reason):
        call = self._active
        if call is not None and call.session_id == session_id:
            self._request_close(call, reason)

    def _request_close(self, call, reason):
        self._seal_transcript(call, reason.value)
        call.completion_ok = False
        call.completion_forwarded.set()
        if call.audio_draft is not None:
            call.audio_draft.discard()
        if call.pipeline is not None and hasattr(call.pipeline, "gate_audio"):
            call.pipeline.gate_audio()
        if call.preparing is not None:
            call.preparing.cancel()
        call.state.stop(reason)
        if call.closing is None:
            call.closing = self._task(self._close(call))
        return call.closing

    async def _close(self, call):
        # Never allow SDK errors to escape to request/global exception logging.
        completed = False
        try:
            tasks = [
                t
                for t in (
                    call.timer,
                    call.startup,
                    call.runner,
                    call.preparing,
                    *call.patches,
                )
                if t is not None and t is not asyncio.current_task()
            ]
            for task in tasks:
                task.cancel()
            # A cancelled SDK child must not skip later cleanup steps. Preserve
            # cancellation of this cleanup owner itself (e.g. forced loop exit).
            # Cut media immediately; do not leave it open while the SDK/worker
            # cooperatively drains its cleanup barrier after the hard deadline.
            if call.connection is not None:
                try:
                    await call.connection.disconnect()
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                except Exception:
                    pass
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if call.pipeline is not None:
                try:
                    await call.pipeline.close()
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                except Exception:
                    pass
            if call.connection is not None:
                try:
                    await call.connection.cleanup()  # Public event-callback barrier.
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                except Exception:
                    pass
            completed = True
        finally:
            if call.audio_draft is not None:
                call.audio_draft.result_roundtrip = None
            if completed and self._active is call:
                self._active = None

    async def _expire(self, call):
        await self._sleep(max(0, call.deadline - self._monotonic()))
        self._request_close(call, PartialReason.TIMEOUT)

    def _live(self, call):
        return (
            self._active is call
            and call.closing is None
            and call.state.snapshot().phase == LivePhase.PENDING
            and self._monotonic() < call.deadline
        )

    def _commit(self, call, text):
        # Only the ordered aggregator-final callback is given this closure.
        if not self._live(call) or call.audio_draft.phase != "not_requested":
            return
        try:
            snapshot = call.state.snapshot()
            call.state.commit_turn(
                CommittedTurn(turn_id=snapshot.last_turn_id + 1, text=text),
                expected_revision=snapshot.revision,
            )
        except Exception:
            self._request_close(call, PartialReason.PROVIDER_ERROR)
            return
        self._capture(call, "user", text)

    def _transcript_event(self, call, data):
        try:
            if call.connection is not None:
                call.connection.send_app_message(
                    {
                        "label": "rtvi-ai",
                        "type": "server-message",
                        "id": uuid4().hex,
                        "data": {**data, "version": 1, "sessionId": call.session_id},
                    }
                )
        except Exception:
            pass

    def _transcript_status(self, call):
        journal = call.transcript
        self._transcript_event(
            call,
            {
                "type": "transcript_status",
                "sealed": True,
                "seal_reason": journal.seal_reason,
                "sealed_at": journal.sealed_at.isoformat().replace("+00:00", "Z"),
                "truncated": journal.truncated,
                "input_tail_complete": False,
                "last_captured_sequence": journal.last_sequence,
            },
        )

    def _seal_transcript(self, call, reason):
        try:
            if call.transcript is not None and call.transcript.seal(reason):
                self._transcript_status(call)
        except Exception:
            pass

    def _capture(self, call, speaker, text, interrupted=False):
        if not self._live(call) or call.audio_draft.phase != "not_requested":
            return
        try:
            was_sealed = call.transcript.sealed
            entry = call.transcript.append(speaker, text, interrupted)
            if entry is not None:
                self._transcript_event(
                    call,
                    {
                        "type": "transcript_turn",
                        "entry": entry.model_dump(mode="json"),
                    },
                )
            if not was_sealed and call.transcript.sealed:
                self._transcript_status(call)
        except Exception:
            self._seal_transcript(call, "capture_error")

    def _completion_live(self, call):
        return (
            self._live(call)
            and call.preparing is None
            and call.audio_draft.phase == "not_requested"
            and call.pipeline is not None
            and call.pipeline.ready
        )

    def _reserve_finished(self, call):
        if not self._completion_live(call) or call.completion_reserved:
            return False
        call.completion_reserved = True  # Reserve before any tool-result await.
        return True

    def _completion_forwarded(self, call, success):
        call.completion_ok = bool(success and self._live(call))
        call.completion_forwarded.set()

    def _finished(self, call):
        # Called only after same-session tool-result forwarding. Advisory only.
        if (
            not self._completion_live(call)
            or not call.completion_reserved
            or not call.completion_ok
            or call.completion_sent
        ):
            return False
        call.completion_sent = True
        call.connection.send_app_message(
            {
                "label": "rtvi-ai",
                "type": "server-message",
                "id": uuid4().hex,
                "data": {"type": "interview_finished", "sessionId": call.session_id},
            }
        )
        return True

    async def offer(self, session_id, capability, sdp, config):
        # Authorization and process reservation are synchronous before any await.
        self.sessions.snapshot(session_id, capability)
        if self._adapter_factory is None:
            require_live_dependencies()
        if self._shutting_down or self._active is not None:
            raise LiveRuntimeConflict()
        try:
            state, deadline = self.sessions.claim_runtime(session_id, capability)
        except LiveSessionClaimConflict as exc:
            raise LiveRuntimeConflict() from exc
        call = _Call(session_id, state, min(deadline, self._monotonic() + 300))
        call.transcript = self.sessions.transcript(session_id, capability)
        call.audio_draft = AudioDraftController(monotonic=self._monotonic)
        self._active = call
        call.timer = self._task(self._expire(call))
        call.startup = self._task(self._start(call, sdp, config))
        try:
            return await asyncio.shield(call.startup)
        except asyncio.CancelledError:
            # A cancelled child (deadline/stop/shutdown) is not an HTTP request
            # cancellation. Return the sanitized failure rather than leaking a
            # CancelledError through FastAPI's response boundary.
            caller_cancelled = asyncio.current_task().cancelling() > 0
            await self._wait_signaling_failure(call, PartialReason.DISCONNECTED)
            if caller_cancelled:
                raise
            raise LiveRuntimeFailure() from None
        except Exception:
            reason = (
                PartialReason.TIMEOUT
                if self._monotonic() >= call.deadline
                else PartialReason.PROVIDER_ERROR
            )
            await self._wait_signaling_failure(call, reason)
            raise LiveRuntimeFailure() from None

    async def _wait_signaling_failure(self, call, reason):
        # Terminalize before waiting; a pending drain must not mask signaling failure.
        self._request_close(call, reason)
        try:
            await self.drain_session(call.session_id)
        except LiveCleanupPending:
            pass

    async def _start(self, call, sdp, config):
        if self._adapter_factory is None:
            from app.adapters.pipecat_live.pipeline import LiveAdapter

            adapter = LiveAdapter(stun_url=config.stun_url)
        else:
            adapter = self._adapter_factory()
        if not self._live(call):
            raise LiveRuntimeFailure()
        call.adapter = adapter
        # Own the peer BEFORE initialize; upstream request handler cannot guarantee
        # this on initialization failure and swallows callback exceptions.
        call.connection = adapter.connection()
        for event in ("closed", "disconnected", "failed"):

            @call.connection.event_handler(event)
            async def disconnected(*args):
                self._request_close(call, PartialReason.DISCONNECTED)

        await call.connection.initialize(sdp=sdp, type="offer")
        if not self._live(call):
            raise LiveRuntimeFailure()
        answer = call.connection.get_answer()
        if (
            not isinstance(answer, dict)
            or answer.get("type") != "answer"
            or not isinstance(answer.get("sdp"), str)
            or not answer["sdp"]
            or len(answer["sdp"].encode()) > 65536
            or not isinstance(answer.get("pc_id"), str)
            or not 1 <= len(answer["pc_id"]) <= 128
            or answer["pc_id"] != call.connection.pc_id
        ):
            raise LiveRuntimeFailure()
        call.pc_id = answer["pc_id"]
        snapshot = call.state.snapshot()
        # No tail-drained/extraction claim is made anywhere in this slice.
        call.state.set_input_pending(True, expected_revision=snapshot.revision)
        call.pipeline = adapter.pipeline(
            call.connection,
            config,
            commit=lambda text: self._commit(call, text),
            assistant_commit=lambda text, interrupted: self._capture(
                call, "assistant", text, interrupted
            ),
            fail=lambda: self._request_close(call, PartialReason.PROVIDER_ERROR),
            disconnect=lambda: self._request_close(call, PartialReason.DISCONNECTED),
            audio_draft=call.audio_draft,
            finished=lambda: self._finished(call),
            reserve_finished=lambda: self._reserve_finished(call),
            completion_forwarded=lambda success: self._completion_forwarded(
                call, success
            ),
        )
        call.runner = self._task(self._run(call))
        await asyncio.sleep(0)
        if not self._live(call) or call.runner.done():
            raise LiveRuntimeFailure()
        return {key: answer[key] for key in ("sdp", "type", "pc_id")}

    async def _run(self, call):
        try:
            await call.pipeline.run()
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            if call.closing is None:
                self._request_close(call, PartialReason.PROVIDER_ERROR)

    async def patch(self, session_id, capability, pc_id, candidates):
        self.sessions.snapshot(session_id, capability)
        call = self._active
        if (
            call is None
            or call.session_id != session_id
            or call.pc_id != pc_id
            or not self._live(call)
            or call.audio_draft.phase != "not_requested"
            or call.candidate_count + len(candidates) > 128
        ):
            raise LiveRuntimeConflict()
        call.candidate_count += len(candidates)
        task = self._task(self._patch(call, candidates))
        call.patches.add(task)
        task.add_done_callback(call.patches.discard)
        try:
            await asyncio.shield(task)
            if not self._live(call):
                raise LiveRuntimeFailure()
        except asyncio.CancelledError:
            caller_cancelled = asyncio.current_task().cancelling() > 0
            await self._wait_signaling_failure(call, PartialReason.DISCONNECTED)
            if caller_cancelled:
                raise
            raise LiveRuntimeFailure() from None
        except Exception:
            await self._wait_signaling_failure(call, PartialReason.PROVIDER_ERROR)
            raise LiveRuntimeFailure() from None

    async def _patch(self, call, candidates):
        # Candidate parser is adapter-owned; no caller gets a state command.
        for candidate in candidates:
            if not self._live(call) or call.audio_draft.phase != "not_requested":
                raise LiveRuntimeFailure()
            await call.connection.add_ice_candidate(call.adapter.candidate(candidate))

    def _audio_call(self, session_id, capability):
        self.sessions.snapshot(session_id, capability)
        call = self._active
        if call is None or call.session_id != session_id or not self._live(call):
            raise LiveRuntimeConflict()
        return call

    def audio_draft_view(self, session_id, capability):
        call = self._audio_call(session_id, capability)
        return call.audio_draft.view(session_id)

    async def prepare_audio_draft(self, session_id, capability):
        call = self._audio_call(session_id, capability)
        if (
            call.pipeline is None
            or not call.pipeline.ready
            or call.audio_draft.phase != "not_requested"
            or call.patches
        ):
            raise LiveRuntimeConflict()
        # Single event-loop reservation and both media gates precede any await.
        call.audio_draft.reserve(min(call.deadline, self._monotonic() + 30))
        self._seal_transcript(call, "prepare")
        call.pipeline.gate_audio()

        def reauthorize():
            if self._audio_call(session_id, capability) is not call:
                raise LiveRuntimeFailure()

        async def prepare_once():
            if call.completion_reserved:
                # Ordering only, within this one Prepare; never cancel forwarding.
                async with asyncio.timeout(
                    min(
                        COMPLETION_FORWARD_TIMEOUT,
                        max(0, call.deadline - self._monotonic()),
                    )
                ):
                    await call.completion_forwarded.wait()
                if not call.completion_ok:
                    raise LiveRuntimeFailure()
            reauthorize()
            await call.audio_draft.request(
                call.pipeline.request_audio_draft, reauthorize
            )

        call.preparing = self._task(prepare_once())
        task = call.preparing
        try:
            await asyncio.shield(task)
            reauthorize()
            return call.audio_draft.view(session_id)
        except asyncio.CancelledError:
            caller_cancelled = asyncio.current_task().cancelling() > 0
            self._request_close(call, PartialReason.DISCONNECTED)
            if caller_cancelled:
                raise
            raise LiveRuntimeFailure() from None
        except Exception:
            call.audio_draft.discard()
            self._request_close(call, PartialReason.PROVIDER_ERROR)
            raise LiveRuntimeFailure() from None
        finally:
            # Finished tasks retain exception tracebacks; never retain proposal results.
            if task.done():
                call.preparing = None
            else:
                task.add_done_callback(lambda done: self._clear_preparing(call, done))

    @staticmethod
    def _clear_preparing(call, task):
        if call.preparing is task:
            call.preparing = None

    @property
    def admission_blocked(self):
        return self._shutting_down or (
            self._active is not None and self._active.closing is not None
        )

    @staticmethod
    async def _wait_cleanup(task):
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise
            raise LiveCleanupPending() from None
        except Exception:
            raise LiveCleanupPending() from None

    async def drain_session(self, session_id):
        call = self._active
        if (
            call is not None
            and call.session_id == session_id
            and call.closing is not None
        ):
            try:
                async with asyncio.timeout(DRAIN_WAIT_SECONDS):
                    await self._wait_cleanup(call.closing)
            except TimeoutError:
                raise LiveCleanupPending() from None
            if self._active is call:
                raise LiveCleanupPending()

    async def shutdown(self):
        self._shutting_down = True
        call = self._active
        try:
            async with asyncio.timeout(SHUTDOWN_WAIT_SECONDS):
                if call is not None:
                    await self._wait_cleanup(
                        self._request_close(call, PartialReason.STOPPED)
                    )
                    if self._active is call:
                        raise LiveCleanupPending()
                if self._tasks:
                    await self._wait_cleanup(
                        asyncio.gather(*list(self._tasks), return_exceptions=True)
                    )
        except TimeoutError:
            raise LiveCleanupPending() from None
