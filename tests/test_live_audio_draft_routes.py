"""Capability-scoped same-session draft API, fake retained-audio provider only."""

from types import SimpleNamespace

import pytest

from tests.support import (
    accept_callback,
    assert_error,
    bearer,
    bootstrap,
    offer,
    params,
)

pytestmark = pytest.mark.integration


def url(issued):
    return issued["state_url"].removesuffix("/state") + "/audio-draft"


def enable_audio(signaling, issued, *, failure=False):
    api, adapter, runtime = signaling
    client = api[-1]
    assert offer(client, issued).status_code == 200
    call = runtime._active
    adapter.pipe.ready = True
    adapter.pipe.gated = False

    def gate():
        adapter.pipe.gated = True

    adapter.pipe.gate_audio = gate

    async def roundtrip():
        pass

    call.audio_draft.result_roundtrip = roundtrip

    async def send():
        assert adapter.pipe.gated
        if failure:
            raise ValueError("private-provider-payload")
        await call.audio_draft.handle_tool(
            params(accept_callback), properties_factory=SimpleNamespace
        )

    adapter.pipe.request_audio_draft = send
    return call


def test_authorization_origin_query_config_and_no_store(signaling):
    api, adapter, runtime = signaling
    _, config, sessions, clock, client = api
    first, second = bootstrap(api).json(), bootstrap(api).json()
    for method in ("GET", "POST"):
        for headers in ({}, bearer(second)):
            # No JSON supplied: unauthorized must win before body parsing.
            assert_error(client.request(method, url(first), headers=headers), 401)
        assert_error(
            client.request(
                method, url(first), headers={**bearer(first), "Origin": "null"}
            ),
            403,
        )
        assert_error(
            client.request(method, url(first) + "?token=no", headers=bearer(first)), 400
        )
        config.LIVE_ENABLED = False
        assert_error(client.request(method, url(first), headers=bearer(first)), 404)
        config.LIVE_ENABLED = True
        config.LIVE_FREE_TIER_ATTESTED = False
        assert_error(client.request(method, url(first), headers=bearer(first)), 503)
        config.LIVE_FREE_TIER_ATTESTED = True
    assert adapter.created == 0


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"acknowledge_unproven_tail": False},
        {"acknowledge_unproven_tail": 1},
        {"acknowledge_unproven_tail": "true"},
        {"acknowledge_unproven_tail": True, "context": "injected"},
        {"acknowledge_unproven_tail": True, "timeout_seconds": 30},
        {"acknowledge_unproven_tail": True, "confirm": True},
    ],
)
def test_strict_bounded_request_never_reserves_invalid_body(signaling, payload):
    api, adapter, runtime = signaling
    client = api[-1]
    issued = bootstrap(api).json()
    call = enable_audio(signaling, issued)
    assert_error(client.post(url(issued), headers=bearer(issued), json=payload), 422)
    assert call.audio_draft.phase == "not_requested" and not adapter.pipe.gated


def test_body_limits_content_type_and_invalid_json(signaling):
    api, adapter, runtime = signaling
    client = api[-1]
    issued = bootstrap(api).json()
    headers = {**bearer(issued), "Content-Type": "application/json"}
    assert_error(
        client.post(url(issued), headers=headers, content=iter([b" " * 200] * 2)), 413
    )
    assert_error(client.post(url(issued), headers=headers, content="{"), 422)
    assert_error(client.post(url(issued), headers=bearer(issued), content="{}"), 415)
    assert adapter.created == 0


def test_single_post_get_no_text_flag_and_stop_deletes(signaling):
    api, adapter, runtime = signaling
    _, config, sessions, clock, client = api
    assert "LIVE_DRAFT_EXTRACTION_ENABLED" not in type(config).model_fields
    issued = bootstrap(api).json()
    assert_error(client.get(url(issued), headers=bearer(issued)), 409)
    call = enable_audio(signaling, issued)
    initial = client.get(url(issued), headers=bearer(issued))
    assert initial.status_code == 200 and initial.json()["phase"] == "not_requested"
    response = client.post(
        url(issued), headers=bearer(issued), json={"acknowledge_unproven_tail": True}
    )
    assert (
        response.status_code == 200 and response.headers["cache-control"] == "no-store"
    )
    body = response.json()
    assert body["session_id"] == issued["session_id"] and body["phase"] == "proposed"
    proposal = body["proposal"]
    assert proposal["confirmed"] is False and proposal["input_tail_complete"] is False
    assert len(proposal["facts"]) == 11
    for forbidden in ("source_turn_ids", "revision", "content_hash", "transcript"):
        assert forbidden not in response.text
    assert_error(
        client.post(
            url(issued),
            headers=bearer(issued),
            json={"acknowledge_unproven_tail": True},
        ),
        409,
    )
    assert client.get(url(issued), headers=bearer(issued)).json() == body
    assert client.post(issued["stop_url"], headers=bearer(issued)).status_code == 200
    assert call.audio_draft.view(issued["session_id"]).proposal is None
    assert_error(client.get(url(issued), headers=bearer(issued)), 409)
    # The separate text endpoint is unmounted, not a disabled fallback.
    retired = client.get(
        url(issued).replace("audio-draft", "draft"), headers=bearer(issued)
    )
    assert retired.status_code == 404 and retired.json() == {"detail": "Not Found"}


def test_failure_sanitized_and_expiry_erases_proposal(signaling):
    api, adapter, runtime = signaling
    _, _, sessions, clock, client = api
    issued = bootstrap(api).json()
    call = enable_audio(signaling, issued, failure=True)
    response = client.post(
        url(issued), headers=bearer(issued), json={"acknowledge_unproven_tail": True}
    )
    assert_error(response, 502)
    assert "private-provider" not in response.text
    assert call.audio_draft.view(issued["session_id"]).proposal is None
    client.portal.call(runtime.drain_session, issued["session_id"])
    assert runtime._active is None


def test_expired_get_deletes_synchronously(signaling):
    api, adapter, runtime = signaling
    _, _, sessions, clock, client = api
    issued = bootstrap(api).json()
    call = enable_audio(signaling, issued)
    assert (
        client.post(
            url(issued),
            headers=bearer(issued),
            json={"acknowledge_unproven_tail": True},
        ).status_code
        == 200
    )
    clock.seconds = 300
    assert_error(client.get(url(issued), headers=bearer(issued)), 401)
    assert call.audio_draft.view(issued["session_id"]).proposal is None
