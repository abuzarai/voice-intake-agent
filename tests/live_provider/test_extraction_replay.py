"""Semantic assertions on the real ``propose_draft`` payload.

Two layers. Each fixture checks that the model filled what the caller said and
left the rest null. Each case then compares the Urdu, English and mixed drafts
against each other, so a language-specific comprehension gap is named rather
than hidden in a golden file.
"""

import json
from pathlib import Path

import pytest

from evals.scoring import absent, amount_values
from tests.live_provider.harness import propose_draft

pytestmark = pytest.mark.provider

FIXTURES = Path(__file__).parent / "fixtures"

MATRIX = {
    "family": ("family_urdu", "family_english", "family_mixed"),
    "property": ("property_urdu", "property_english", "property_mixed"),
    "criminal": ("criminal_urdu", "criminal_english", "criminal_mixed"),
}
ADVERSARIAL = ("injection", "refusal", "partial_refusal", "correction")
ALL_FIXTURES = tuple(name for names in MATRIX.values() for name in names) + ADVERSARIAL

# Presence of these must agree across languages. ``previous_action`` and
# ``documentation`` are excluded: a model may record "no documents" in one
# telling and leave the field null in another, and that is not a comprehension
# gap. The per-fixture assertions cover those two directly.
CORE_FIELDS = (
    "alias_name",
    "issue_category",
    "issue_description",
    "parties",
    "location",
    "timeline",
    "financial_impact",
    "desired_outcome",
)


def load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def facts_of(draft):
    return {getattr(key, "value", key): value for key, value in draft.facts.items()}


async def draft_for(config, cache, name):
    if name not in cache:
        cache[name] = await propose_draft(config, load(name)["turns"])
    return cache[name]


def domains(expect):
    if "legal_domain" in expect:
        return {expect["legal_domain"]}
    return set(expect["legal_domain_any"])


def languages(expect):
    # Roman Urdu with English nouns carries no script signal, so "mixed" and
    # "urdu" are both defensible labels and Gemini returns each on different
    # runs of the same fixture. Mixed fixtures accept either; the English and
    # Urdu fixtures stay exact.
    if "primary_language_any" in expect:
        return set(expect["primary_language_any"])
    return {expect["primary_language"]}


def assert_amount(text, expected, require_first):
    values = amount_values(text)
    assert expected in values, f"expected {expected} in {text!r}, parsed {values}"
    if require_first and len(values) > 1:
        assert values[0] == expected, f"superseded amount came first: {text!r}"


@pytest.fixture(scope="session")
def cache():
    return {}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ALL_FIXTURES)
async def test_extraction_matches_fixture(provider_config, cache, name):
    expect = load(name)["expect"]
    draft = await draft_for(provider_config, cache, name)
    facts = facts_of(draft)

    dropped = [
        field for field in expect.get("facts_present", []) if absent(facts.get(field))
    ]
    assert not dropped, f"stated facts were dropped: {dropped}"

    invented = {
        field: facts[field]
        for field in expect.get("facts_absent", [])
        if not absent(facts.get(field))
    }
    assert not invented, f"facts were invented: {invented}"

    assert draft.analysis.primary_language.value in languages(expect)
    assert draft.analysis.legal_domain.value in domains(expect)
    assert draft.analysis.urgency.value in expect["urgency_any"]

    if "amount" in expect:
        assert_amount(
            facts["financial_impact"],
            expect["amount"],
            expect.get("require_first", False),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("case", MATRIX)
async def test_cross_language_draft_agrees(provider_config, cache, case):
    comparable = {}
    for name in MATRIX[case]:
        draft = await draft_for(provider_config, cache, name)
        facts = facts_of(draft)
        comparable[name] = {
            "legal_domain": draft.analysis.legal_domain.value,
            "urgency": draft.analysis.urgency.value,
            "present": {field for field in CORE_FIELDS if not absent(facts.get(field))},
            "amount": amount_values(facts.get("financial_impact")),
        }

    legal = {name: item["legal_domain"] for name, item in comparable.items()}
    assert len(set(legal.values())) == 1, f"legal_domain diverged: {legal}"

    urgency = {name: item["urgency"] for name, item in comparable.items()}
    # Urgency is a severity calibration, not a comprehension fact. Gemini reads
    # the same account as medium in two languages and high or low in the third
    # often enough that exact equality reports run-to-run variance as a
    # language gap. One band apart is tolerated; two bands (low against high)
    # still fail. The exact labels are printed so a real bias stays visible.
    ranks = {level: rank for rank, level in enumerate(("low", "medium", "high"))}
    spread = max(ranks[level] for level in urgency.values()) - min(
        ranks[level] for level in urgency.values()
    )
    assert spread <= 1, f"urgency diverged: {urgency}"

    populated = {name: frozenset(item["present"]) for name, item in comparable.items()}
    assert len(set(populated.values())) == 1, f"populated facts diverged: {populated}"

    amounts = {
        name: item["amount"][0] for name, item in comparable.items() if item["amount"]
    }
    if amounts:
        assert len(set(amounts.values())) == 1, f"amount diverged: {amounts}"
