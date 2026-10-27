"""Bounded waits retain ownership of synthetic stalled cleanup."""

import asyncio

import pytest

import app.services.live_runtime as module
from app.composition import LiveServices
from app.services.live_runtime import LiveCleanupPending, LiveRuntimeConflict
from tests.support import CONFIG, request, setup

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["disconnect", "runner", "pipeline", "callback"])
async def test_stalled_cleanup_retains_slot_until_barrier_released(monkeypatch, stage):
    monkeypatch.setattr(module, "DRAIN_WAIT_SECONDS", 0.01)
    _, sessions, adapter, runtime, first, second = setup()
    entered, release = asyncio.Event(), asyncio.Event()
    gated = []
    adapter.pipe.gate_audio = lambda: gated.append(True)

    async def barrier():
        entered.set()
        await release.wait()

    if stage == "runner":

        async def runner():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await barrier()

        adapter.pipe.run = runner
    elif stage == "pipeline":
        adapter.pipe.close = barrier
    elif stage == "callback":
        adapter.peer.cleanup = barrier
    else:
        adapter.peer.disconnect = barrier
    await runtime.offer(first.session_id, first.capability, "fictional-offer", CONFIG)
    await asyncio.sleep(0)
    sessions.stop(first.session_id, first.capability)
    owner = runtime._active.closing
    await asyncio.wait_for(entered.wait(), timeout=5)
    results = await asyncio.gather(
        runtime.drain_session(first.session_id),
        runtime.drain_session(first.session_id),
        return_exceptions=True,
    )
    assert all(isinstance(result, LiveCleanupPending) for result in results)
    assert gated and runtime.admission_blocked and not owner.done()
    assert runtime._active.closing is owner
    with pytest.raises(LiveRuntimeConflict):
        await runtime.offer(
            second.session_id, second.capability, "fictional-offer", CONFIG
        )
    waiter = asyncio.create_task(runtime.drain_session(first.session_id))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not owner.done()
    release.set()
    await owner
    assert runtime._active is None and not runtime.admission_blocked
    assert adapter.created == 1
    await runtime.shutdown()


@pytest.mark.asyncio
async def test_cancelled_owner_quarantines_slot(monkeypatch):
    _, sessions, adapter, runtime, first, _ = setup()
    entered = asyncio.Event()

    async def blocked():
        entered.set()
        await asyncio.Event().wait()

    adapter.peer.disconnect = blocked
    await runtime.offer(first.session_id, first.capability, "fictional-offer", CONFIG)
    sessions.stop(first.session_id, first.capability)
    await asyncio.wait_for(entered.wait(), timeout=5)
    call = runtime._active
    call.closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call.closing
    with pytest.raises(LiveCleanupPending):
        await runtime.drain_session(first.session_id)
    with pytest.raises(LiveCleanupPending):
        await runtime.shutdown()
    assert runtime._active is call and runtime.admission_blocked


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_caller", [False, True])
async def test_shutdown_is_bounded_shielded_and_registry_clear_keeps_slot(
    monkeypatch, cancel_caller
):
    monkeypatch.setattr(module, "SHUTDOWN_WAIT_SECONDS", 0.01)
    _, sessions, adapter, runtime, first, _ = setup()
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked():
        entered.set()
        await release.wait()

    adapter.peer.cleanup = blocked
    await runtime.offer(first.session_id, first.capability, "fictional-offer", CONFIG)
    services = LiveServices(None, sessions, runtime)
    shutdown = asyncio.create_task(services.shutdown())
    await asyncio.wait_for(entered.wait(), timeout=5)
    owner = runtime._active.closing
    if cancel_caller:
        shutdown.cancel()
        with pytest.raises(asyncio.CancelledError):
            await shutdown
    else:
        with pytest.raises(LiveCleanupPending):
            await shutdown
    assert not sessions._sessions
    assert runtime.admission_blocked and not owner.done()
    release.set()
    await owner
    assert runtime._active is None and runtime.admission_blocked


@pytest.mark.asyncio
async def test_http_pending_cleanup_and_admission_no_allocation(signaling, monkeypatch):
    from tests.support import bearer

    monkeypatch.setattr(module, "DRAIN_WAIT_SECONDS", 0.01)
    api, adapter, runtime = signaling
    from app.application import create_app

    _, sessions, adapter, runtime, first, _ = setup()
    app = create_app(services=LiveServices(api[1], sessions, runtime))
    issued = {"session_id": first.session_id, "capability": first.capability}
    await runtime.offer(first.session_id, first.capability, "fictional-offer", CONFIG)
    release = asyncio.Event()
    adapter.peer.disconnect = release.wait

    async def receive():
        return {"type": "http.request", "body": b'{"processing_consent":true}'}

    base = "/api/v1/live/sessions/" + issued["session_id"]
    try:
        responses = await asyncio.gather(
            request(app, base + "/stop", bearer(issued), receive),
            request(app, base + "/state", bearer(issued), receive, "GET"),
        )
        for status, headers, body in responses:
            assert status == 503
            assert headers[b"cache-control"] == b"no-store"
            assert set(body["detail"]) == {"message_en", "message_ur"}
        from unittest.mock import Mock

        create = Mock(wraps=sessions.create)
        monkeypatch.setattr(sessions, "create", create)
        before = len(sessions._sessions)
        status, _, _ = await request(
            app,
            "/api/v1/live/sessions",
            {
                "Content-Type": "application/json",
                "X-Live-Bootstrap-Secret": api[
                    1
                ].LIVE_BOOTSTRAP_SECRET.get_secret_value(),
            },
            receive,
        )
        assert status == 503 and len(sessions._sessions) == before
        create.assert_not_called()
    finally:
        release.set()
        await runtime._active.closing


@pytest.mark.asyncio
async def test_shutdown_one_budget_covers_remaining_tasks(monkeypatch):
    monkeypatch.setattr(module, "SHUTDOWN_WAIT_SECONDS", 0.01)
    _, _, _, runtime, first, _ = setup()
    await runtime.offer(first.session_id, first.capability, "fictional-offer", CONFIG)
    release = asyncio.Event()
    work = runtime._task(release.wait())
    budgets = []
    timeout = asyncio.timeout

    def observed_timeout(seconds):
        budgets.append(seconds)
        return timeout(seconds)

    monkeypatch.setattr(module.asyncio, "timeout", observed_timeout)
    with pytest.raises(LiveCleanupPending):
        await runtime.shutdown()
    assert budgets == [0.01] and not work.done()
    release.set()
    await work
    await runtime.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["offer", "patch", "stop", "deadline"])
@pytest.mark.parametrize("stage", ["disconnect", "callback"])
async def test_signaling_failure_wait_is_bounded(monkeypatch, operation, stage):
    from app.services.live_runtime import LiveRuntimeFailure

    monkeypatch.setattr(module, "DRAIN_WAIT_SECONDS", 0.01)
    _, sessions, adapter, runtime, first, second = setup()
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked():
        entered.set()
        await release.wait()

    if stage == "disconnect":
        adapter.peer.disconnect = blocked
    else:
        adapter.peer.cleanup = blocked
    if operation == "patch":
        await runtime.offer(
            first.session_id, first.capability, "fictional-offer", CONFIG
        )

        async def fail(candidate):
            raise ValueError("private candidate failure")

        adapter.peer.add_ice_candidate = fail
        response = asyncio.create_task(
            runtime.patch(first.session_id, first.capability, adapter.peer.pc_id, [{}])
        )
    else:
        if operation == "offer":
            adapter.peer.error = ValueError("private initialization failure")
        else:
            adapter.peer.gate = asyncio.Event()
        response = asyncio.create_task(
            runtime.offer(first.session_id, first.capability, "fictional-offer", CONFIG)
        )
        await asyncio.wait_for(adapter.peer.initialized.wait(), timeout=5)
        if operation == "stop":
            sessions.stop(first.session_id, first.capability)
        elif operation == "deadline":
            runtime._active.deadline = 0
            runtime._request_close(runtime._active, module.PartialReason.TIMEOUT)
    try:
        await asyncio.wait_for(entered.wait(), 0.5)
        owner = runtime._active.closing
        with pytest.raises(LiveRuntimeFailure):
            await asyncio.wait_for(asyncio.shield(response), 0.2)
        assert runtime.admission_blocked and not owner.done()
        assert runtime._active.closing is owner
        with pytest.raises(LiveRuntimeConflict):
            await runtime.offer(
                second.session_id, second.capability, "fictional-offer", CONFIG
            )
        assert adapter.created == 1
    finally:
        release.set()
        await asyncio.gather(response, return_exceptions=True)
        await runtime.shutdown()
    assert runtime._active is None and not runtime._tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["offer", "patch"])
@pytest.mark.parametrize("during_failure_wait", [False, True])
async def test_signaling_caller_cancellation_retains_cleanup(
    monkeypatch, operation, during_failure_wait
):
    monkeypatch.setattr(module, "DRAIN_WAIT_SECONDS", 0.01)
    _, _, adapter, runtime, first, _ = setup()
    entered, release, work = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def blocked():
        entered.set()
        await release.wait()

    async def candidate(candidate):
        work.set()
        if during_failure_wait:
            raise ValueError("private failure")
        await asyncio.Event().wait()

    adapter.peer.disconnect = blocked
    if operation == "patch":
        await runtime.offer(
            first.session_id, first.capability, "fictional-offer", CONFIG
        )
        adapter.peer.add_ice_candidate = candidate
        response = asyncio.create_task(
            runtime.patch(first.session_id, first.capability, adapter.peer.pc_id, [{}])
        )
        await work.wait()
    else:
        if during_failure_wait:
            adapter.peer.error = ValueError("private failure")
        else:
            adapter.peer.gate = asyncio.Event()
        response = asyncio.create_task(
            runtime.offer(first.session_id, first.capability, "fictional-offer", CONFIG)
        )
        await asyncio.wait_for(adapter.peer.initialized.wait(), timeout=5)
    try:
        if during_failure_wait:
            await asyncio.wait_for(entered.wait(), 0.5)
        response.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(response), 0.2)
        assert runtime.admission_blocked and not runtime._active.closing.done()
    finally:
        release.set()
        await asyncio.gather(response, return_exceptions=True)
        await runtime.shutdown()
    assert not runtime._tasks


@pytest.mark.asyncio
async def test_concurrent_patch_failures_share_cleanup(monkeypatch):
    from app.services.live_runtime import LiveRuntimeFailure

    monkeypatch.setattr(module, "DRAIN_WAIT_SECONDS", 0.01)
    _, _, adapter, runtime, first, _ = setup()
    release, fail = asyncio.Event(), asyncio.Event()
    started = 0
    both = asyncio.Event()

    async def candidate(candidate):
        nonlocal started
        started += 1
        if started == 2:
            both.set()
        await fail.wait()
        raise ValueError("private failure")

    adapter.peer.disconnect = release.wait
    await runtime.offer(first.session_id, first.capability, "fictional-offer", CONFIG)
    adapter.peer.add_ice_candidate = candidate
    responses = [
        asyncio.create_task(
            runtime.patch(first.session_id, first.capability, adapter.peer.pc_id, [{}])
        )
        for _ in range(2)
    ]
    try:
        await asyncio.wait_for(both.wait(), 0.5)
        fail.set()
        results = await asyncio.wait_for(
            asyncio.gather(*responses, return_exceptions=True), 0.2
        )
        assert all(isinstance(result, LiveRuntimeFailure) for result in results)
        owner = runtime._active.closing
        assert not owner.done() and runtime.admission_blocked
        assert adapter.created == 1
    finally:
        fail.set()
        release.set()
        await asyncio.gather(*responses, return_exceptions=True)
        await runtime.shutdown()
    assert runtime._active is None and not runtime._tasks


@pytest.mark.asyncio
async def test_http_signaling_failure_stalled_cleanup_remains_502(
    signaling, monkeypatch
):
    from app.application import create_app
    from tests.support import bearer

    monkeypatch.setattr(module, "DRAIN_WAIT_SECONDS", 0.01)
    api, _, _ = signaling
    _, sessions, adapter, runtime, first, _ = setup()
    app = create_app(services=LiveServices(api[1], sessions, runtime))
    release = asyncio.Event()
    adapter.peer.disconnect = release.wait
    adapter.peer.error = ValueError("private initialization failure")

    async def receive():
        return {
            "type": "http.request",
            "body": b'{"sdp":"fictional-offer","type":"offer"}',
        }

    try:
        status, headers, body = await asyncio.wait_for(
            request(
                app,
                "/api/v1/live/sessions/" + first.session_id + "/webrtc/offer",
                {
                    **bearer({"capability": first.capability}),
                    "Content-Type": "application/json",
                },
                receive,
            ),
            0.5,
        )
        assert status == 502 and headers[b"cache-control"] == b"no-store"
        assert body["detail"]["message_en"] == "Live call failed"
        assert "private" not in str(body)
        assert runtime.admission_blocked and not runtime._active.closing.done()
    finally:
        release.set()
        await runtime.shutdown()
