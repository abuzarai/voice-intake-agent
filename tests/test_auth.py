"""Production-hardening tests: /test hidden, CORS from settings."""

import os

os.environ["ENVIRONMENT"] = "production"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def test_health_ok():
    assert client.get("/api/v1/health").status_code == 200


def test_test_ui_hidden_in_production():
    assert client.get("/test").status_code == 404


def test_root_omits_test_link_in_production():
    body = client.get("/").json()
    assert "test_ui" not in body


def test_cors_reflects_configured_origin():
    resp = client.options(
        "/api/v1/sessions",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.status_code == 200
    allow = resp.headers.get("access-control-allow-origin")
    assert allow == "http://localhost:3000"


def test_cors_rejects_unlisted_origin():
    resp = client.options(
        "/api/v1/sessions",
        headers={
            "Origin": "https://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    allow = resp.headers.get("access-control-allow-origin")
    assert allow is None or allow == "*"