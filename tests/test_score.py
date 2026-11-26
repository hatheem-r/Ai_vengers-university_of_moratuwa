"""Tests of the scoring foundation, independent of unfinished plan schemas."""

import pytest

from evaluation.score import calculate_pqs, determine_pass


@pytest.fixture
def metrics():
    return {
        "q1_extraction_f1": 1.0,
        "q2_material_reqs": 1.0,
        "q3_shortage_f1": 1.0,
        "q4_suggested_pos": 1.0,
        "q5_schedule_feasibility": 1.0,
        "q6_due_dates": 1.0,
        "q7_flag_recall": 1.0,
    }


@pytest.fixture
def run_checks():
    return {
        "schema_valid": True,
        "run_success": True,
        "within_budget": True,
        "hard_schedule_violations": 0,
        "missed_shortages": 0,
        "silently_accepted_compliance_breach": False,
    }


def test_perfect_scores(metrics):
    assert calculate_pqs(metrics) == 100.0


def test_zero_scores(metrics):
    assert calculate_pqs(dict.fromkeys(metrics, 0.0)) == 0.0


@pytest.mark.parametrize(
    ("metric", "weight"),
    [
        ("q1_extraction_f1", 15),
        ("q2_material_reqs", 10),
        ("q3_shortage_f1", 15),
        ("q4_suggested_pos", 10),
        ("q5_schedule_feasibility", 15),
        ("q6_due_dates", 10),
        ("q7_flag_recall", 20),
    ],
)
def test_each_metric_has_its_specified_weight(metrics, metric, weight):
    scores = dict.fromkeys(metrics, 0.0)
    scores[metric] = 1.0
    assert calculate_pqs(scores) == pytest.approx(100 * weight / 95)


def test_mixed_scores_worked_example(metrics):
    metrics.update(q1_extraction_f1=0.8, q3_shortage_f1=0.5, q7_flag_recall=0.75)
    # Weighted contributions: 12 + 10 + 7.5 + 10 + 15 + 10 + 15 = 79.5.
    # Normalise to 100, then subtract four points for two false flags.
    assert calculate_pqs(metrics, false_flags=2) == pytest.approx(79.6842105263158)


@pytest.mark.parametrize(
    ("false_flags", "expected"),
    [(0, 100), (1, 98), (3, 94), (4, 92), (5, 90), (6, 90), (10**1000, 90)],
)
def test_false_flag_penalty_and_cap(metrics, false_flags, expected):
    assert calculate_pqs(metrics, false_flags=false_flags) == expected


def test_penalty_clamps_low_score_to_zero(metrics):
    assert calculate_pqs(dict.fromkeys(metrics, 0.05), false_flags=5) == 0.0


def test_calculation_is_repeatable_and_does_not_modify_inputs(metrics):
    before = metrics.copy()
    assert calculate_pqs(metrics, 2) == calculate_pqs(metrics, 2)
    assert metrics == before


def test_additional_reported_metrics_are_not_weighted(metrics):
    metrics.update(q3_shortage_recall=1.0, q9_gate_triggered=False)
    assert calculate_pqs(metrics) == 100.0


def test_q9_affected_flag_credit_is_supplied_through_q7(metrics):
    # One of two planted flags earns zero credit under Q9: Q7 recall is 1/2.
    metrics["q7_flag_recall"] = 0.5
    assert calculate_pqs(metrics) == pytest.approx(89.4736842105263)


@pytest.mark.parametrize("metric_index", range(7))
def test_missing_metric_is_rejected(metrics, metric_index):
    name = list(metrics)[metric_index]
    del metrics[name]
    with pytest.raises(ValueError, match=name):
        calculate_pqs(metrics)


def test_empty_metrics_are_rejected():
    with pytest.raises(ValueError, match="Missing required metrics"):
        calculate_pqs({})


@pytest.mark.parametrize("invalid", [None, [], "scores", 1])
def test_non_mapping_metrics_are_rejected(invalid):
    with pytest.raises(ValueError, match="mapping"):
        calculate_pqs(invalid)


@pytest.mark.parametrize("metric_index", range(7))
@pytest.mark.parametrize(
    "invalid",
    [float("nan"), float("inf"), -float("inf"), -0.01, 1.01, None, "0.5", True],
)
def test_invalid_metric_values_are_rejected(metrics, metric_index, invalid):
    name = list(metrics)[metric_index]
    metrics[name] = invalid
    with pytest.raises(ValueError, match=name):
        calculate_pqs(metrics)


def test_extremely_large_metric_is_rejected_cleanly(metrics):
    metrics["q1_extraction_f1"] = 10**1000
    with pytest.raises(ValueError, match="q1_extraction_f1"):
        calculate_pqs(metrics)


@pytest.mark.parametrize(
    "invalid", [-1, 1.5, 1.0, True, False, "2", None, float("nan"), float("inf")]
)
def test_invalid_false_flag_counts_are_rejected(metrics, invalid):
    with pytest.raises(ValueError, match="false_flags"):
        calculate_pqs(metrics, false_flags=invalid)


@pytest.mark.parametrize("pqs", [80.0, 80.000001, 100.0])
def test_passes_at_or_above_threshold(run_checks, pqs):
    assert determine_pass(pqs, **run_checks) == (True, [])


@pytest.mark.parametrize("pqs", [0.0, 79.0, 79.999999])
def test_below_threshold_fails_without_rounding(run_checks, pqs):
    assert determine_pass(pqs, **run_checks) == (False, ["PQS is below 80."])


@pytest.mark.parametrize(
    ("condition", "value", "reason"),
    [
        ("schema_valid", False, "Plan is not schema-valid."),
        ("run_success", False, "Run did not complete successfully."),
        ("within_budget", False, "Run exceeded its budget."),
        ("hard_schedule_violations", 1, "Hard schedule violations detected."),
        ("missed_shortages", 1, "Missed shortages detected."),
        (
            "silently_accepted_compliance_breach",
            True,
            "Planted compliance breach was silently accepted.",
        ),
    ],
)
def test_mandatory_failures_override_perfect_pqs(run_checks, condition, value, reason):
    run_checks[condition] = value
    assert determine_pass(100, **run_checks) == (False, [reason])


def test_all_failure_reasons_are_reported():
    passed, reasons = determine_pass(
        79,
        schema_valid=False,
        run_success=False,
        within_budget=False,
        hard_schedule_violations=2,
        missed_shortages=3,
        silently_accepted_compliance_breach=True,
    )
    assert passed is False
    assert reasons == [
        "PQS is below 80.",
        "Plan is not schema-valid.",
        "Run did not complete successfully.",
        "Run exceeded its budget.",
        "Hard schedule violations detected.",
        "Missed shortages detected.",
        "Planted compliance breach was silently accepted.",
    ]


@pytest.mark.parametrize(
    "invalid",
    [float("nan"), float("inf"), -float("inf"), -1, 101, True, "80", None, 10**1000],
)
def test_invalid_pqs_is_rejected(run_checks, invalid):
    with pytest.raises(ValueError, match="pqs"):
        determine_pass(invalid, **run_checks)


@pytest.mark.parametrize("condition", ["hard_schedule_violations", "missed_shortages"])
@pytest.mark.parametrize("invalid", [-1, 0.5, 0.0, True, None, "0", float("inf")])
def test_invalid_failure_counts_are_rejected(run_checks, condition, invalid):
    run_checks[condition] = invalid
    with pytest.raises(ValueError, match=condition):
        determine_pass(100, **run_checks)


@pytest.mark.parametrize(
    "condition",
    ["schema_valid", "run_success", "within_budget", "silently_accepted_compliance_breach"],
)
@pytest.mark.parametrize("invalid", [None, "false", 0, 1])
def test_non_boolean_status_flags_are_rejected(run_checks, condition, invalid):
    run_checks[condition] = invalid
    with pytest.raises(ValueError, match=condition):
        determine_pass(100, **run_checks)


def test_checks_must_be_supplied_explicitly():
    with pytest.raises(TypeError):
        determine_pass(100)


def test_aggregation_and_pass_rules_together(metrics, run_checks):
    metrics = dict.fromkeys(metrics, 0.8)
    pqs = calculate_pqs(metrics)
    assert determine_pass(pqs, **run_checks) == (True, [])
    penalised_pqs = calculate_pqs(metrics, false_flags=1)
    assert determine_pass(penalised_pqs, **run_checks) == (False, ["PQS is below 80."])
