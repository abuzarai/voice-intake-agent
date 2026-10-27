"""Offline bounded journal, HTTP replay and runtime isolation contracts."""

import asyncio
import json
from unittest.mock import Mock

import pytest

from app.services.live_transcript import LiveTranscript, encode_batch
from tests.support import CONFIG, Clock, bootstrap, setup


def journal():
    return LiveTranscript("fictional-session", clock=Clock().utc)


@pytest.mark.parametrize("text", ["اردو English 😀", "\x00" * 4096, "😀" * 4096])
@pytest.mark.unit
def test_exact_encoded_batches_immutable_replay(text):
    log = journal()
    for _ in range(17):
        log.append("assistant", text, True)
    first = log.read(0)
    assert len(encode_batch(first)) <= 32768
    assert first.next_sequence == len(first.entries) <= 16
    assert first.has_more
    assert encode_batch(first) == encode_batch(log.read(0))
    with pytest.raises(ValueError):
        first.entries[0].text = "changed"
    cursor = first.next_sequence
    while cursor < log.last_sequence:
        batch = log.read(cursor)
        assert len(encode_batch(batch)) <= 32768
        assert batch.next_sequence > cursor
        cursor = batch.next_sequence
    assert not log.read(cursor).has_more
    assert log.read(cursor).entries == ()


@pytest.mark.unit
def test_literal_empty_wire_contract():
    assert json.loads(encode_batch(journal().read(0))) == {
        "version": 1,
        "session_id": "fictional-session",
        "after_sequence": 0,
        "entries": [],
        "next_sequence": 0,
        "last_captured_sequence": 0,
        "earliest_available_sequence": 1,
        "sealed": False,
        "seal_reason": None,
        "sealed_at": None,
        "truncated": False,
        "input_tail_complete": False,
        "has_more": False,
    }


@pytest.mark.parametrize(
    "text,reason", [("x" * 4097, "limit"), ("\ud800", "capture_error")]
)
@pytest.mark.unit
def test_rejected_entry_seals_without_sequence(text, reason):
    log = journal()
    log.append("user", "accepted")
    assert log.append("assistant", text) is None
    assert log.last_sequence == 1 and log.truncated and log.seal_reason == reason
    timestamp = log.sealed_at
    assert not log.seal("stopped") and log.sealed_at == timestamp
    assert log.append("user", "late") is None


@pytest.mark.parametrize("mode", ["count", "bytes"])
@pytest.mark.unit
def test_capacity(mode):
    log = journal()
    for _ in range(257):
        log.append("user", "x" if mode == "count" else "😀" * 4096)
    assert log.last_sequence == (256 if mode == "count" else 16)
    assert log.seal_reason == "limit" and log.truncated


@pytest.mark.parametrize("cursor", [True, -1, 257, 1.5, "0", 10**100])
@pytest.mark.unit
def test_strict_cursor(cursor):
    with pytest.raises(ValueError):
        journal().read(cursor)


@pytest.mark.parametrize(
    "body",
    [
        {"after_sequence": True},
        {"after_sequence": 1},
        {"after_sequence": 0, "extra": {}},
        {"after_sequence": {}},
        {},
    ],
)
@pytest.mark.integration
def test_sanitized_http_cursor(api, body):
    *_, client = api
    issued = bootstrap(api).json()
    response = client.post(
        f"/api/v1/live/sessions/{issued['session_id']}/transcript/read",
        headers={"Authorization": f"Bearer {issued['capability']}"},
        json=body,
    )
    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert set(response.json()["detail"]) == {"message_en", "message_ur"}


@pytest.mark.integration
def test_http_stopped_replay_and_expiry(api, monkeypatch):
    services, _, sessions, clock, client = api
    issued = bootstrap(api).json()
    sid, cap = issued["session_id"], issued["capability"]
    log = sessions.transcript(sid, cap)
    log.append("user", "fictional text")
    sessions.stop(sid, cap)
    monkeypatch.setattr(
        services.live_runtime, "drain_session", Mock(side_effect=AssertionError())
    )
    url = f"/api/v1/live/sessions/{sid}/transcript/read"
    headers = {"Authorization": f"Bearer {cap}"}
    response = client.post(url, headers=headers, json={"after_sequence": 0})
    assert response.status_code == 200 and response.content == encode_batch(log.read(0))
    assert response.json()["seal_reason"] == "stopped"
    assert (
        client.post(
            url + "?x=y", headers=headers, json={"after_sequence": 0}
        ).status_code
        == 400
    )
    assert client.get(url).status_code == 405
    assert client.post(url, headers=headers, content="x").status_code == 415
    assert (
        client.post(
            url,
            headers={**headers, "Content-Type": "application/json"},
            content="x" * 257,
        ).status_code
        == 413
    )
    clock.seconds = 301
    assert (
        client.post(url, headers=headers, json={"after_sequence": 0}).status_code == 401
    )
    assert not sessions._sessions


@pytest.mark.asyncio
@pytest.mark.integration
async def test_runtime_order_delivery_fault_and_late_fence(monkeypatch):
    _, sessions, adapter, runtime, issued, _ = setup()
    messages = []
    adapter.peer.send_app_message = messages.append
    await runtime.offer(issued.session_id, issued.capability, "fictional-offer", CONFIG)
    callbacks = adapter.pipe.events
    callbacks["commit"]("fictional user")
    callbacks["assistant_commit"]("fictional assistant", True)
    log = sessions.transcript(issued.session_id, issued.capability)
    assert [e.speaker for e in log.read(0).entries] == ["user", "assistant"]
    assert runtime._active.state.snapshot().last_turn_id == 1
    assert messages[1]["data"]["entry"]["interrupted"] is True
    assert set(messages[0]) == {"label", "type", "id", "data"}
    adapter.peer.send_app_message = Mock(side_effect=ValueError())
    callbacks["assistant_commit"]("still captured", False)
    assert log.last_sequence == 3 and runtime._active.closing is None
    monkeypatch.setattr(log, "append", Mock(side_effect=ValueError()))
    callbacks["assistant_commit"]("fault", False)
    assert log.seal_reason == "capture_error" and runtime._active.closing is None
    sessions.stop(issued.session_id, issued.capability)
    callbacks["commit"]("late")
    callbacks["assistant_commit"]("late", False)
    await runtime.drain_session(issued.session_id)
    assert sessions.transcript(issued.session_id, issued.capability) is log
    assert log.last_sequence == 3
    await runtime.shutdown()
    await asyncio.sleep(0)
    assert not runtime._tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["limit", "append-error"])
@pytest.mark.integration
async def test_user_transcript_failure_does_not_change_intake_or_voice(
    monkeypatch, failure
):
    _, sessions, adapter, runtime, issued, _ = setup()
    await runtime.offer(issued.session_id, issued.capability, "fictional-offer", CONFIG)
    call = runtime._active
    log = call.transcript
    if failure == "limit":
        log.append("assistant", "x" * 4097)
    else:
        monkeypatch.setattr(log, "append", Mock(side_effect=ValueError("private")))
    adapter.pipe.events["commit"]("accepted user text")
    assert call.state.snapshot().turns[0].text == "accepted user text"
    assert log.truncated and call.closing is None
    await runtime.shutdown()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_prepare_seals_before_extraction_and_excludes_control_text():
    from tests.support import audio_setup

    _, sessions, adapter, runtime, issued, _, call = await audio_setup()
    adapter.pipe.events["commit"]("user before prepare")
    original = adapter.pipe.request_audio_draft

    async def extract():
        assert call.transcript.sealed and call.transcript.seal_reason == "prepare"
        adapter.pipe.events["commit"]("internal request must not be recorded")
        adapter.pipe.events["assistant_commit"]("internal extraction", False)
        await original()

    adapter.pipe.request_audio_draft = extract
    await runtime.prepare_audio_draft(issued.session_id, issued.capability)
    batch = call.transcript.read(0)
    assert [entry.text for entry in batch.entries] == ["user before prepare"]
    assert batch.input_tail_complete is False
    ended_at = batch.sealed_at
    sessions.stop(issued.session_id, issued.capability)
    await runtime.drain_session(issued.session_id)
    assert call.transcript.read(0).sealed_at == ended_at
    assert call.transcript.seal_reason == "prepare"
    await runtime.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["stopped", "timeout", "provider_error"])
@pytest.mark.integration
async def test_seal_read_survives_stalled_cleanup(reason):
    from app.models.live import PartialReason

    _, sessions, adapter, runtime, issued, _ = setup()
    barrier = asyncio.Event()
    await runtime.offer(issued.session_id, issued.capability, "fictional-offer", CONFIG)
    call = runtime._active
    adapter.peer.disconnect = barrier.wait
    adapter.pipe.events["assistant_commit"]("retained before closure", False)
    runtime._request_close(call, PartialReason(reason))
    await asyncio.sleep(0)
    try:
        batch = sessions.transcript(issued.session_id, issued.capability).read(0)
        assert batch.sealed and batch.seal_reason == reason
        assert batch.entries[0].text == "retained before closure"
        assert runtime._active is call and not call.closing.done()
        adapter.pipe.events["assistant_commit"]("late", False)
        assert call.transcript.last_sequence == 1
    finally:
        barrier.set()
        await runtime.shutdown()
    assert not runtime._tasks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["unauthorized", "origin", "disabled", "expired-during-read", "deadline"]
)
@pytest.mark.integration
async def test_transcript_http_auth_before_body_and_revalidation(
    api, monkeypatch, mode
):
    from app.api import live
    from tests.support import request

    _, config, sessions, clock, client = api
    issued = bootstrap(api).json()
    path = f"/api/v1/live/sessions/{issued['session_id']}/transcript/read"
    headers = {
        "Authorization": f"Bearer {issued['capability']}",
        "Content-Type": "application/json",
    }
    calls = 0
    if mode == "unauthorized":
        headers["Authorization"] = "Bearer " + "x" * 43
    elif mode == "origin":
        headers["Origin"] = "https://untrusted.invalid"
    elif mode == "disabled":
        monkeypatch.setattr(config, "LIVE_ENABLED", False)
    elif mode == "deadline":
        monkeypatch.setattr(live, "JSON_BODY_READ_TIMEOUT_SECONDS", 0.01)

    async def receive():
        nonlocal calls
        calls += 1
        if mode == "expired-during-read":
            clock.seconds = 301
        elif mode == "deadline":
            await asyncio.sleep(60)
        else:
            raise AssertionError("unauthorized body read")
        return {
            "type": "http.request",
            "body": b'{"after_sequence":0}',
            "more_body": False,
        }

    status, response_headers, body = await request(client.app, path, headers, receive)
    expected = {
        "unauthorized": 401,
        "origin": 403,
        "disabled": 404,
        "expired-during-read": 401,
        "deadline": 408,
    }
    assert status == expected[mode]
    assert response_headers[b"cache-control"] == b"no-store"
    assert set(body["detail"]) == {"message_en", "message_ur"}
    assert calls == (1 if mode in ("expired-during-read", "deadline") else 0)


@pytest.mark.integration
def test_http_worst_case_wire_batch_and_registry_clear(api):
    _, _, sessions, _, client = api
    issued = bootstrap(api).json()
    log = sessions.transcript(issued["session_id"], issued["capability"])
    for _ in range(17):
        log.append("assistant", "\x00" * 4096)
    response = client.post(
        f"/api/v1/live/sessions/{issued['session_id']}/transcript/read",
        headers={"Authorization": f"Bearer {issued['capability']}"},
        json={"after_sequence": 0},
    )
    assert response.status_code == 200 and len(response.content) <= 32768
    assert len(response.json()["entries"]) == 1 and response.json()["has_more"]
    sessions.clear()
    from app.services.live_sessions import LiveSessionUnauthorized

    with pytest.raises(LiveSessionUnauthorized):
        sessions.transcript(issued["session_id"], issued["capability"])
