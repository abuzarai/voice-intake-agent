"""Authorized bounded signaling HTTP tests with entirely fake media/provider."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from tests.support import assert_error, bearer, bootstrap, offer, offer_url

pytestmark = pytest.mark.integration


def test_authorization_origin_query_default_off_all_signaling_routes(signaling):
    api, adapter, _ = signaling
    _, config, _, _, client = api
    first, second = bootstrap(api).json(), bootstrap(api).json()
    for method in ("POST", "PATCH"):
        url = offer_url(first)
        for headers in ({}, bearer(second), {"Authorization": "Bearer " + "x" * 43}):
            assert_error(client.request(method, url, headers=headers), 401)
        assert_error(
            client.request(method, url, headers={**bearer(first), "Origin": "null"}),
            403,
        )
        assert_error(
            client.request(
                method, url, headers={**bearer(first), "Origin": "https://evil.example"}
            ),
            403,
        )
        assert_error(
            client.request(method, url + "?token=never-in-url", headers=bearer(first)),
            400,
        )
        config.LIVE_ENABLED = False
        assert_error(client.request(method, url, headers=bearer(first)), 404)
        config.LIVE_ENABLED = True
    assert adapter.created == 0


@pytest.mark.parametrize(
    "setting,value",
    [
        ("LIVE_API_KEY", None),
        ("LIVE_MODEL", None),
        ("LIVE_MODEL", "has whitespace"),
        ("LIVE_MODEL", "models/gemini-fictional"),
        ("LIVE_MODEL", "https://example.invalid/model"),
        ("LIVE_MODEL", "gemini:fictional"),
        ("LIVE_MODEL", ".gemini-fictional"),
        ("LIVE_MODEL", "gemini?key=fictional"),
        ("LIVE_MODEL", "gemini-فرضی"),
        ("LIVE_MODEL", "x" * 129),
        ("LIVE_MODEL", 42),
        ("LIVE_FREE_TIER_ATTESTED", False),
    ],
)
def test_live_only_key_model_attestation_before_adapter_no_legacy_fallback(
    signaling, setting, value
):
    api, adapter, _ = signaling
    _, config, _, _, client = api
    assert "GEMINI_API_KEY" not in type(config).model_fields
    assert "GCP_PROJECT_ID" not in type(config).model_fields
    setattr(config, setting, value)
    issued = bootstrap(api).json()
    assert_error(offer(client, issued), 503)
    assert adapter.created == 0
    assert (
        client.get(issued["state_url"], headers=bearer(issued)).json()["phase"]
        == "pending"
    )


def test_offer_strict_schema_streamed_bounds_and_sanitized_errors(signaling):
    api, adapter, _ = signaling
    client = api[-1]
    issued = bootstrap(api).json()
    for payload in (
        {"sdp": "fictional-offer", "type": "answer"},
        {"sdp": "fictional-offer", "type": "offer", "pc_id": "injected"},
        {"sdp": "fictional-offer", "type": "offer", "restart_pc": True},
        # SDK defaults must be normalized by the application server.
        # The internal API deliberately stays canonical and strict.
        {"sdp": "fictional-offer", "type": "offer", "pc_id": None, "restart_pc": False},
        {
            "sdp": "fictional-offer",
            "type": "offer",
            "request_data": {"command": "confirm"},
        },
        {"sdp": 1, "type": "offer"},
        {"sdp": "", "type": "offer"},
        {"sdp": "x" * 65537, "type": "offer"},
        {"sdp": "ا" * 33000, "type": "offer"},
    ):
        assert_error(offer(client, issued, json=payload), 422)
    assert_error(
        client.post(
            offer_url(issued),
            headers={**bearer(issued), "Content-Type": "application/json"},
            content=iter([b" " * 40000] * 2),
        ),
        413,
    )
    assert adapter.created == 0
    adapter.peer.error = RuntimeError("private-payload-key")
    response = offer(client, issued)
    assert_error(response, 502)
    assert "private-payload" not in response.text
    assert adapter.peer.closed == 1
    assert_error(offer(client, issued), 409)


def test_offer_patch_session_peer_isolation_stop_and_no_state_commands(
    signaling, caplog
):
    api, adapter, _ = signaling
    client = api[-1]
    first, second = bootstrap(api).json(), bootstrap(api).json()
    response = offer(client, first)
    assert (
        response.status_code == 200 and response.headers["cache-control"] == "no-store"
    )
    assert response.json() == adapter.peer.answer
    patch = {
        "pc_id": "owned-peer",
        "candidates": [{"candidate": "", "sdp_mid": "0", "sdp_mline_index": 0}],
    }
    assert_error(
        client.patch(offer_url(second), headers=bearer(second), json=patch), 409
    )
    assert_error(
        client.patch(offer_url(first), headers=bearer(second), json=patch), 401
    )
    assert_error(
        client.patch(
            offer_url(first), headers=bearer(first), json={**patch, "pc_id": "wrong"}
        ),
        409,
    )
    assert not adapter.peer.candidates
    good = client.patch(offer_url(first), headers=bearer(first), json=patch)
    assert good.status_code == 200 and good.json() == {"status": "accepted"}
    for candidates in (
        [],
        patch["candidates"] * 33,
        [{"candidate": "x" * 2049, "sdp_mid": "0", "sdp_mline_index": 0}],
        [{"candidate": "", "sdp_mid": "0", "sdp_mline_index": True}],
    ):
        assert_error(
            client.patch(
                offer_url(first),
                headers=bearer(first),
                json={**patch, "candidates": candidates},
            ),
            422,
        )
    assert_error(offer(client, first), 409)
    assert_error(offer(client, second), 409)
    stopped = client.post(first["stop_url"], headers=bearer(first))
    assert stopped.json()["partial_reason"] == "stopped" and adapter.peer.closed == 1
    assert (
        client.post(first["stop_url"], headers=bearer(first)).json() == stopped.json()
    )
    assert_error(client.patch(offer_url(first), headers=bearer(first), json=patch), 409)
    base = first["state_url"].removesuffix("/state")
    for command in ("facts", "finalize", "confirm", "turns"):
        assert (
            client.post(
                base + "/" + command, headers=bearer(first), json={}
            ).status_code
            == 404
        )
    assert first["capability"] not in caplog.text
    assert "fictional-offer" not in caplog.text


def test_concurrent_http_offer_failure_cannot_allocate_or_retry_twice(signaling):
    api, adapter, runtime = signaling
    client = api[-1]
    issued = bootstrap(api).json()
    adapter.peer.error = ValueError("private-provider-payload")
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: offer(client, issued), range(2)))
    assert sorted(response.status_code for response in responses) == [409, 502]
    assert adapter.created == 1
    assert all(
        "private-provider-payload" not in response.text for response in responses
    )
    assert_error(offer(client, issued), 409)
    assert client.post(issued["stop_url"], headers=bearer(issued)).status_code == 200
    assert_error(offer(client, issued), 409)
    assert adapter.created == 1 and runtime._active is None
