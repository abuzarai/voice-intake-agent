"""Characterization of canonical analysis types during the v2 domain extraction."""

import hashlib
import json

import pytest
from pydantic import ValidationError

from app.models.intake import KeyEntities, Language, LegalAnalysis, LegalDomain, Urgency
from app.models.live_audio_draft import AudioContextProposal

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (
            KeyEntities,
            "86d2cedecd2f4e16b5d748914f131116ec0abf45eefa016bba5255fa17ee23a8",
        ),
        (
            LegalAnalysis,
            "015d3eab5568e778682a764f373a9d94561f26eccc5666eb84845982c80db031",
        ),
    ],
)
def test_canonical_schema_matches_pre_extraction_checkpoint(model, expected):
    # Derived from the checkpoint's original class ASTs, not the moved definitions.
    # Canonical serialization covers defaults, enum values, bounds and descriptions.
    encoded = json.dumps(
        model.model_json_schema(), sort_keys=True, separators=(",", ":")
    ).encode()
    assert hashlib.sha256(encoded).hexdigest() == expected


def test_audio_contract_uses_canonical_types():
    assert LegalAnalysis.model_fields["primary_language"].annotation is Language
    assert LegalAnalysis.model_fields["legal_domain"].annotation is LegalDomain
    assert LegalAnalysis.model_fields["urgency"].annotation is Urgency
    assert AudioContextProposal.model_fields["analysis"].annotation is LegalAnalysis


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_confidence_bounds_are_preserved(confidence):
    with pytest.raises(ValidationError):
        LegalAnalysis(
            primary_language="mixed",
            legal_domain="property_law",
            confidence_score=confidence,
            key_entities={},
            issue_summary="Fictional deposit dispute",
            adr_suitable=True,
            adr_reasoning="Fictional negotiation",
            urgency="low",
            urgency_reasoning="No immediate danger stated",
        )


def test_entity_defaults_are_not_shared():
    first, second = KeyEntities(), KeyEntities()
    first.parties.append("Fictional Noor")
    assert second.parties == []
