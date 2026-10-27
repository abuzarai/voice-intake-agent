"""All maintained tests are offline. Real provider sessions are operator-only."""

import importlib
import socket
from unittest.mock import Mock

import dotenv
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pydantic_settings.sources import DotEnvSettingsSource

from app.application import create_app
from app.composition import LiveServices
from app.services.live_runtime import LiveProviderConfig, LiveRuntime
from app.settings import Settings
from tests.support import Adapter, Clock, registry


def pytest_collection_modifyitems(items):
    layers = {"unit", "integration", "sdk"}
    for item in items:
        assigned = {mark.name for mark in item.iter_markers()} & layers
        if len(assigned) != 1:
            raise pytest.UsageError(
                f"{item.nodeid}: expected exactly one test layer, got {sorted(assigned)}"
            )


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("network or implicit dotenv discovery is forbidden")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(dotenv, "load_dotenv", denied)
    monkeypatch.setattr(dotenv, "find_dotenv", denied)
    monkeypatch.setattr(DotEnvSettingsSource, "_read_env_file", denied)


@pytest.fixture
def media_adapter():
    return Adapter()


@pytest.fixture
def api(media_adapter):
    config = Settings(
        _env_file=None,
        LIVE_ENABLED=True,
        LIVE_BOOTSTRAP_SECRET="synthetic-test-secret-not-for-use-1234",
        CORS_ORIGINS="https://fixture.example",
        LIVE_CAPABILITY_TTL_SECONDS=300,
        LIVE_MAX_SESSIONS=16,
        LIVE_ADMISSIONS_PER_MINUTE=6,
        LIVE_API_KEY=None,
        LIVE_MODEL=None,
        LIVE_FREE_TIER_ATTESTED=False,
    )
    clock = Clock()
    sessions = registry(clock)
    runtime = LiveRuntime(
        sessions, adapter_factory=lambda: media_adapter, monotonic=clock.monotonic
    )
    services = LiveServices(config, sessions, runtime)
    with TestClient(create_app(services=services)) as client:
        yield services, config, sessions, clock, client


@pytest.fixture
def signaling(api, media_adapter):
    services, config, _, _, _ = api

    def pipeline(connection, config, **events):
        media_adapter.pipe.events = events
        return media_adapter.pipe

    media_adapter.pipeline = pipeline
    config.LIVE_API_KEY = SecretStr("synthetic-live-only-key")
    config.LIVE_MODEL = "explicit-fictional-live-model"
    config.LIVE_FREE_TIER_ATTESTED = True
    return api, media_adapter, services.live_runtime


@pytest.fixture
def pipeline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network/provider/ADC forbidden")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    import google.auth

    monkeypatch.setattr(google.auth, "default", forbidden)
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_GENAI_USE_ENTERPRISE", "true")
    monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", "https://must-not-use.invalid")
    monkeypatch.setenv("GOOGLE_GENAI_CLIENT_MODE", "record")
    monkeypatch.setenv("GOOGLE_GENAI_REPLAYS_DIRECTORY", "/must-not-write")
    from pydantic_settings.sources import DotEnvSettingsSource

    monkeypatch.setattr(DotEnvSettingsSource, "_read_env_files", lambda self: {})
    import builtins

    real_open = builtins.open

    def safe_open(file, *args, **kwargs):
        if str(file).endswith(".env") or "service-account" in str(file):
            raise AssertionError("credential file forbidden")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", safe_open)
    module = importlib.import_module("app.adapters.pipecat_live.pipeline")
    # Constructing clients is offline; the only transport start/connect paths
    # are stubbed or deliberately failed below, never called on a real peer.
    yield module


@pytest.fixture
def gemini(pipeline):
    return importlib.import_module("app.adapters.pipecat_live.gemini")


@pytest_asyncio.fixture
async def llm(pipeline):
    service = pipeline.make_service(
        LiveProviderConfig("offline-placeholder-not-a-key", "explicit-model"), Mock()
    )
    try:
        yield service
    finally:
        await service.close_sdk()
