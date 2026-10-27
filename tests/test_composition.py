"""The service graph is inert, HTTP-independent and application-owned."""

import subprocess
import sys
from pathlib import Path

import pytest

from app.application import create_app
from app.composition import LiveServices
from app.settings import Settings
from tests.support import Clock, registry

pytestmark = pytest.mark.integration


def test_composition_import_is_inert_and_independent_of_http_and_provider_sdks():
    script = """
import importlib.abc
import socket
import sys

class DenyImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'fastapi', 'starlette', 'pipecat', 'google'} or fullname == 'app.api' or fullname.startswith('app.api.'):
            raise AssertionError('forbidden import: ' + fullname)

sys.meta_path.insert(0, DenyImports())
def denied(*args, **kwargs):
    raise AssertionError('import must not construct services or perform I/O')
socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.getaddrinfo = denied
import dotenv
dotenv.load_dotenv = denied
dotenv.find_dotenv = denied
from pydantic_settings.sources import DotEnvSettingsSource
DotEnvSettingsSource._read_env_file = denied
from app.settings import Settings
from app.services.live_sessions import LiveSessionRegistry
from app.services.live_runtime import LiveRuntime
Settings.__init__ = denied
LiveSessionRegistry.__init__ = denied
LiveRuntime.__init__ = denied
from app.composition import LiveServices
assert LiveServices.__module__ == 'app.composition'
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_build_preserves_injected_graph_identity():
    settings = Settings(_env_file=None)
    sessions = registry(Clock())
    adapter_factory = object()
    services = LiveServices.build(
        settings, sessions=sessions, adapter_factory=adapter_factory
    )
    assert services.settings is settings
    assert services.live_sessions is sessions
    assert services.live_runtime.sessions is sessions
    assert services.live_runtime._adapter_factory is adapter_factory
    application = create_app(settings, services=services)
    assert application.state.live is services


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
async def test_shutdown_finishes_runtime_before_clearing_registry(monkeypatch, fails):
    services = LiveServices.build(Settings(_env_file=None))
    events = []

    async def shutdown():
        events.append("runtime")
        if fails:
            raise RuntimeError("synthetic shutdown failure")

    monkeypatch.setattr(services.live_runtime, "shutdown", shutdown)
    monkeypatch.setattr(services.live_sessions, "clear", lambda: events.append("clear"))
    if fails:
        with pytest.raises(RuntimeError, match="synthetic shutdown failure"):
            await services.shutdown()
    else:
        await services.shutdown()
    assert events == ["runtime", "clear"]
