import pytest

from evaluation.metrics import (
    due_date_accuracy,
    evaluate_quality,
    field_level_f1,
    flag_scores,
    material_requirements_accuracy,
    schedule_feasibility_score,
    shortage_scores,
    suggested_po_coverage,
)


def test_field_f1_counts_correct_missing_and_extra_fields():
    score = field_level_f1({"po": {"qty": 10, "date": "2026-01-01"}, "extra": 1}, {"po": {"qty": 10, "date": "2026-01-02"}})
    assert score == {"precision": pytest.approx(1 / 3), "recall": pytest.approx(1 / 2), "f1": pytest.approx(0.4)}


def test_empty_extraction_is_only_perfect_against_explicit_empty_key():
    assert field_level_f1({}, {})["f1"] == 1
    with pytest.raises(ValueError):
        field_level_f1(None, {})


def test_material_accuracy_uses_explicit_unit_conversion():
    predicted = [{"item_code": "FAB", "required_qty": 1000, "uom": "g"}]
    expected = [{"item_code": "FAB", "required_qty": 1, "uom": "kg"}]
    assert material_requirements_accuracy(predicted, expected, unit_conversions={"g": ("kg", 0.001)}) == 1


def test_material_accuracy_aggregates_duplicate_item_lines():
    predicted = [
        {"item_code": "FAB", "required_qty": 0.4, "uom": "kg"},
        {"item_code": "FAB", "required_qty": 0.6, "uom": "kg"},
    ]
    assert material_requirements_accuracy(predicted, [{"item_code": "FAB", "required_qty": 1, "uom": "kg"}]) == 1


def test_shortage_f1_and_recall_include_false_and_missed_shortages():
    score = shortage_scores(
        [{"item_code": "A", "shortfall_qty": 2, "uom": "pcs"}, {"item_code": "X", "shortfall_qty": 1, "uom": "pcs"}],
        [{"item_code": "A", "shortfall_qty": 2, "uom": "pcs"}, {"item_code": "B", "shortfall_qty": 1, "uom": "pcs"}],
    )
    assert score == {"precision": 0.5, "recall": 0.5, "f1": 0.5, "missed": 1}


def test_po_coverage_requires_sufficient_quantity_and_timely_date():
    shortages = [{"item_code": "A", "shortfall_qty": 10, "uom": "pcs", "need_by": "2026-11-25"}]
    assert suggested_po_coverage([{"item_code": "A", "qty": 10, "uom": "pcs", "need_by": "2026-11-25"}], shortages) == 1
    assert suggested_po_coverage([{"item_code": "A", "qty": 9, "uom": "pcs", "need_by": "2026-11-24"}], shortages) == 0
    assert suggested_po_coverage([{"item_code": "A", "qty": 10, "uom": "pcs", "need_by": "2026-11-26"}], shortages) == 0


@pytest.mark.parametrize(("count", "expected"), [(0, 1), (1, 0.75), (3, 0.25), (4, 0), (8, 0)])
def test_q5_reduces_by_quarter_per_hard_violation(count, expected):
    assert schedule_feasibility_score([{}] * count) == expected


def test_due_date_accuracy_uses_one_working_day_tolerance_and_late_f1():
    predicted = [
        {"wo_id": "A", "planned_finish": "2026-11-30", "is_late": True},
        {"wo_id": "B", "planned_finish": "2026-12-01", "is_late": False},
    ]
    expected = [
        {"wo_id": "A", "planned_finish": "2026-11-27", "is_late": True},
        {"wo_id": "B", "planned_finish": "2026-11-27", "is_late": False},
    ]
    score = due_date_accuracy(predicted, expected)
    assert score == {"finish_accuracy": 0.5, "late_f1": 1.0, "score": 0.75}


def test_due_date_accuracy_accepts_configured_factory_holidays():
    predicted = [{"wo_id": "A", "planned_finish": "2026-12-01", "is_late": False}]
    expected = [{"wo_id": "A", "planned_finish": "2026-11-27", "is_late": False}]
    assert due_date_accuracy(predicted, expected)["finish_accuracy"] == 0
    assert due_date_accuracy(predicted, expected, holidays=["2026-11-30"])["finish_accuracy"] == 1


def test_zero_unit_conversion_factor_is_rejected():
    with pytest.raises(ValueError, match="greater than zero"):
        material_requirements_accuracy(
            [{"item_code": "A", "required_qty": 1, "uom": "g"}],
            [{"item_code": "A", "required_qty": 1, "uom": "kg"}],
            unit_conversions={"g": ("kg", 0)},
        )


def test_flags_match_one_to_one_and_require_evidence():
    expected = [{"type": "conflict", "sources": [{"document": "po.pdf", "field": "qty"}]}]
    predicted = [
        {"type": "conflict", "needs_human": True, "sources": [{"document": "po.pdf"}]},
        {"type": "conflict", "needs_human": True, "sources": [{"document": "po.pdf"}]},
    ]
    score = flag_scores(predicted, expected)
    assert score["recall"] == 1
    assert score["false_flags"] == 1


def test_flag_without_correct_source_gets_no_credit():
    score = flag_scores(
        [{"type": "shortage", "sources": [{"document": "wrong.csv"}]}],
        [{"type": "shortage", "sources": [{"document": "stock.csv", "field": "on_hand"}]}],
    )
    assert score["recall"] == 0
    assert score["false_flags"] == 1


def test_silently_accepted_conflict_gets_zero_flag_credit_and_q9_gate():
    score = flag_scores(
        [{"type": "conflict", "needs_human": False, "sources": [{"field": "qty"}]}],
        [{"type": "conflict", "sources": [{"field": "qty"}]}],
    )
    assert score["recall"] == 0
    assert score["q9_gate_triggered"] is True
    assert score["silently_accepted_conflicts"] == 1


def _perfect_quality_inputs():
    return {
        "predicted_extraction": {"qty": 10},
        "expected_extraction": {"qty": 10},
        "predicted_materials": [{"item_code": "MAT", "required_qty": 2, "uom": "pcs"}],
        "expected_materials": [{"item_code": "MAT", "required_qty": 2, "uom": "pcs"}],
        "predicted_shortages": [{"item_code": "MAT", "shortfall_qty": 1, "uom": "pcs"}],
        "expected_shortages": [{"item_code": "MAT", "shortfall_qty": 1, "uom": "pcs", "need_by": "2026-11-25"}],
        "predicted_pos": [{"item_code": "MAT", "qty": 1, "uom": "pcs", "need_by": "2026-11-25"}],
        "schedule_violations": [],
        "predicted_work_orders": [{"wo_id": "WO-1", "planned_finish": "2026-11-25", "is_late": False}],
        "expected_work_orders": [{"wo_id": "WO-1", "planned_finish": "2026-11-25", "is_late": False}],
        "predicted_flags": [{"type": "shortage", "needs_human": False, "sources": [{"field": "on_hand"}]}],
        "expected_flags": [{"type": "shortage", "sources": [{"field": "on_hand"}]}],
        "schema_valid": True,
        "run_success": True,
        "within_budget": True,
    }


def test_perfect_integrated_evaluation_scores_100_and_passes():
    result = evaluate_quality(**_perfect_quality_inputs())
    assert result["pqs"] == 100
    assert result["passed"] is True


def test_missed_shortage_lowers_score_and_forces_failure():
    inputs = _perfect_quality_inputs()
    inputs["predicted_shortages"] = []
    result = evaluate_quality(**inputs)
    assert result["pqs"] < 100
    assert result["passed"] is False
    assert "Missed shortages detected." in result["fail_reasons"]


def test_schedule_violation_lowers_q5_and_forces_failure():
    inputs = _perfect_quality_inputs()
    inputs["schedule_violations"] = [{"type": "machine_overlap"}]
    result = evaluate_quality(**inputs)
    assert result["metrics"]["q5_schedule_feasibility"] == 0.75
    assert result["passed"] is False


def test_silent_compliance_breach_forces_failure():
    inputs = _perfect_quality_inputs()
    inputs["predicted_flags"] = []
    inputs["expected_flags"] = [{"type": "compliance_breach", "sources": [{"field": "overtime"}]}]
    result = evaluate_quality(**inputs)
    assert result["metrics"]["q9_gate_triggered"] is True
    assert result["passed"] is False
    assert "Planted compliance breach was silently accepted." in result["fail_reasons"]
