"""Offline, synthetic-only admission contract and bounded registry tests."""

from datetime import timedelta
from unittest.mock import Mock

import pytest

from app.services.live_sessions import (
    LiveAdmissionFull,
    LiveAdmissionRateLimited,
    LiveSessionUnauthorized,
)
from tests.support import Clock, assert_error, bearer, bootstrap, registry


@pytest.mark.integration
def test_unsupported_methods_keep_sensitive_response_policy(api):
    client = api[-1]
    for method, path, allowed in (
        ("GET", "/api/v1/live/sessions", "POST"),
        ("DELETE", "/api/v1/live/sessions/unknown/state", "GET"),
        ("GET", "/api/v1/live/sessions/unknown/stop", "POST"),
    ):
        response = client.request(method, path)
        assert_error(response, 405)
        assert allowed in response.headers["allow"]


@pytest.mark.unit
def test_registry_hash_only_expiry_binding_and_defensive_snapshots():
    clock = Clock()
    sessions = registry(clock)
    first, second = sessions.create(), sessions.create()
    assert first.capability != second.capability and len(first.capability) == 43
    assert first.capability not in repr(first)
    assert first.capability not in repr(sessions._sessions)
    stored = sessions._sessions[first.session_id]
    assert len(stored.capability_hash) == 32
    assert "capability" not in vars(stored)
    with pytest.raises(LiveSessionUnauthorized):
        sessions.snapshot(first.session_id, second.capability)
    original_expiry = stored.expires_at
    stored.expires_at += timedelta(seconds=1)
    with pytest.raises(LiveSessionUnauthorized):
        sessions.snapshot(first.session_id, first.capability)
    stored.expires_at = original_expiry
    snapshot = sessions.snapshot(first.session_id, first.capability)
    object.__setattr__(snapshot, "session_id", "mutated-copy")
    assert (
        sessions.snapshot(first.session_id, first.capability).session_id
        == first.session_id
    )
    clock.seconds = 300
    with pytest.raises(LiveSessionUnauthorized):
        sessions.stop(first.session_id, first.capability)
    assert sessions._sessions == {}


@pytest.mark.unit
def test_registry_capacity_rate_expiry_and_shutdown():
    clock = Clock()
    sessions = registry(clock, max_sessions=1, admissions_per_minute=2, ttl_seconds=30)
    first = sessions.create()
    stopped = sessions.stop(first.session_id, first.capability)
    assert stopped.phase == "partial" and stopped.partial_reason == "stopped"
    assert sessions.stop(first.session_id, first.capability) == stopped
    with pytest.raises(LiveAdmissionFull):
        sessions.create()
    clock.seconds = 30
    sessions.create()
    clock.seconds = 59
    with pytest.raises(LiveAdmissionRateLimited):
        sessions.create()
    assert len(sessions._sessions) == 1
    clock.seconds = 60
    sessions.create()
    sessions.clear()
    assert not sessions._sessions


@pytest.mark.parametrize(
    "setting,value", [("LIVE_ENABLED", False), ("LIVE_BOOTSTRAP_SECRET", None)]
)
@pytest.mark.integration
def test_disabled_or_missing_secret_gates_all_routes(api, setting, value):
    _, config, sessions, _, client = api
    setattr(config, setting, value)
    for method, path in (
        ("POST", ""),
        ("GET", "/unknown/state"),
        ("POST", "/unknown/stop"),
    ):
        assert_error(client.request(method, "/api/v1/live/sessions" + path), 404)
    assert not sessions._sessions


@pytest.mark.integration
def test_defaults_are_off_and_secret_has_no_default(api):
    from app.settings import Settings

    settings = Settings(_env_file=None, LIVE_ENABLED=False, LIVE_BOOTSTRAP_SECRET=None)
    assert settings.LIVE_ENABLED is False and settings.LIVE_BOOTSTRAP_SECRET is None
    assert Settings.model_fields["LIVE_ENABLED"].default is False
    assert Settings.model_fields["LIVE_BOOTSTRAP_SECRET"].default is None
    assert Settings.model_fields["LIVE_CAPABILITY_TTL_SECONDS"].default == 300
    for value in ("short", "x" * 257):
        with pytest.raises(ValueError):
            Settings(_env_file=None, LIVE_BOOTSTRAP_SECRET=value)


@pytest.mark.integration
def test_bootstrap_secret_and_strict_ack(api):
    for headers in (
        {},
        {"X-Live-Bootstrap-Secret": "wrong"},
        {"Authorization": "Bearer " + "x" * 43},
    ):
        assert_error(bootstrap(api, headers=headers), 401)
    for body in (
        {},
        {"processing_consent": False},
        {"processing_consent": "true"},
        {"processing_consent": 1},
        {"processing_consent": True, "client_id": "forbidden"},
        {"synthetic_only": True},
        [],
    ):
        assert_error(bootstrap(api, json=body), 422)
    assert not api[2]._sessions


@pytest.mark.integration
def test_bootstrap_bounds_malformed_json_and_no_reflection(api):
    _, config, _, _, client = api
    headers = {
        "X-Live-Bootstrap-Secret": config.LIVE_BOOTSTRAP_SECRET.get_secret_value(),
        "Content-Type": "application/json",
    }
    for content, status in (
        (b"x" * 257, 413),
        (b'{"processing_consent":', 422),
        (b'"private-payload"', 422),
        (b"\xff", 422),
    ):
        response = client.post(
            "/api/v1/live/sessions", headers=headers, content=content
        )
        assert_error(response, status)
        assert "private-payload" not in response.text
    assert_error(
        client.post(
            "/api/v1/live/sessions",
            headers={**headers, "Content-Type": "text/plain"},
            content="{}",
        ),
        415,
    )
    # Check actual streamed bytes rather than trusting Content-Length.
    assert_error(
        client.post(
            "/api/v1/live/sessions", headers=headers, content=iter([b" " * 128] * 3)
        ),
        413,
    )


@pytest.mark.integration
def test_origin_exact_allowlist_applies_to_bootstrap_state_and_stop(api):
    _, config, _, _, client = api
    secret = {
        "X-Live-Bootstrap-Secret": config.LIVE_BOOTSTRAP_SECRET.get_secret_value()
    }
    for origin in ("https://evil.example", "null", "https://fixture.example.evil", "*"):
        assert_error(bootstrap(api, headers={**secret, "Origin": origin}), 403)
    issued = bootstrap(
        api, headers={**secret, "Origin": "https://fixture.example"}
    ).json()
    for method, key in (("GET", "state_url"), ("POST", "stop_url")):
        assert_error(
            client.request(
                method,
                issued[key],
                headers={**bearer(issued), "Origin": "https://evil.example"},
            ),
            403,
        )
        assert (
            client.request(
                method,
                issued[key],
                headers={**bearer(issued), "Origin": "https://fixture.example"},
            ).status_code
            == 200
        )
    config.CORS_ORIGINS = "*"
    assert_error(
        bootstrap(api, headers={**secret, "Origin": "https://fixture.example"}), 403
    )


@pytest.mark.integration
def test_capability_isolation_expiry_no_query_and_no_leakage(api, caplog):
    _, config, sessions, clock, client = api
    response = bootstrap(api)
    assert (
        response.status_code == 201 and response.headers["cache-control"] == "no-store"
    )
    first, second = response.json(), bootstrap(api).json()
    assert set(first) == {
        "session_id",
        "created_at",
        "expires_at",
        "capability",
        "token_type",
        "state_url",
        "stop_url",
    }
    assert config.LIVE_BOOTSTRAP_SECRET.get_secret_value() not in response.text
    for method, key in (("GET", "state_url"), ("POST", "stop_url")):
        for headers in (
            {},
            bearer(second),
            {
                "X-Live-Bootstrap-Secret": config.LIVE_BOOTSTRAP_SECRET.get_secret_value()
            },
            {"Authorization": "Bearer " + "x" * 1000},
            {"Authorization": "Bearer bad"},
        ):
            assert_error(client.request(method, first[key], headers=headers), 401)
        query_response = client.request(
            method, first[key] + "?capability=query-token-never-supported"
        )
        assert_error(query_response, 400)
        assert "query-token-never-supported" not in query_response.text
    assert_error(
        client.get("/api/v1/live/sessions/unknown/state", headers=bearer(first)), 401
    )
    state = client.get(first["state_url"], headers=bearer(first))
    assert state.status_code == 200 and state.headers["cache-control"] == "no-store"
    assert state.json()["phase"] == "pending" and "result" not in state.json()
    assert first["capability"] not in state.text
    stopped = client.post(first["stop_url"], headers=bearer(first))
    assert stopped.json()["phase"] == "partial" and "result" not in stopped.json()
    assert (
        client.post(first["stop_url"], headers=bearer(first)).json() == stopped.json()
    )
    assert (
        client.get(second["state_url"], headers=bearer(second)).json()["phase"]
        == "pending"
    )
    clock.seconds = 300
    assert_error(client.get(first["state_url"], headers=bearer(first)), 401)
    assert not sessions._sessions
    assert first["capability"] not in caplog.text
    assert config.LIVE_BOOTSTRAP_SECRET.get_secret_value() not in caplog.text


@pytest.mark.integration
def test_routes_do_not_log_secrets_payload_or_query(api, caplog):
    _, config, _, _, client = api
    caplog.set_level("DEBUG", logger="app")
    issued = bootstrap(api).json()
    assert client.get(issued["state_url"], headers=bearer(issued)).status_code == 200
    response = client.get(issued["state_url"] + "?capability=" + issued["capability"])
    assert_error(response, 400)
    assert issued["capability"] not in response.text
    assert issued["capability"] not in caplog.text
    assert config.LIVE_BOOTSTRAP_SECRET.get_secret_value() not in caplog.text
    assert (
        "processing_consent" not in caplog.text and "Authorization" not in caplog.text
    )


@pytest.mark.integration
def test_http_capacity_rate_and_sanitized_unexpected_error(api, monkeypatch):
    module, _, _, clock, _ = api
    sessions = registry(clock, max_sessions=1, admissions_per_minute=1)
    monkeypatch.setattr(module, "live_sessions", sessions)
    assert bootstrap(api).status_code == 201
    assert_error(bootstrap(api), 429)
    clock.seconds = 60
    assert_error(bootstrap(api), 503)
    clock.seconds = 300
    assert bootstrap(api).status_code == 201
    monkeypatch.setattr(
        sessions, "create", Mock(side_effect=RuntimeError("private-internal-secret"))
    )
    response = bootstrap(api)
    assert_error(response, 500)
    assert "private-internal-secret" not in response.text
