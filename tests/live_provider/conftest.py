"""Live provider extraction replay.

Network is allowed in this directory and nowhere else in the suite. The parent
conftest blocks sockets for every test under ``tests/``; the fixture below
shadows that block so the replay can open a real Gemini Live session.
"""

import os

import pytest

REQUIRED = ("LIVE_EVAL_GEMINI_KEY", "LIVE_EVAL_MODEL")


def pytest_collection_modifyitems(config, items):
    missing = [name for name in REQUIRED if not os.environ.get(name, "").strip()]
    if not missing:
        return
    reason = f"provider eval needs {', '.join(missing)}"
    for item in items:
        if item.get_closest_marker("provider") is not None:
            item.add_marker(pytest.mark.skip(reason=reason))


@pytest.fixture(autouse=True)
def deny_network():
    """Shadow the offline suite's block for this directory only."""
    yield


@pytest.fixture(scope="session")
def provider_config():
    from app.services.live_runtime import LiveProviderConfig

    return LiveProviderConfig(
        api_key=os.environ["LIVE_EVAL_GEMINI_KEY"].strip(),
        model=os.environ["LIVE_EVAL_MODEL"].strip(),
    )
