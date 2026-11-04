"""The verdict a run earns, kept free of the SDK so it can be tested offline."""


def pass_threshold(repeats):
    """A strict majority of attempts: one flaky attempt in three still passes."""
    return 0.5 + 0.5 / max(1, repeats)


def gate(aggregate_result, cases, baseline, parity=None):
    """Return the reasons the run fails, or an empty list.

    A case that did not pass fails the run on its own. The baseline adds
    regression reasons; it is not what decides whether the run succeeded.
    """
    reasons = [f"{case['id']}: did not pass" for case in cases if not case["passed"]]
    for divergence in parity or []:
        reasons.append(
            f"{'/'.join(divergence['triplet'])}: {divergence['check']} diverged"
        )
    if not baseline:
        return reasons
    previous = {case["id"]: case for case in baseline.get("cases", [])}
    for case in cases:
        before = previous.get(case["id"])
        if before and before.get("passed") and not case.get("passed"):
            reasons.append(f"{case['id']}: regressed from pass to fail")
    drop = (
        baseline["aggregate"].get("field_accuracy", 0)
        - aggregate_result["field_accuracy"]
    )
    if drop > 0.02:
        reasons.append(f"field_accuracy dropped {drop:.3f}")
    if aggregate_result["fact_precision"] < baseline["aggregate"].get(
        "fact_precision", 0
    ):
        reasons.append("fact_precision dropped")
    return reasons
