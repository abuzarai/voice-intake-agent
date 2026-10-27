"""Registry claims stay independent; only claim conflicts cross the runtime boundary."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.services import live_sessions
from app.services.live_runtime import LiveRuntimeConflict
from app.services.live_sessions import LiveSessionUnauthorized
from tests.support import CONFIG, setup

pytestmark = pytest.mark.integration


def test_registry_claims_without_runtime_http_or_provider_imports():
    script = """
import importlib.abc
import socket
import sys

class DenyImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'fastapi', 'starlette', 'pipecat', 'google'} or fullname == 'app.services.live_runtime' or fullname == 'app.api' or fullname.startswith('app.api.'):
            raise AssertionError('forbidden import: ' + fullname)

sys.meta_path.insert(0, DenyImports())
def denied(*args, **kwargs):
    raise AssertionError('network or dotenv forbidden')
socket.socket.connect = denied
socket.socket.connect_ex = denied
socket.getaddrinfo = denied
import dotenv
dotenv.load_dotenv = denied
dotenv.find_dotenv = denied
from pydantic_settings.sources import DotEnvSettingsSource
DotEnvSettingsSource._read_env_file = denied
from app.services.live_sessions import LiveSessionRegistry, LiveSessionClaimConflict
assert LiveSessionClaimConflict.__module__ == 'app.services.live_sessions'
sessions = LiveSessionRegistry(ttl_seconds=30, max_sessions=2, admissions_per_minute=2)
first = sessions.create()
sessions.claim_runtime(first.session_id, first.capability)
second = sessions.create()
sessions.stop(second.session_id, second.capability)
for issued in (first, second):
    try:
        sessions.claim_runtime(issued.session_id, issued.capability)
    except LiveSessionClaimConflict:
        pass
    else:
        raise AssertionError('claim must conflict')
assert not sessions._sessions[second.session_id].offer_started
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [False, True])
async def test_runtime_translates_registry_claim_conflict_without_reservation(terminal):
    _, sessions, adapter, runtime, first, _ = setup()
    if terminal:
        sessions.stop(first.session_id, first.capability)
    else:
        sessions.claim_runtime(first.session_id, first.capability)
    with pytest.raises(LiveRuntimeConflict) as caught:
        await runtime.offer(first.session_id, first.capability, "offer", CONFIG)
    assert isinstance(caught.value.__cause__, live_sessions.LiveSessionClaimConflict)
    assert runtime._active is None and not runtime._tasks
    assert adapter.created == 0
    assert sessions._sessions[first.session_id].offer_started is (not terminal)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [LiveSessionUnauthorized(), ValueError("synthetic")])
async def test_runtime_propagates_non_conflict_claim_failures(monkeypatch, error):
    _, sessions, adapter, runtime, first, _ = setup()
    claim = Mock(side_effect=error)
    monkeypatch.setattr(sessions, "claim_runtime", claim)
    with pytest.raises(type(error)) as caught:
        await runtime.offer(first.session_id, first.capability, "offer", CONFIG)
    assert caught.value is error
    claim.assert_called_once_with(first.session_id, first.capability)
    assert runtime._active is None and not runtime._tasks
    assert adapter.created == 0
    assert not sessions._sessions[first.session_id].offer_started


@pytest.mark.asyncio
async def test_unauthorized_offer_does_not_attempt_claim(monkeypatch):
    _, sessions, adapter, runtime, first, _ = setup()
    claim = Mock()
    monkeypatch.setattr(sessions, "claim_runtime", claim)
    with pytest.raises(LiveSessionUnauthorized):
        await runtime.offer(first.session_id, "wrong-capability", "offer", CONFIG)
    claim.assert_not_called()
    assert runtime._active is None and not runtime._tasks
    assert adapter.created == 0
    assert not sessions._sessions[first.session_id].offer_started
