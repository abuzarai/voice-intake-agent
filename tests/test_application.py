"""Per-application ownership and lifecycle; all I/O is ASGI/fake only."""

from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.application import create_app
from app.composition import LiveServices
from app.settings import Settings

pytestmark = pytest.mark.integration


def configured(secret):
    return Settings(
        LIVE_ENABLED=True,
        LIVE_BOOTSTRAP_SECRET=secret,
        CORS_ORIGINS="https://fixture.example",
    )


def issue(client, secret):
    return client.post(
        "/api/v1/live/sessions",
        json={"processing_consent": True},
        headers={"X-Live-Bootstrap-Secret": secret},
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("LIVE_BOOTSTRAP_SECRET", "fictional-short-secret"),
        ("LIVE_API_KEY", "fictional-secret-marker-" + "x" * 256),
    ],
)
def test_invalid_secret_settings_do_not_echo_values_in_errors(field, value):
    with pytest.raises(ValidationError) as error:
        Settings(**{field: value})
    for rendered in (str(error.value), repr(error.value)):
        assert "fictional-" not in rendered and "input_value" not in rendered


@pytest.mark.parametrize("blank", ["", "   "])
def test_empty_compose_options_leave_disabled_service_unconfigured(blank):
    settings = Settings(
        LIVE_BOOTSTRAP_SECRET=blank, LIVE_API_KEY=blank, LIVE_MODEL=blank
    )
    assert settings.LIVE_BOOTSTRAP_SECRET is None
    assert settings.LIVE_API_KEY is None
    assert settings.LIVE_MODEL is None
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
        assert issue(client, "fictional-not-configured").status_code == 404


def test_factory_creates_isolated_settings_and_service_graphs():
    first, second = create_app(), create_app()
    assert first.state.live is not second.state.live
    assert first.state.live.settings is not second.state.live.settings
    assert first.state.live.live_sessions is not second.state.live.live_sessions
    assert first.state.live.live_runtime is not second.state.live.live_runtime


def test_capability_cannot_cross_application_boundary():
    one, two = (
        "fictional-first-secret-" + "a" * 32,
        "fictional-second-secret-" + "b" * 32,
    )
    first, second = create_app(configured(one)), create_app(configured(two))
    with TestClient(first) as client_one, TestClient(second) as client_two:
        result = issue(client_one, one)
        assert result.status_code == 201
        issued = result.json()
        headers = {"Authorization": "Bearer " + issued["capability"]}
        assert client_one.get(issued["state_url"], headers=headers).status_code == 200
        assert client_two.get(issued["state_url"], headers=headers).status_code == 401
        assert issue(client_two, one).status_code == 401
        assert issue(client_two, two).status_code == 201
    assert not first.state.live.live_sessions._sessions
    assert not second.state.live.live_sessions._sessions


def test_services_cannot_be_shared_or_reused_between_applications():
    services = LiveServices.build(Settings())
    create_app(services=services)
    with pytest.raises(ValueError, match="already belong"):
        create_app(services=services)


def test_factory_rejects_inconsistent_injected_settings():
    original = Settings()
    services = LiveServices.build(original)
    with pytest.raises(ValueError, match="must own"):
        create_app(Settings(), services=services)


def test_enabled_runtime_preloads_before_requests(monkeypatch):
    application = create_app(configured("fictional-preload-secret-" + "x" * 32))
    preload = Mock()
    monkeypatch.setattr(application.state.live.live_runtime, "preload", preload)
    preload.assert_not_called()
    with TestClient(application) as client:
        preload.assert_called_once_with()
        assert client.get("/health").status_code == 200


def test_disabled_runtime_does_not_load_optional_dependencies(monkeypatch):
    application = create_app(Settings(LIVE_ENABLED=False))
    preload = Mock()
    monkeypatch.setattr(application.state.live.live_runtime, "preload", preload)
    with TestClient(application) as client:
        assert client.get("/health").status_code == 200
    preload.assert_not_called()


def test_preload_failure_prevents_readiness_and_still_cleans_up(monkeypatch):
    from app.services.live_runtime import LiveRuntimeUnavailable

    application = create_app(configured("fictional-preload-secret-" + "x" * 32))
    runtime = application.state.live.live_runtime
    preload = Mock(side_effect=LiveRuntimeUnavailable())
    shutdown = AsyncMock()
    monkeypatch.setattr(runtime, "preload", preload)
    monkeypatch.setattr(runtime, "shutdown", shutdown)
    with pytest.raises(LiveRuntimeUnavailable):
        with TestClient(application):
            pytest.fail("A failed preload must not admit requests")
    shutdown.assert_awaited_once_with()


def test_preload_imports_only_the_adapter_module(monkeypatch):
    from app.services import live_runtime

    dependencies = Mock()
    importer = Mock()
    monkeypatch.setattr(live_runtime, "require_live_dependencies", dependencies)
    monkeypatch.setattr(live_runtime, "import_module", importer)
    runtime = LiveServices.build(Settings()).live_runtime
    runtime.preload()
    dependencies.assert_called_once_with()
    importer.assert_called_once_with("app.adapters.pipecat_live.pipeline")
    assert runtime._active is None
    assert not runtime._tasks


def test_injected_adapter_does_not_import_production_dependencies(monkeypatch):
    from app.services import live_runtime

    dependencies = Mock()
    importer = Mock()
    monkeypatch.setattr(live_runtime, "require_live_dependencies", dependencies)
    monkeypatch.setattr(live_runtime, "import_module", importer)
    runtime = LiveServices.build(Settings(), adapter_factory=Mock()).live_runtime
    runtime.preload()
    dependencies.assert_not_called()
    importer.assert_not_called()


def test_shutdown_clears_registry_even_when_runtime_raises(monkeypatch):
    services = LiveServices.build(Settings())
    runtime = AsyncMock(side_effect=RuntimeError("fictional cleanup failure"))
    monkeypatch.setattr(services.live_runtime, "shutdown", runtime)
    services.live_sessions.create()
    with pytest.raises(RuntimeError, match="fictional cleanup failure"):
        with TestClient(create_app(services=services)):
            pass
    runtime.assert_awaited_once()
    assert not services.live_sessions._sessions
