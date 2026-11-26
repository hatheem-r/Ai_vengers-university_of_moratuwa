"""Independent PQS aggregation and pass rules for the AI Vengers planner.

Metric names follow API.md section 4.10. Plan/answer-key parsing and metric
extraction belong to a future adapter once the shared schemas are final.
Q9 must give zero credit to each affected flag before Q7 recall is computed.
"""

from collections.abc import Mapping
from math import fsum, isfinite
from numbers import Integral, Real


_METRIC_WEIGHTS = (
    ("q1_extraction_f1", 15),
    ("q2_material_reqs", 10),
    ("q3_shortage_f1", 15),
    ("q4_suggested_pos", 10),
    ("q5_schedule_feasibility", 15),
    ("q6_due_dates", 10),
    ("q7_flag_recall", 20),
)
_TOTAL_WEIGHT = sum(weight for _, weight in _METRIC_WEIGHTS)
_PASS_THRESHOLD = 80.0


def _bounded_number(name: str, value: object, upper_bound: float) -> float:
    """Require a finite real number in range; booleans are not scores."""
    message = f"{name} must be a finite number between 0 and {upper_bound}."
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(message)
    # Check the range first so even extremely large integers fail cleanly.
    if not 0 <= value <= upper_bound or not isfinite(value):
        raise ValueError(message)
    return float(value)


def _nonnegative_count(name: str, value: object) -> int:
    """Require a nonnegative integer count, excluding booleans and floats."""
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer.")
    return int(value)


def calculate_pqs(metrics: Mapping[str, float], false_flags: int = 0) -> float:
    """Return clamp(100 * weighted_sum / 95 - min(2 * false_flags, 10)).

    All seven Q1-Q7 keys in API.md section 4.10 are required, with finite
    numeric values in [0, 1]. Other entries, such as shortage recall, are
    ignored. Q7 must already include Q9's zero credit for affected flags.
    ``false_flags`` is the authoritative Q8 count supplied by the caller.

    This pure function neither modifies inputs nor rounds the result.
    Invalid or missing scoring inputs raise ValueError.
    """
    if not isinstance(metrics, Mapping):
        raise ValueError("metrics must be a mapping containing all Q1-Q7 scores.")
    missing = [name for name, _ in _METRIC_WEIGHTS if name not in metrics]
    if missing:
        raise ValueError(f"Missing required metrics: {', '.join(missing)}")

    false_flags = _nonnegative_count("false_flags", false_flags)
    weighted_sum = fsum(
        _bounded_number(name, metrics[name], 1) * weight
        for name, weight in _METRIC_WEIGHTS
    )
    penalty = min(2 * false_flags, 10)
    pqs = 100.0 * weighted_sum / _TOTAL_WEIGHT - penalty
    return max(0.0, min(100.0, pqs))


def determine_pass(
    pqs: float,
    *,
    schema_valid: bool,
    run_success: bool,
    within_budget: bool,
    hard_schedule_violations: int,
    missed_shortages: int,
    silently_accepted_compliance_breach: bool,
) -> tuple[bool, list[str]]:
    """Return (passed, failure_reasons), reporting every failed condition.

    The caller supplies outcomes from schema, runner, feasibility, shortage
    and compliance checks. They are all required: an unchecked condition
    cannot default to a pass. Counts must be nonnegative integers and status
    flags must be actual booleans. Invalid inputs raise ValueError.

    PQS uses its full precision, with an inclusive threshold of 80. A silent
    conflict resolution reduces flag credit via Q9/Q7; a silently accepted
    planted compliance breach also fails the run here regardless of PQS.
    """
    pqs = _bounded_number("pqs", pqs, 100)
    hard_schedule_violations = _nonnegative_count(
        "hard_schedule_violations", hard_schedule_violations
    )
    missed_shortages = _nonnegative_count("missed_shortages", missed_shortages)
    for name, value in (
        ("schema_valid", schema_valid),
        ("run_success", run_success),
        ("within_budget", within_budget),
        ("silently_accepted_compliance_breach", silently_accepted_compliance_breach),
    ):
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be a boolean.")

    checks = (
        (pqs >= _PASS_THRESHOLD, "PQS is below 80."),
        (schema_valid, "Plan is not schema-valid."),
        (run_success, "Run did not complete successfully."),
        (within_budget, "Run exceeded its budget."),
        (hard_schedule_violations == 0, "Hard schedule violations detected."),
        (missed_shortages == 0, "Missed shortages detected."),
        (
            not silently_accepted_compliance_breach,
            "Planted compliance breach was silently accepted.",
        ),
    )
    reasons = [reason for satisfied, reason in checks if not satisfied]
    return not reasons, reasons
