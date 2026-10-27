"""Cold-process import isolation for the v2 factory and canonical entrypoint."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_live_server_cold_default_off_and_shutdown():
    script = r"""
import os, sys, pathlib, dotenv
os.environ["GCP_CREDENTIALS_PATH"] = "forbidden-legacy-discovery"
os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
original_exists = pathlib.Path.exists
def guarded_exists(self):
    if "forbidden-legacy-discovery" in str(self):
        raise AssertionError("legacy credential probe")
    return original_exists(self)
pathlib.Path.exists = guarded_exists
def denied(*args, **kwargs):
    raise AssertionError("legacy discovery forbidden")
dotenv.load_dotenv = denied
dotenv.find_dotenv = denied
from pydantic_settings.sources import DotEnvSettingsSource
DotEnvSettingsSource._read_env_file = denied
before = dict(os.environ)
import app
from app.application import create_app
assert dict(os.environ) == before
assert "app.config" not in sys.modules
assert "app.main" not in sys.modules
assert "app.services.storage_service" not in sys.modules
assert "pipecat" not in sys.modules
application = create_app()
assert dict(os.environ) == before
assert application.state.live.settings.LIVE_ENABLED is False
assert application.version == "2.0.0"
from fastapi.testclient import TestClient
calls = []
async def runtime(): calls.append("runtime")
application.state.live.live_runtime.shutdown = runtime
application.state.live.live_sessions.clear = lambda: calls.append("clear")
with TestClient(application) as client:
    assert client.get("/health").status_code == 200
    assert client.get("/openapi.json").status_code == 404
    response = client.post("/api/v1/live/sessions", json={"processing_consent": True})
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
assert calls == ["runtime", "clear"]
from app.main import create_app as entrypoint
assert entrypoint is create_app
assert dict(os.environ) == before
assert "VOICE_INTAKE_LIVE_ONLY" not in os.environ
assert "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ
"""
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("LIVE_", "GCP_", "GOOGLE_", "GEMINI_", "VOICE_INTAKE_"))
    }
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_package_and_domain_imports_are_inert_even_with_legacy_environment():
    script = r"""
import os, sys, dotenv, pathlib
os.environ["GCP_CREDENTIALS_PATH"] = "fictional-credential-path"
os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
def denied(*args, **kwargs):
    raise AssertionError("package import attempted legacy discovery")
dotenv.load_dotenv = denied
dotenv.find_dotenv = denied
pathlib.Path.resolve = denied
before = dict(os.environ)
import app
from app.models import intake, live, live_audio_draft
assert not any(name == prefix or name.startswith(prefix + ".")
               for name in sys.modules for prefix in ("fastapi", "pipecat", "google"))
assert dict(os.environ) == before
assert app.__version__ == "2.0.0"
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
