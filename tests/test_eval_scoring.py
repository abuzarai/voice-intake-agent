"""Deterministic scoring of a draft: units, absence, matching, aggregation."""

import pytest

from evals.scoring import (
    FACT_FIELDS,
    absent,
    aggregate,
    amount_values,
    draft_args,
    parity,
    prose_null,
    score_case,
)

pytestmark = pytest.mark.unit


def draft(**overrides):
    """A valid ``propose_draft`` call, with the facts and analysis a case needs."""
    facts = {field: None for field in FACT_FIELDS}
    facts.update(
        {
            "issue_category": "business dispute",
            "issue_description": "a partner owes money",
            "financial_impact": "five lakh rupees",
        }
    )
    facts.update(overrides.pop("facts", {}))
    analysis = {
        "primary_language": "english",
        "legal_domain": "civil_law",
        "confidence_score": 0.7,
        "key_entities": {"parties": [], "locations": [], "dates": [], "amounts": []},
        "issue_summary": "unpaid debt",
        "case_title_en": "",
        "case_title_ur": None,
        "adr_suitable": True,
        "adr_reasoning": "a money claim",
        "urgency": "low",
        "urgency_reasoning": "no deadline given",
    }
    analysis.update(overrides.pop("analysis", {}))
    return {"facts": facts, "analysis": analysis}


def events(*calls):
    """Run events holding the given function calls, in order."""
    return [
        {"type": "function_call", "name": name, "args": args} for name, args in calls
    ]


def gold(**overrides):
    expect = {
        "legal_domain_any": ["civil_law", "corporate_law"],
        "primary_language": "english",
        "urgency_any": ["low", "medium"],
        "facts_present": ["issue_category", "issue_description", "financial_impact"],
        "facts_absent": ["documentation"],
        "amount": 500000,
    }
    expect.update(overrides)
    return expect


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("five lakh rupees", [500000]),
        ("2 crore", [20000000]),
        ("پانچ لاکھ", [500000]),
        ("1.5 million", [1500000]),
        ("two lakh, then five lakh", [200000, 500000]),
        ("five hundred thousand", [500000]),
        ("fifty lakh", [5000000]),
        ("", []),
        (None, []),
    ],
)
def test_amount_values_reads_words_and_units(text, expected):
    assert amount_values(text) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, True),
        ("", True),
        ("Not provided.", True),
        ("none mentioned", True),
        ("declined to say", True),
        ("five lakh rupees", False),
    ],
)
def test_absent_treats_a_prose_null_as_absent(value, expected):
    assert absent(value) is expected


def test_prose_null_separates_prose_from_a_json_null():
    assert prose_null(None) is False
    assert prose_null("declined") is True
    assert prose_null("five lakh") is False


def test_draft_args_takes_the_last_propose_draft_call():
    first = draft()
    second = draft(facts={"issue_category": "second"})
    seen = events(
        ("interview_finished", {}), ("propose_draft", first), ("propose_draft", second)
    )
    assert draft_args(seen) == second


def test_draft_args_is_none_without_a_call():
    assert draft_args([]) is None
    assert draft_args(events(("interview_finished", {}))) is None


def test_score_case_matches_a_complete_draft():
    result = score_case(events(("propose_draft", draft())), gold())
    assert result["passed"] is True
    assert result["schema_valid"] is True
    assert result["dropped"] == []
    assert result["invented"] == []


def test_score_case_reports_a_dropped_fact():
    args = draft(facts={"issue_category": None})
    result = score_case(events(("propose_draft", args)), gold())
    assert result["dropped"] == ["issue_category"]
    assert result["passed"] is False


def test_score_case_reports_an_invented_fact():
    args = draft(facts={"documentation": "a signed contract"})
    result = score_case(events(("propose_draft", args)), gold())
    assert result["invented"] == ["documentation"]
    assert result["passed"] is False


def test_score_case_rejects_a_draft_the_validator_refuses():
    args = draft()
    args["extra"] = True
    result = score_case(events(("propose_draft", args)), gold())
    assert result["schema_valid"] is False
    assert result["checks"] == {"schema": False}
    assert result["passed"] is False


def test_score_case_counts_a_prose_null_without_failing_it():
    args = draft(facts={"documentation": "not provided"})
    result = score_case(events(("propose_draft", args)), gold())
    assert result["prose_nulls"] == 1
    assert result["passed"] is True


def test_score_case_requires_the_first_amount_when_asked():
    args = draft(facts={"financial_impact": "two lakh, corrected to five lakh"})
    expect = gold(require_first=True)
    assert score_case(events(("propose_draft", args)), expect)["passed"] is False
    assert score_case(events(("propose_draft", args)), gold())["passed"] is True


def test_score_case_fails_on_a_wrong_domain():
    result = score_case(
        events(("propose_draft", draft())), gold(legal_domain_any=["family_law"])
    )
    assert result["checks"]["legal_domain"] is False
    assert result["passed"] is False


def test_aggregate_counts_only_the_fields_a_case_scored():
    result = score_case(events(("propose_draft", draft())), gold())
    result["expected_present"] = 3
    result["expected_absent"] = 1
    summary = aggregate([result])
    assert summary["cases"] == 1
    assert summary["pass_rate"] == 1.0
    assert summary["field_accuracy"] == 1.0
    assert summary["fact_recall"] == 1.0
    assert summary["fact_precision"] == 1.0


def test_aggregate_reports_no_precision_when_nothing_was_drafted():
    missing = {
        "passed": False,
        "schema_valid": False,
        "checks": {"draft": False},
        "fields": {},
        "dropped": [],
        "invented": [],
        "prose_nulls": 0,
        "expected_present": 3,
        "expected_absent": 1,
    }
    summary = aggregate([missing])
    assert summary["schema_valid_rate"] == 0.0
    assert summary["fact_recall"] == 0.0
    assert summary["fact_precision"] == 0.0


def triplet(**overrides):
    """Three language variants that agree, for parity to compare."""
    base = {
        "legal_domain": "family_law",
        "urgency": "medium",
        "populated": ["issue_category", "issue_description"],
        "amount": [500000],
        "schema_valid": True,
    }
    base.update(overrides)
    return [
        {"id": f"family_{language}", **base}
        for language in ("urdu", "english", "mixed")
    ]


def checks_for(cases):
    return [divergence["check"] for divergence in parity(cases)]


def test_parity_accepts_variants_that_agree():
    assert parity(triplet()) == []


def test_parity_names_a_domain_divergence():
    cases = triplet()
    cases[1]["legal_domain"] = "property_law"
    assert checks_for(cases) == ["legal_domain"]


def test_parity_tolerates_one_urgency_band():
    cases = triplet()
    cases[0]["urgency"] = "low"
    cases[1]["urgency"] = "medium"
    cases[2]["urgency"] = "medium"
    assert "urgency" not in checks_for(cases)


def test_parity_names_two_urgency_bands():
    cases = triplet()
    cases[0]["urgency"] = "low"
    cases[1]["urgency"] = "high"
    cases[2]["urgency"] = "low"
    assert "urgency" in checks_for(cases)


def test_parity_names_a_populated_divergence():
    cases = triplet()
    cases[2]["populated"] = ["issue_category"]
    assert checks_for(cases) == ["populated"]


def test_parity_names_an_amount_divergence():
    cases = triplet()
    cases[1]["amount"] = [5000000]
    assert checks_for(cases) == ["amount"]


def test_parity_reports_a_triplet_it_cannot_compare():
    cases = triplet()
    cases[0]["schema_valid"] = False
    cases[1]["schema_valid"] = False
    assert checks_for(cases) == ["comparable"]


def test_parity_ignores_a_lone_variant():
    assert parity([{"id": "family_urdu", "schema_valid": True}]) == []


def test_a_dropped_fact_counts_against_accuracy():
    args = draft(facts={"parties": None})
    expect = gold(facts_present=["issue_category", "parties"])
    result = score_case(events(("propose_draft", args)), expect)
    assert result["dropped"] == ["parties"]
    assert result["fields"]["parties"] == "miss"
    result["expected_present"] = 2
    result["expected_absent"] = 1
    assert aggregate([result])["field_accuracy"] < 1.0


def test_a_contradictory_gold_is_refused():
    expect = gold(facts_present=["issue_category"], facts_absent=["issue_category"])
    result = score_case(events(("propose_draft", draft())), expect)
    assert result["gold_errors"] == ["issue_category"]
    assert result["checks"]["gold"] is False
    assert result["passed"] is False


def test_parity_ignores_a_domain_its_gold_leaves_open():
    cases = triplet()
    for case in cases:
        case["domain_open"] = True
    cases[1]["legal_domain"] = "property_law"
    assert "legal_domain" not in checks_for(cases)


def test_parity_still_names_a_domain_when_gold_is_strict():
    cases = triplet()
    cases[1]["legal_domain"] = "property_law"
    assert "legal_domain" in checks_for(cases)
