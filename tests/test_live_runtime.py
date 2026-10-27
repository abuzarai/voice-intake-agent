"""Offline lifecycle/ownership tests: no SDKs, sockets, files or provider calls."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services.live_runtime import (
    LiveRuntime,
    LiveRuntimeConflict,
    LiveRuntimeFailure,
    LiveRuntimeUnavailable,
)
from app.services.live_sessions import LiveSessionUnauthorized
from tests.support import CONFIG, Adapter, assert_clean, audio_setup, setup

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "runtime_case", ["python311", "python313", "missing", "wrong-version"]
)
async def test_unsupported_python_or_optional_deps_refused_before_reservation_imports(
    runtime_case, monkeypatch
):
    from importlib.metadata import PackageNotFoundError

    import app.services.live_runtime as module

    _, sessions, _, _, first, _ = setup()
    runtime = LiveRuntime(sessions)
    python = (
        (3, 11)
        if runtime_case == "python311"
        else (3, 13)
        if runtime_case == "python313"
        else (3, 12)
    )
    monkeypatch.setattr(module, "sys", SimpleNamespace(version_info=python))
    lookup = Mock(
        side_effect=PackageNotFoundError() if runtime_case == "missing" else None,
        return_value="wrong-version",
    )
    monkeypatch.setattr(module, "version", lookup)
    with pytest.raises(LiveRuntimeUnavailable):
        await runtime.offer(
            first.session_id, first.capability, "fictional-offer", CONFIG
        )
    if runtime_case.startswith("python"):
        lookup.assert_not_called()
    assert runtime._active is None and not runtime._tasks
    assert not sessions._sessions[first.session_id].offer_started
    assert sessions.snapshot(first.session_id, first.capability).phase == "pending"
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_reserve_before_initialize_race_authorization_and_no_replay():
    _, sessions, adapter, runtime, first, second = setup()
    adapter.peer.gate = asyncio.Event()
    with pytest.raises(LiveSessionUnauthorized):
        await runtime.offer(
            first.session_id, second.capability, "fictional-offer", CONFIG
        )
    assert adapter.created == 0 and runtime._active is None
    pending = asyncio.create_task(
        runtime.offer(first.session_id, first.capability, "fictional-offer", CONFIG)
    )
    await asyncio.wait_for(adapter.peer.initialized.wait(), timeout=5)
    for issued in (first, second):
        with pytest.raises(LiveRuntimeConflict):
            await runtime.offer(
                issued.session_id, issued.capability, "fictional-offer", CONFIG
            )
    assert not sessions._sessions[second.session_id].offer_started
    adapter.peer.gate.set()
    assert await pending == adapter.peer.answer
    sessions.stop(first.session_id, first.capability)
    await runtime.drain_session(first.session_id)
    with pytest.raises(LiveRuntimeConflict):
        await runtime.offer(
            first.session_id, first.capability, "fictional-offer", CONFIG
        )
    await assert_clean(runtime, adapter)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    ["initialize", "none", "wrong-peer", "bad-sdp", "callback", "task", "constructor"],
)
async def test_initialization_answer_callback_and_task_failure_cleanup(failure):
    _, sessions, adapter, runtime, first, _ = setup()
    if failure == "initialize":
        adapter.peer.error = RuntimeError("private-init-payload")
    elif failure == "none":
        adapter.peer.answer = None
    elif failure == "wrong-peer":
        adapter.peer.answer["pc_id"] = "other-peer"
    elif failure == "bad-sdp":
        adapter.peer.answer["sdp"] = ""
    elif failure == "callback":
        adapter.error = RuntimeError("private-callback-payload")
    elif failure == "task":
        adapter.pipe.error = RuntimeError("private-task-payload")
    else:

        def failed():
            raise RuntimeError("private-constructor")

        adapter.connection = failed
    with pytest.raises(LiveRuntimeFailure) as error:
        await runtime.offer(
            first.session_id, first.capability, "fictional-offer", CONFIG
        )
    assert "private" not in str(error.value)
    snapshot = sessions.snapshot(first.session_id, first.capability)
    assert snapshot.phase == "partial" and snapshot.partial_reason == "provider_error"
    assert "result" not in snapshot.model_dump()
    if failure == "constructor":
        await runtime.shutdown()
        assert (
            adapter.peer.closed == 0 and runtime._active is None and not runtime._tasks
        )
    else:
        await assert_clean(runtime, adapter)


@pytest.mark.asyncio
async def test_cancel_during_initialize_disconnects_owned_peer_and_tasks():
    _, sessions, adapter, runtime, first, _ = setup()
    adapter.peer.gate = asyncio.Event()
    pending = asyncio.create_task(
        runtime.offer(first.session_id, first.capability, "fictional-offer", CONFIG)
    )
    await asyncio.wait_for(adapter.peer.initialized.wait(), timeout=5)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert (
        sessions.snapshot(first.session_id, first.capability).partial_reason
        == "disconnected"
    )
    await assert_clean(runtime, adapter)


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["closed", "failed", "disconnected"])
async def test_disconnect_is_terminal_partial_and_cleans_tracked_tasks(event):
    _, sessions, adapter, runtime, first, _ = setup()
    await runtime.offer(first.session_id, first.capability, "fictional-offer", CONFIG)
    await adapter.peer.events[event](adapter.peer)
    await runtime.drain_session(first.session_id)
    snapshot = sessions.snapshot(first.session_id, first.capability)
    assert snapshot.partial_reason == "disconnected"
    assert "result" not in snapshot.model_dump()
    await assert_clean(runtime, adapter)


@pytest.mark.asyncio
async def test_ordered_final_commits_only_pending_tail_never_drained_no_extraction():
    _, sessions, adapter, runtime, first, _ = setup()
    await runtime.offer(first.session_id, first.capability, "fictional-offer", CONFIG)
    adapter.pipe.events["commit"]("invented alias")
    adapter.pipe.events["commit"]("invented issue")
    snapshot = sessions.snapshot(first.session_id, first.capability)
    assert [(t.turn_id, t.text) for t in snapshot.turns] == [
        (1, "invented alias"),
        (2, "invented issue"),
    ]
    assert snapshot.input_pending
    assert "tools" not in adapter.pipe.events  # No automatic transcript extraction.
    assert not {"facts", "analysis_proposal", "result"}.intersection(
        snapshot.model_dump()
    )
    adapter.pipe.events["fail"]()
    await runtime.drain_session(first.session_id)
    adapter.pipe.events["commit"]("late callback must not mutate")
    assert sessions.snapshot(first.session_id, first.capability).turns == snapshot.turns
    assert (
        sessions.snapshot(first.session_id, first.capability).partial_reason
        == "provider_error"
    )
    await assert_clean(runtime, adapter)


@pytest.mark.asyncio
async def test_transcript_limit_fails_closed_without_mutating_or_fallback():
    _, sessions, adapter, runtime, first, _ = setup()
    await runtime.offer(first.session_id, first.capability, "fictional-offer", CONFIG)
    adapter.pipe.events["commit"]("x" * 4097)
    await runtime.drain_session(first.session_id)
    snapshot = sessions.snapshot(first.session_id, first.capability)
    assert not snapshot.turns and snapshot.partial_reason == "provider_error"
    await assert_clean(runtime, adapter)


@pytest.mark.asyncio
async def test_patch_exact_session_and_pc_ownership_total_bounds_and_stop():
    _, sessions, adapter, runtime, first, second = setup()
    await runtime.offer(first.session_id, first.capability, "fictional-offer", CONFIG)
    deadline = runtime._active.deadline
    for sid, cap, pc, error in (
        (first.session_id, second.capability, "owned-peer", LiveSessionUnauthorized),
        (second.session_id, second.capability, "owned-peer", LiveRuntimeConflict),
        (first.session_id, first.capability, "wrong-peer", LiveRuntimeConflict),
    ):
        with pytest.raises(error):
            await runtime.patch(sid, cap, pc, [None])
    assert not adapter.peer.candidates
    await runtime.patch(first.session_id, first.capability, "owned-peer", [None] * 128)
    assert runtime._active.deadline == deadline
    with pytest.raises(LiveRuntimeConflict):
        await runtime.patch(first.session_id, first.capability, "owned-peer", [None])
    stopped = sessions.stop(first.session_id, first.capability)
    await runtime.drain_session(first.session_id)
    assert sessions.stop(first.session_id, first.capability) == stopped
    with pytest.raises(LiveRuntimeConflict):
        await runtime.patch(first.session_id, first.capability, "owned-peer", [None])
    await assert_clean(runtime, adapter)


@pytest.mark.asyncio
@pytest.mark.parametrize("expiry", ["timer", "registry", "shutdown", "stop"])
async def test_startup_inclusive_fixed_deadline_expiry_and_shutdown(expiry):
    wake = asyncio.Event()
    delays = []

    async def sleep(delay):
        delays.append(delay)
        await wake.wait()

    clock, sessions, adapter, runtime, first, _ = setup(sleep=sleep)
    clock.seconds = 40
    adapter.peer.gate = asyncio.Event()
    pending = asyncio.create_task(
        runtime.offer(first.session_id, first.capability, "fictional-offer", CONFIG)
    )
    await asyncio.wait_for(adapter.peer.initialized.wait(), timeout=5)
    assert runtime._active.deadline == 300 and delays == [260]
    state = runtime._active.state
    if expiry == "shutdown":
        await runtime.shutdown()
    elif expiry == "stop":
        sessions.stop(first.session_id, first.capability)
    else:
        clock.seconds = 300
        if expiry == "registry":
            with pytest.raises(LiveSessionUnauthorized):
                sessions.snapshot(first.session_id, first.capability)
        else:
            wake.set()
    with pytest.raises(LiveRuntimeFailure):
        await pending
    await assert_clean(runtime, adapter)
    reason = "stopped" if expiry in ("shutdown", "stop") else "timeout"
    assert state.snapshot().partial_reason == reason
    assert "result" not in state.snapshot().model_dump()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled_step", ["disconnect", "pipeline", "callbacks"])
async def test_cancelled_sdk_cleanup_child_does_not_skip_remaining_steps(
    cancelled_step,
):
    _, sessions, adapter, runtime, first, _ = setup()
    await runtime.offer(first.session_id, first.capability, "fictional-offer", CONFIG)
    seen = []
    original_disconnect = adapter.peer.disconnect
    original_close = adapter.pipe.close

    async def disconnect():
        seen.append("disconnect")
        if cancelled_step == "disconnect":
            raise asyncio.CancelledError()
        await original_disconnect()

    async def close():
        seen.append("pipeline")
        if cancelled_step == "pipeline":
            raise asyncio.CancelledError()
        await original_close()

    async def cleanup():
        seen.append("callbacks")
        if cancelled_step == "callbacks":
            raise asyncio.CancelledError()

    adapter.peer.disconnect = disconnect
    adapter.pipe.close = close
    adapter.peer.cleanup = cleanup
    await runtime.shutdown()
    await asyncio.sleep(0)
    assert seen == ["disconnect", "pipeline", "callbacks"]
    assert runtime._active is None and not runtime._tasks
    assert (
        sessions.snapshot(first.session_id, first.capability).partial_reason
        == "stopped"
    )


@pytest.mark.asyncio
async def test_cleanup_exceptions_sanitized_slot_retained_until_cleanup():
    _, sessions, adapter, runtime, first, second = setup()
    await runtime.offer(first.session_id, first.capability, "fictional-offer", CONFIG)
    closing = asyncio.Event()

    async def slow_close():
        await closing.wait()
        raise RuntimeError("private-cleanup")

    adapter.pipe.close = slow_close
    sessions.stop(first.session_id, first.capability)
    await asyncio.sleep(0)
    with pytest.raises(LiveRuntimeConflict):
        await runtime.offer(
            second.session_id, second.capability, "fictional-offer", CONFIG
        )
    assert adapter.peer.closed == 1  # media cut before waiting for SDK cleanup
    closing.set()
    await runtime.drain_session(first.session_id)
    await assert_clean(runtime, adapter)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_patch_failure_or_cancellation_terminates_owned_call(cancel):
    _, sessions, adapter, runtime, first, _ = setup()
    await runtime.offer(first.session_id, first.capability, "fictional-offer", CONFIG)
    entered = asyncio.Event()

    async def broken(candidate):
        entered.set()
        if cancel:
            await asyncio.Event().wait()
        raise RuntimeError("private-candidate")

    adapter.peer.add_ice_candidate = broken
    patch = asyncio.create_task(
        runtime.patch(first.session_id, first.capability, "owned-peer", [None])
    )
    await asyncio.wait_for(entered.wait(), timeout=5)
    if cancel:
        patch.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else LiveRuntimeFailure):
        await patch
    assert sessions.snapshot(first.session_id, first.capability).phase == "partial"
    await assert_clean(runtime, adapter)


@pytest.mark.asyncio
async def test_stop_cancels_and_drains_inflight_candidate_tasks():
    _, sessions, adapter, runtime, first, _ = setup()
    await runtime.offer(first.session_id, first.capability, "fictional-offer", CONFIG)
    entered = asyncio.Event()
    cancelled = []

    async def blocked(candidate):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    adapter.peer.add_ice_candidate = blocked
    patch = asyncio.create_task(
        runtime.patch(first.session_id, first.capability, "owned-peer", [None])
    )
    await asyncio.wait_for(entered.wait(), timeout=5)
    assert len(runtime._active.patches) == 1
    sessions.stop(first.session_id, first.capability)
    await runtime.drain_session(first.session_id)
    with pytest.raises(LiveRuntimeFailure):
        await patch
    assert cancelled == [True]
    await assert_clean(runtime, adapter)


@pytest.mark.asyncio
async def test_registry_clear_closes_call_and_slot_can_serve_new_session_after_cleanup():
    clock, sessions, first_adapter, runtime, first, second = setup()
    await runtime.offer(first.session_id, first.capability, "fictional-offer", CONFIG)
    sessions.stop(first.session_id, first.capability)
    await runtime.drain_session(first.session_id)
    second_adapter = Adapter()
    second_adapter.peer.pc_id = "second-peer"
    second_adapter.peer.answer["pc_id"] = "second-peer"
    runtime._adapter_factory = lambda: second_adapter
    await runtime.offer(second.session_id, second.capability, "fictional-offer", CONFIG)
    with pytest.raises(LiveRuntimeConflict):
        await runtime.patch(first.session_id, first.capability, "second-peer", [None])
    with pytest.raises(LiveRuntimeConflict):
        await runtime.patch(second.session_id, second.capability, "owned-peer", [None])
    sessions.clear()
    await runtime.drain_session(second.session_id)
    assert not sessions._sessions and first_adapter.peer.closed == 1
    await assert_clean(runtime, second_adapter)


@pytest.mark.asyncio
async def test_audio_authenticated_one_attempt_preserves_state_and_deadline_then_deletes():
    clock, sessions, adapter, runtime, first, second, call = await audio_setup()
    before = call.state.snapshot()
    deadline = call.deadline
    with pytest.raises(LiveSessionUnauthorized):
        await runtime.prepare_audio_draft(first.session_id, second.capability)
    with pytest.raises(LiveRuntimeConflict):
        await runtime.prepare_audio_draft(second.session_id, second.capability)
    assert call.audio_draft.phase == "not_requested" and not adapter.pipe.gated
    adapter.pipe.ready = False
    with pytest.raises(LiveRuntimeConflict):
        await runtime.prepare_audio_draft(first.session_id, first.capability)
    adapter.pipe.ready = True
    view = await runtime.prepare_audio_draft(first.session_id, first.capability)
    assert view.phase == "proposed" and view.proposal.confirmed is False
    assert view.proposal.input_tail_complete is False and call.preparing is None
    assert call.deadline == deadline
    assert runtime.audio_draft_view(first.session_id, first.capability) == view
    adapter.pipe.events["commit"]("late aggregation")
    assert call.state.snapshot() == before
    with pytest.raises(LiveRuntimeConflict):
        await runtime.prepare_audio_draft(first.session_id, first.capability)
    with pytest.raises(LiveRuntimeConflict):
        await runtime.patch(first.session_id, first.capability, "owned-peer", [None])
    sessions.stop(first.session_id, first.capability)
    assert (
        call.audio_draft.view(first.session_id).proposal is None
    )  # synchronous deletion
    await runtime.drain_session(first.session_id)
    with pytest.raises(LiveRuntimeConflict):
        runtime.audio_draft_view(first.session_id, first.capability)
    await assert_clean(runtime, adapter)


@pytest.mark.asyncio
@pytest.mark.parametrize("barrier", ["send", "callback", "context", "settled"])
@pytest.mark.parametrize(
    "event", ["stop", "disconnect", "failure", "expiry", "shutdown", "cancel"]
)
async def test_audio_terminal_races_at_every_settlement_barrier(barrier, event):
    from tests.support import params

    clock, sessions, adapter, runtime, first, second, call = await audio_setup()
    entered, release = asyncio.Event(), asyncio.Event()

    async def block():
        entered.set()
        await release.wait()

    async def context():
        if barrier == "context":
            await block()

    async def callback(result, *, properties):
        if barrier == "callback":
            await block()
        await properties.on_context_updated()

    async def send():
        assert adapter.pipe.gated
        if barrier == "send":
            await block()
        await call.audio_draft.handle_tool(
            params(callback), properties_factory=SimpleNamespace
        )
        if barrier == "settled":
            await block()

    call.audio_draft.result_roundtrip = context
    adapter.pipe.request_audio_draft = send
    pending = asyncio.create_task(
        runtime.prepare_audio_draft(first.session_id, first.capability)
    )
    await asyncio.wait_for(entered.wait(), timeout=5)
    assert call.audio_draft.phase == "requested" and adapter.pipe.gated
    with pytest.raises(LiveRuntimeConflict):
        await runtime.prepare_audio_draft(first.session_id, first.capability)
    adapter.pipe.events["commit"]("late")
    assert not call.state.snapshot().turns
    if event == "stop":
        sessions.stop(first.session_id, first.capability)
    elif event == "disconnect":
        await adapter.peer.events["disconnected"]()
    elif event == "failure":
        adapter.pipe.events["fail"]()
    elif event == "expiry":
        clock.seconds = 300
        with pytest.raises(LiveSessionUnauthorized):
            sessions.snapshot(first.session_id, first.capability)
    elif event == "shutdown":
        await runtime.shutdown()
    else:
        pending.cancel()
    with pytest.raises(
        asyncio.CancelledError if event == "cancel" else LiveRuntimeFailure
    ):
        await pending
    assert call.audio_draft._pending is None
    assert call.audio_draft.view(first.session_id).proposal is None
    release.set()
    await assert_clean(runtime, adapter)
    assert call.preparing is None


@pytest.mark.asyncio
async def test_audio_publication_equality_deadline_revalidates_without_timer_wake():
    clock, sessions, adapter, runtime, first, second, call = await audio_setup()
    original = adapter.pipe.request_audio_draft
    clock.seconds = 280

    async def send():
        await original()
        clock.seconds = 300

    adapter.pipe.request_audio_draft = send
    with pytest.raises(LiveRuntimeFailure):
        await runtime.prepare_audio_draft(first.session_id, first.capability)
    assert call.audio_draft._deadline == 300  # not a fresh five-minute budget
    assert call.audio_draft._proposal is None
    await assert_clean(runtime, adapter)


@pytest.mark.asyncio
async def test_audio_thirty_second_budget_and_cleanup_retains_slot():
    clock, sessions, adapter, runtime, first, second, call = await audio_setup()
    entered, release = asyncio.Event(), asyncio.Event()

    async def broken_send():
        assert call.audio_draft._deadline == 30
        raise ValueError("private-output")

    async def cleanup():
        entered.set()
        await release.wait()

    adapter.pipe.request_audio_draft = broken_send
    adapter.pipe.close = cleanup
    with pytest.raises(LiveRuntimeFailure):
        await runtime.prepare_audio_draft(first.session_id, first.capability)
    await asyncio.wait_for(entered.wait(), timeout=5)
    assert runtime._active is call and call.preparing is None
    with pytest.raises(LiveRuntimeConflict):
        await runtime.offer(
            second.session_id, second.capability, "fictional-offer", CONFIG
        )
    release.set()
    await assert_clean(runtime, adapter)


@pytest.mark.asyncio
async def test_completion_is_one_shot_owned_advisory_and_does_not_prepare():
    _, sessions, adapter, runtime, first, _ = setup()
    await runtime.offer(first.session_id, first.capability, "fictional-offer", CONFIG)
    call = runtime._active
    adapter.pipe.ready = True
    adapter.peer.send_app_message = Mock()
    finish = adapter.pipe.events["finished"]
    assert finish() is False
    assert adapter.pipe.events["reserve_finished"]() is True
    assert adapter.pipe.events["reserve_finished"]() is False
    adapter.peer.send_app_message.assert_not_called()
    adapter.pipe.events["completion_forwarded"](True)
    assert finish() is True
    assert finish() is False
    envelope = adapter.peer.send_app_message.call_args.args[0]
    assert envelope["label"] == "rtvi-ai"
    assert envelope["type"] == "server-message"
    assert envelope["data"] == {
        "type": "interview_finished",
        "sessionId": first.session_id,
    }
    assert first.capability not in str(envelope)
    assert call.audio_draft.phase == "not_requested"
    assert call.preparing is None
    sessions.stop(first.session_id, first.capability)
    await runtime.drain_session(first.session_id)
    assert finish() is False
    completion_events = [
        item.args[0]
        for item in adapter.peer.send_app_message.call_args_list
        if item.args[0]["data"]["type"] == "interview_finished"
    ]
    assert completion_events == [envelope]
    assert (
        adapter.peer.send_app_message.call_args.args[0]["data"]["type"]
        == "transcript_status"
    )
    await assert_clean(runtime, adapter)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "blocked", ["not-ready", "expired", "preparing", "extracting", "wrong-owner"]
)
async def test_completion_suppressed_outside_current_live_conversation(blocked):
    clock, sessions, adapter, runtime, first, _ = setup()
    await runtime.offer(first.session_id, first.capability, "fictional-offer", CONFIG)
    call = runtime._active
    adapter.pipe.ready = blocked != "not-ready"
    adapter.peer.send_app_message = Mock()
    if blocked == "expired":
        call.deadline = clock.monotonic()
    elif blocked == "preparing":
        call.preparing = asyncio.create_task(asyncio.sleep(100))
    elif blocked == "extracting":
        call.audio_draft.reserve(call.deadline)
    elif blocked == "wrong-owner":
        runtime._active = None
    assert adapter.pipe.events["reserve_finished"]() is False
    assert adapter.pipe.events["finished"]() is False
    assert not call.completion_sent
    adapter.peer.send_app_message.assert_not_called()
    runtime._active = call
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_stop_between_completion_reservation_and_forwarding_suppresses_event():
    _, sessions, adapter, runtime, first, _ = setup()
    await runtime.offer(first.session_id, first.capability, "fictional-offer", CONFIG)
    adapter.pipe.ready = True
    adapter.peer.send_app_message = Mock()
    assert adapter.pipe.events["reserve_finished"]() is True
    sessions.stop(first.session_id, first.capability)
    assert adapter.pipe.events["finished"]() is False
    events = [
        item.args[0]["data"] for item in adapter.peer.send_app_message.call_args_list
    ]
    assert len(events) == 1 and events[0]["type"] == "transcript_status"
    assert events[0]["seal_reason"] == "stopped"
    assert all(event["type"] != "interview_finished" for event in events)
    await runtime.drain_session(first.session_id)
    await assert_clean(runtime, adapter)


@pytest.mark.asyncio
async def test_manual_end_waits_for_reserved_completion_before_one_extraction():
    _, sessions, adapter, runtime, first, _, call = await audio_setup()
    assert adapter.pipe.events["reserve_finished"]()
    extracted = []
    original = adapter.pipe.request_audio_draft

    async def extract():
        assert call.completion_ok
        extracted.append(True)
        await original()

    adapter.pipe.request_audio_draft = extract
    preparing = asyncio.create_task(
        runtime.prepare_audio_draft(first.session_id, first.capability)
    )
    await asyncio.sleep(0)
    assert adapter.pipe.gated and call.preparing is not None
    assert not extracted
    with pytest.raises(LiveRuntimeConflict):
        await runtime.prepare_audio_draft(first.session_id, first.capability)
    adapter.pipe.events["completion_forwarded"](True)
    # Manual End owns preparation; late advisory delivery is now inert.
    assert adapter.pipe.events["finished"]() is False
    assert (await preparing).phase == "proposed"
    assert extracted == [True]
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_prepare_first_blocks_completion_reservation_before_extraction():
    _, _, adapter, runtime, first, _, _ = await audio_setup()
    original = adapter.pipe.request_audio_draft
    extracted = []

    async def extract():
        assert adapter.pipe.events["reserve_finished"]() is False
        assert adapter.pipe.events["finished"]() is False
        extracted.append(True)
        await original()

    adapter.pipe.request_audio_draft = extract
    assert (
        await runtime.prepare_audio_draft(first.session_id, first.capability)
    ).phase == "proposed"
    assert extracted == [True]
    await runtime.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["failure", "timeout", "stop", "deadline", "cancel"])
async def test_completion_barrier_teardown_never_extracts_late(ending, monkeypatch):
    import app.services.live_runtime as module

    monkeypatch.setattr(module, "COMPLETION_FORWARD_TIMEOUT", 0.01)
    clock, sessions, adapter, runtime, first, _, call = await audio_setup()
    assert adapter.pipe.events["reserve_finished"]()
    extracted = Mock()
    adapter.pipe.request_audio_draft = extracted
    release_forwarding = asyncio.Event()

    async def shared_forwarding():
        await release_forwarding.wait()
        adapter.pipe.events["completion_forwarded"](True)

    # Independent forwarding owner: cancellation of the barrier never propagates
    # into this task. Real runner-owned forwarding is cancelled by call teardown.
    forwarding = asyncio.create_task(shared_forwarding())
    preparing = asyncio.create_task(
        runtime.prepare_audio_draft(first.session_id, first.capability)
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    if ending == "failure":
        adapter.pipe.events["completion_forwarded"](False)
    elif ending == "stop":
        sessions.stop(first.session_id, first.capability)
    elif ending == "deadline":
        call.deadline = clock.monotonic()
        adapter.pipe.events["completion_forwarded"](True)
    elif ending == "cancel":
        preparing.cancel()
    async with asyncio.timeout(1):
        with pytest.raises(
            asyncio.CancelledError if ending == "cancel" else LiveRuntimeFailure
        ):
            await preparing
        await runtime.shutdown()
    assert call.completion_forwarded.is_set()
    # A late provider acknowledgement cannot revive the discarded Prepare.
    assert not forwarding.done()
    release_forwarding.set()
    await forwarding
    assert adapter.pipe.events["finished"]() is False
    extracted.assert_not_called()
    assert runtime._active is None and not runtime._tasks
