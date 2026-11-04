"""Deterministic scoring of a draft against gold; no provider, no judge."""

import re

from app.models.live_audio_draft import FACT_FIELDS
from app.services.live_audio_draft import validate_arguments

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

# The model sometimes describes a missing fact in prose ("Not provided")
# instead of sending JSON null. That is a schema-adherence defect, not an
# invented fact, so it is counted separately rather than failed.
NULL_STRINGS = {
    "",
    "none",
    "null",
    "n/a",
    "na",
    "nil",
    "unknown",
    "not applicable",
    "not provided",
    "not given",
    "not shared",
    "not stated",
    "not mentioned",
    "not specified",
    "not disclosed",
    "none mentioned",
    "none provided",
    "none given",
    "none reported",
    "no information",
    "declined",
    "declined to say",
    "refused",
    "refused to say",
    "no answer",
    "unavailable",
}

_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
    "hundred": 100,
    "ek": 1,
    "aik": 1,
    "do": 2,
    "teen": 3,
    "char": 4,
    "chaar": 4,
    "paanch": 5,
    "panch": 5,
    "chay": 6,
    "sat": 7,
    "aath": 8,
    "nau": 9,
    "das": 10,
    "bees": 20,
    "tees": 30,
    "chalees": 40,
    "pachas": 50,
    "saath": 60,
    "sattar": 70,
    "assi": 80,
    "nawway": 90,
    "ایک": 1,
    "دو": 2,
    "تین": 3,
    "چار": 4,
    "پانچ": 5,
    "چھ": 6,
    "سات": 7,
    "آٹھ": 8,
    "نو": 9,
    "دس": 10,
    "بیس": 20,
    "تیس": 30,
    "چالیس": 40,
    "پچاس": 50,
    "ساٹھ": 60,
    "ستر": 70,
    "اسی": 80,
    "نوے": 90,
    "سو": 100,
}

_UNITS = {
    "thousand": 1000,
    "hazar": 1000,
    "ہزار": 1000,
    "lakh": 100000,
    "lakhs": 100000,
    "lac": 100000,
    "لاکھ": 100000,
    "million": 1000000,
    "crore": 10000000,
    "crores": 10000000,
    "کروڑ": 10000000,
}


def absent(value):
    """Whether a fact value reports absence, in JSON or in prose."""
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    text = value.strip().rstrip(".").lower()
    return text in NULL_STRINGS or text.startswith("none ")


def prose_null(value):
    """A prose null: absent, but not sent as JSON null."""
    return isinstance(value, str) and value.strip() != "" and absent(value)


def amount_values(text):
    """Every recognisable monetary value in a free-text amount, in order.

    Number words accumulate, so "five hundred thousand" is one value rather
    than three: "hundred" scales what precedes it, a unit closes the value.
    """
    if not text:
        return []
    tokens = re.findall(
        r"\d+(?:\.\d+)?|[\w\u0600-\u06FF]+", text.lower().replace(",", "")
    )
    values = []
    current = None
    for token in tokens:
        if re.fullmatch(r"\d+(?:\.\d+)?", token):
            if current is not None:
                values.append(int(current))
            current = float(token)
        elif token in _WORDS:
            if current is None:
                current = float(_WORDS[token])
            elif token == "hundred":
                current *= 100
            else:
                current += _WORDS[token]
        elif token in _UNITS and current is not None:
            values.append(int(current * _UNITS[token]))
            current = None
    if current is not None:
        values.append(int(current))
    return values


def draft_args(events):
    """The last propose_draft call's arguments, or None when it never came."""
    for event in reversed(events or []):
        if (
            event.get("type") == "function_call"
            and event.get("name") == "propose_draft"
        ):
            return event.get("args")
    return None


def _domains(expect):
    if "legal_domain" in expect:
        return {expect["legal_domain"]}
    return set(expect["legal_domain_any"])


def _languages(expect):
    if "primary_language" in expect:
        return {expect["primary_language"]}
    return set(expect["primary_language_any"])


def proposal_args(proposal):
    """The draft arguments from a proposal the bot wrote, or None."""
    if not isinstance(proposal, dict):
        return None
    if "facts" not in proposal or "analysis" not in proposal:
        return None
    return {"facts": proposal["facts"], "analysis": proposal["analysis"]}


def score_case(events, expect, args=None):
    """Score one run against the case's gold expectation.

    The arguments come from the bot's own settled proposal when the runner
    has one; the event stream only carries a tool name, because reporting the
    arguments is opt-in on the harness side.
    """
    result = {
        "schema_valid": False,
        "prose_nulls": 0,
        "fields": {field: "unscored" for field in FACT_FIELDS},
        "dropped": [],
        "invented": [],
        "legal_domain": None,
        "urgency": None,
        "primary_language": None,
        "amount": [],
        "populated": [],
        "checks": {},
        "passed": False,
    }
    args = args if args is not None else draft_args(events)
    if args is None:
        result["checks"]["draft"] = False
        return result
    try:
        facts, analysis = validate_arguments(args)
    except (ValueError, TypeError, OverflowError):
        result["checks"]["schema"] = False
        return result

    result["schema_valid"] = True
    result["checks"]["schema"] = True

    # A field in both lists is a gold error, not a model result; say so rather
    # than letting the second list quietly overwrite the first.
    contradictory = sorted(
        set(expect.get("facts_present", [])) & set(expect.get("facts_absent", []))
    )
    if contradictory:
        result["checks"]["gold"] = False
        result["gold_errors"] = contradictory
        result["passed"] = False
        return result
    result["prose_nulls"] = sum(1 for value in facts.values() if prose_null(value))

    for field in expect.get("facts_present", []):
        if absent(facts.get(field)):
            result["dropped"].append(field)
            result["fields"][field] = "miss"
        else:
            result["fields"][field] = "match"

    for field in expect.get("facts_absent", []):
        if not absent(facts.get(field)):
            result["invented"].append(field)
            result["fields"][field] = "miss"
        else:
            result["fields"][field] = "match"

    result["populated"] = sorted(
        field for field in FACT_FIELDS if not absent(facts.get(field))
    )
    result["domain_open"] = "legal_domain_any" in expect
    result["legal_domain"] = analysis.legal_domain.value
    result["urgency"] = analysis.urgency.value
    result["primary_language"] = analysis.primary_language.value
    result["amount"] = amount_values(facts.get("financial_impact"))

    result["checks"]["dropped"] = not result["dropped"]
    result["checks"]["invented"] = not result["invented"]
    result["checks"]["legal_domain"] = result["legal_domain"] in _domains(expect)
    result["checks"]["primary_language"] = result["primary_language"] in _languages(
        expect
    )
    result["checks"]["urgency"] = result["urgency"] in expect["urgency_any"]
    if "amount" in expect:
        values = result["amount"]
        found = expect["amount"] in values
        first = (
            not expect.get("require_first")
            or not values
            or values[0] == expect["amount"]
        )
        result["checks"]["amount"] = found and first
    result["passed"] = all(result["checks"].values())
    return result


def _ratio(wrong, expected, scored):
    """The share of a fact class that survived, or 0 when nothing was drafted."""
    if not scored:
        return 0.0
    if not expected:
        return 1.0
    return round(1 - wrong / expected, 4)


TRIPLETS = (
    ("family_urdu", "family_english", "family_mixed"),
    ("property_urdu", "property_english", "property_mixed"),
    ("criminal_urdu", "criminal_english", "criminal_mixed"),
)

URGENCY_RANK = {"low": 0, "medium": 1, "high": 2}


def parity(cases):
    """Compare the language variants of one case, and name every divergence.

    A per-case score cannot see a language-specific comprehension gap: each
    variant can pass its own gold while disagreeing with its siblings. This
    compares the drafts directly. Urgency is a severity calibration rather
    than a fact, so one band apart is tolerated and two is not.
    """
    by_id = {case["id"]: case for case in cases}
    found = []
    for triplet in TRIPLETS:
        present = [by_id[name] for name in triplet if name in by_id]
        if len(present) < 2:
            continue
        usable = [case for case in present if case.get("schema_valid")]
        if len(usable) < 2:
            found.append(
                {
                    "triplet": list(triplet),
                    "check": "comparable",
                    "detail": "fewer than two variants produced a draft",
                }
            )
            continue

        # If the corpus accepts more than one domain for these cases, demanding
        # that the languages agree on one of them contradicts the gold.
        domains = {case["id"]: case["legal_domain"] for case in usable}
        if (
            not any(case.get("domain_open") for case in usable)
            and len(set(domains.values())) > 1
        ):
            found.append(
                {"triplet": list(triplet), "check": "legal_domain", "detail": domains}
            )

        ranks = {case["id"]: URGENCY_RANK.get(case["urgency"], 0) for case in usable}
        if max(ranks.values()) - min(ranks.values()) > 1:
            found.append(
                {
                    "triplet": list(triplet),
                    "check": "urgency",
                    "detail": {case["id"]: case["urgency"] for case in usable},
                }
            )

        # Only the core facts: urgency is a calibration, and
        # previous_action/documentation are legitimately recorded in one
        # telling and left null in another without being a comprehension gap.
        populated = {
            case["id"]: tuple(
                field for field in case.get("populated", []) if field in CORE_FIELDS
            )
            for case in usable
        }
        if len(set(populated.values())) > 1:
            found.append(
                {"triplet": list(triplet), "check": "populated", "detail": populated}
            )

        amounts = {
            case["id"]: case["amount"][0] for case in usable if case.get("amount")
        }
        if len(set(amounts.values())) > 1:
            found.append(
                {"triplet": list(triplet), "check": "amount", "detail": amounts}
            )
    return found


def aggregate(cases):
    """Corpus-level numbers from scored cases."""
    scored = [case for case in cases if case.get("schema_valid")]
    # A case that produced no draft has nothing to be recalled or invented,
    # so it must not count as perfect precision in the aggregate.
    expected_present = sum(case.get("expected_present", 0) for case in scored)
    expected_absent = sum(case.get("expected_absent", 0) for case in scored)
    dropped = sum(len(case.get("dropped", [])) for case in cases)
    invented = sum(len(case.get("invented", [])) for case in cases)
    matched = sum(
        1
        for case in cases
        for state in case.get("fields", {}).values()
        if state == "match"
    )
    total = sum(
        1
        for case in cases
        for state in case.get("fields", {}).values()
        if state in ("match", "miss")
    )
    return {
        "cases": len(cases),
        "passed": sum(1 for case in cases if case.get("passed")),
        "pass_rate": round(sum(1 for c in cases if c.get("passed")) / len(cases), 4)
        if cases
        else 0.0,
        "schema_valid_rate": round(len(scored) / len(cases), 4) if cases else 0.0,
        "field_accuracy": round(matched / total, 4) if total else 0.0,
        "fact_recall": _ratio(dropped, expected_present, scored),
        "fact_precision": _ratio(invented, expected_absent, scored),
        "prose_nulls": sum(case.get("prose_nulls", 0) for case in cases),
    }
