"""The verdict a run earns: a failing case fails the run, baseline or not."""

import pytest

from evals.gate import gate, pass_threshold

pytestmark = pytest.mark.unit


def case(case_id="correction", passed=True, schema_valid=True):
    return {
        "id": case_id,
        "passed": passed,
        "schema_valid": schema_valid,
        "checks": {},
        "fields": {},
        "dropped": [],
        "invented": [],
        "prose_nulls": 0,
    }


def summary(**overrides):
    aggregate = {"field_accuracy": 1.0, "fact_precision": 1.0}
    aggregate.update(overrides)
    return aggregate


def test_a_failing_case_fails_the_run_without_a_baseline():
    assert gate(summary(), [case(passed=False)], None) == ["correction: did not pass"]


def test_a_passing_case_without_a_baseline_reports_nothing():
    assert gate(summary(), [case()], None) == []


def test_a_regression_names_the_case():
    baseline = {"cases": [{"id": "correction", "passed": True}], "aggregate": summary()}
    reasons = gate(summary(), [case(passed=False)], baseline)
    assert "correction: did not pass" in reasons
    assert "correction: regressed from pass to fail" in reasons


def test_a_large_accuracy_drop_fails_the_run():
    baseline = {"cases": [], "aggregate": summary(field_accuracy=0.95)}
    reasons = gate(summary(field_accuracy=0.90), [case()], baseline)
    assert "field_accuracy dropped 0.050" in reasons


def test_a_small_accuracy_drop_is_tolerated():
    baseline = {"cases": [], "aggregate": summary(field_accuracy=0.95)}
    assert gate(summary(field_accuracy=0.94), [case()], baseline) == []


def test_precision_never_drops_quietly():
    baseline = {"cases": [], "aggregate": summary(fact_precision=0.9)}
    assert "fact_precision dropped" in gate(
        summary(fact_precision=0.8), [case()], baseline
    )


@pytest.mark.parametrize(
    ("repeats", "expected"),
    [(1, 1.0), (3, 0.6667), (5, 0.6), (10, 0.55)],
)
def test_the_threshold_is_a_strict_majority(repeats, expected):
    assert round(pass_threshold(repeats), 4) == expected


def test_two_of_three_attempts_pass():
    assert 2 / 3 >= pass_threshold(3)


def test_three_of_five_attempts_pass():
    assert 3 / 5 >= pass_threshold(5)


def test_one_of_three_attempts_does_not_pass():
    assert not 1 / 3 >= pass_threshold(3)
