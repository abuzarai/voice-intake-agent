from datetime import datetime

from fastapi.testclient import TestClient

from app.main import app
from app.models import SessionStatus


client = TestClient(app)


def test_create_session_success(monkeypatch):
    mock_session = {
        "session_id": "sess-001",
        "created_at": datetime.utcnow(),
        "expires_at": datetime.utcnow(),
        "status": SessionStatus.PENDING,
    }

    monkeypatch.setattr(
        "app.api.rest.session_manager.create_session",
        lambda client_id=None, metadata=None: {
            **mock_session,
            "client_id": client_id,
            "metadata": metadata or {},
        },
    )

    response = client.post(
        "/api/v1/sessions",
        json={
            "client_id": "client-123",
            "metadata": {"case_id": 809, "language": "urdu"},
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["session_id"] == "sess-001"
    assert body["status"] == "pending"
    assert "/api/v1/ws/sess-001" in body["ws_url"]


def test_get_session_success(monkeypatch):
    class Result:
        session_id = "sess-abc"
        status = SessionStatus.COMPLETED
        transcript = "Tenant threatened unlawful eviction in Lahore"
        analysis = None
        audio_duration_seconds = 42.5
        audio_url = None
        created_at = datetime.utcnow()
        completed_at = datetime.utcnow()
        client_id = "client-123"
        metadata = {"case_id": 1}

        def model_dump(self):
            return {
                "session_id": self.session_id,
                "status": self.status,
                "transcript": self.transcript,
                "analysis": self.analysis,
                "audio_duration_seconds": self.audio_duration_seconds,
                "audio_url": self.audio_url,
                "created_at": self.created_at,
                "completed_at": self.completed_at,
                "client_id": self.client_id,
                "metadata": self.metadata,
            }

    monkeypatch.setattr(
        "app.api.rest.session_manager.get_result", lambda _session_id: Result()
    )

    response = client.get("/api/v1/sessions/sess-abc")

    assert response.status_code == 200
    assert response.json()["session_id"] == "sess-abc"
    assert response.json()["status"] == "completed"


def test_get_session_not_found(monkeypatch):
    monkeypatch.setattr(
        "app.api.rest.session_manager.get_result", lambda _session_id: None
    )

    response = client.get("/api/v1/sessions/sess-missing")

    assert response.status_code == 404
    assert response.json()["detail"]["message_en"] == "Session not found"


def test_health_endpoint_returns_service_metadata():
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["service"] == "voice-interview-agent"


def test_root_endpoint_returns_basic_info():
    response = client.get("/")

    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "Voice Interview Agent"
    assert body["status"] == "running"
    assert body["test_ui"] == "/test"
