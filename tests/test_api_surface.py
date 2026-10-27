"""Supported API surface and safe rejection of unsupported paths."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import Settings

pytestmark = pytest.mark.integration


def test_exact_supported_route_surface():
    base = "/api/v1/live/sessions"
    application = create_app(Settings(LIVE_ENABLED=False))
    actual = {
        (path, method.upper())
        for path, operations in application.openapi()["paths"].items()
        for method in operations
    }
    assert actual == {
        (base, "POST"),
        (base + "/{session_id}/state", "GET"),
        (base + "/{session_id}/stop", "POST"),
        (base + "/{session_id}/webrtc/offer", "POST"),
        (base + "/{session_id}/webrtc/offer", "PATCH"),
        (base + "/{session_id}/audio-draft", "GET"),
        (base + "/{session_id}/audio-draft", "POST"),
        (base + "/{session_id}/transcript/read", "POST"),
    }
    with TestClient(application) as client:
        assert client.get("/health").status_code == 200


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/v1/sessions"),
        ("GET", "/api/v1/sessions/fictional"),
        ("GET", "/api/v1/live/sessions/fictional/draft"),
        ("POST", "/api/v1/live/sessions/fictional/draft"),
        ("POST", "/api/v1/live/sessions/fictional/finalize"),
    ],
)
def test_unsupported_endpoints_are_unmounted(method, path):
    with TestClient(create_app(Settings(LIVE_ENABLED=False))) as client:
        response = client.request(method, path)
    assert response.status_code == 404 and response.json() == {"detail": "Not Found"}


@pytest.mark.parametrize(
    "path", ["/api/v1/live/sessions/", "/api/v1/live/sessions/fictional/state/"]
)
def test_mistyped_paths_do_not_redirect_or_reflect_query_capabilities(path):
    with TestClient(create_app(Settings(LIVE_ENABLED=False))) as client:
        response = client.get(
            path + "?capability=fictional-query-secret", follow_redirects=False
        )
    assert response.status_code == 404 and "location" not in response.headers
    assert "fictional-query-secret" not in response.text
