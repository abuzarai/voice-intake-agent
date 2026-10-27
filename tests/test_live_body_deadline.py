"""Body deadlines exercised through ASGI without a network or provider."""

import asyncio

import pytest

from app.api import live
from tests.support import bearer, bootstrap, offer, offer_url, request

pytestmark = pytest.mark.integration


@pytest.fixture
def deadline(monkeypatch):
    monkeypatch.setattr(live, "JSON_BODY_READ_TIMEOUT_SECONDS", 0.1)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["first", "later", "drip"])
async def test_bootstrap_total_deadline_no_admission(api, deadline, mode):
    _, config, sessions, _, client = api
    calls = 0

    async def receive():
        nonlocal calls
        calls += 1
        if mode == "later" and calls == 1:
            return {"type": "http.request", "body": b"{", "more_body": True}
        if mode == "drip":
            # Multiple receives do not depend on two sleeps fitting a deadline.
            if calls > 1:
                await asyncio.sleep(0.02)
            return {"type": "http.request", "body": b" ", "more_body": True}
        await asyncio.sleep(1)
        raise AssertionError("deadline did not interrupt receive")

    # A per-chunk deadline regression must fail, not stream forever.
    async with asyncio.timeout(3):
        result = await request(
            client.app,
            "/api/v1/live/sessions",
            {
                "Content-Type": "application/json",
                "X-Live-Bootstrap-Secret": config.LIVE_BOOTSTRAP_SECRET.get_secret_value(),
            },
            receive,
        )
    assert result[0] == 408
    assert result[1][b"cache-control"] == b"no-store"
    assert result[2] == {
        "detail": {
            "message_en": "Live request body timed out",
            "message_ur": "لائیو درخواست کا وقت ختم ہو گیا",
        }
    }
    if mode == "drip":
        assert calls > 1
    # All admission quota remains available after the failed read.
    for _ in range(6):
        sessions.create()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["offer", "patch", "prepare"])
async def test_timeout_preserves_call_and_operation_state(
    signaling, deadline, operation
):
    api, adapter, runtime = signaling
    client = api[-1]
    issued = bootstrap(api).json()
    if operation != "offer":
        assert offer(client, issued).status_code == 200
    before = api[2].snapshot(issued["session_id"], issued["capability"])
    active = runtime._active
    created = adapter.created
    path = (
        offer_url(issued)
        if operation != "prepare"
        else ("/api/v1/live/sessions/" + issued["session_id"] + "/audio-draft")
    )

    async def receive():
        await asyncio.sleep(1)
        raise AssertionError("deadline missing")

    result = await request(
        client.app,
        path,
        {**bearer(issued), "Content-Type": "application/json"},
        receive,
        "PATCH" if operation == "patch" else "POST",
    )
    assert result[0] == 408
    assert runtime._active is active
    assert adapter.created == created
    assert not adapter.peer.candidates and adapter.peer.closed == 0
    assert api[2].snapshot(issued["session_id"], issued["capability"]) == before
    if operation == "offer":
        assert offer(client, issued).status_code == 200
    elif operation == "prepare":
        assert (
            runtime.audio_draft_view(issued["session_id"], issued["capability"]).phase
            == "not_requested"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,expected", [("auth", 401), ("gate", 404), ("type", 415)])
async def test_rejection_does_not_read(api, deadline, kind, expected):
    _, config, _, _, client = api
    headers = {
        "X-Live-Bootstrap-Secret": config.LIVE_BOOTSTRAP_SECRET.get_secret_value()
    }
    if kind == "auth":
        headers = {}
    if kind == "gate":
        config.LIVE_ENABLED = False

    async def receive():
        pytest.fail("rejected request consumed body")

    assert (await request(client.app, "/api/v1/live/sessions", headers, receive))[
        0
    ] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [TimeoutError("private-source"), asyncio.CancelledError()]
)
async def test_source_timeout_is_not_deadline_and_cancellation_propagates(
    api, deadline, error
):
    _, config, _, _, client = api

    async def receive():
        raise error

    call = request(
        client.app,
        "/api/v1/live/sessions",
        {
            "Content-Type": "application/json",
            "X-Live-Bootstrap-Secret": config.LIVE_BOOTSTRAP_SECRET.get_secret_value(),
        },
        receive,
    )
    if isinstance(error, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await call
    else:
        result = await call
        assert result[0] == 500 and "private-source" not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,status",
    [(b'{"processing_consent":true}', 201), (b"bad", 422), (b" " * 257, 413)],
)
async def test_completed_body_preserves_status(api, deadline, body, status):
    _, config, _, _, client = api

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    result = await request(
        client.app,
        "/api/v1/live/sessions",
        {
            "Content-Type": "application/json",
            "X-Live-Bootstrap-Secret": config.LIVE_BOOTSTRAP_SECRET.get_secret_value(),
        },
        receive,
    )
    assert result[0] == status


@pytest.mark.asyncio
async def test_disconnect_is_not_deadline(api, deadline):
    _, config, _, _, client = api

    async def receive():
        return {"type": "http.disconnect"}

    result = await request(
        client.app,
        "/api/v1/live/sessions",
        {
            "Content-Type": "application/json",
            "X-Live-Bootstrap-Secret": config.LIVE_BOOTSTRAP_SECRET.get_secret_value(),
        },
        receive,
    )
    # Preserve the existing sensitive-route sanitization of ClientDisconnect.
    assert result[0] == 500
