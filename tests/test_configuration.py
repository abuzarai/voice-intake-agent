"""Explicit Live provider configuration and secret-safe validation."""

import pytest

from app.services.live_runtime import LiveProviderConfig, LiveRuntimeUnavailable
from app.settings import Settings

pytestmark = pytest.mark.unit


def test_unrelated_credentials_cannot_enable_live_provider():
    settings = Settings(
        GEMINI_API_KEY="fictional-legacy-key",
        LIVE_TEXT_API_KEY="fictional-text-key",
        LIVE_TEXT_MODEL="fictional-text-model",
        LIVE_TEXT_FREE_TIER_ATTESTED=True,
        LIVE_API_KEY=None,
        LIVE_MODEL=None,
        LIVE_FREE_TIER_ATTESTED=False,
    )
    assert settings.LIVE_API_KEY is None and settings.LIVE_MODEL is None
    assert not any(
        name.startswith(("GEMINI_", "GCP_", "LIVE_TEXT_"))
        for name in type(settings).model_fields
    )
    with pytest.raises(LiveRuntimeUnavailable):
        LiveProviderConfig.from_settings(settings)


@pytest.mark.parametrize(
    "model",
    [
        "gemini-3.8-live",
        "fictional_model.v1",
        "A" + "x" * 127,
    ],
)
def test_model_syntax_accepts_identifiers_without_asserting_entitlement(model):
    settings = Settings(
        LIVE_API_KEY="fictional-dedicated-key",
        LIVE_MODEL=model,
        LIVE_FREE_TIER_ATTESTED=True,
    )
    config = LiveProviderConfig.from_settings(settings)
    assert config.model == model
    assert "fictional-dedicated-key" not in repr(config)
