"""Deterministic Q1-Q9 metric evaluators.

This module deliberately consumes plain mappings and lists while Hatheem's
Pydantic schemas and answer-key format are unfinished. Field names follow the
current API.md WorkOrderPlan where possible. Integration code should translate
the final schemas into these records rather than putting schema assumptions in
the metric algorithms.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from math import isclose, isfinite
from numbers import Real
from typing import Any

from evaluation.score import calculate_pqs, determine_pass


def _records(name: str, value: object) -> list[Mapping[str, Any]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an explicit sequence of mappings.")
    result = list(value)
    if any(not isinstance(item, Mapping) for item in result):
        raise ValueError(f"{name} must contain only mappings.")
    return result


def _number(name: str, value: object, *, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or not isfinite(value) or value < minimum:
        raise ValueError(f"{name} must be a finite number >= {minimum}.")
    return float(value)


def _date(name: str, value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            pass
    raise ValueError(f"{name} must be an ISO 8601 date or datetime.")


def _prf(matches: int, predicted: int, expected: int) -> dict[str, float]:
    precision = matches / predicted if predicted else (1.0 if expected == 0 else 0.0)
    recall = matches / expected if expected else (1.0 if predicted == 0 else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _freeze(child)) for key, child in value.items()))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(_freeze(child) for child in value)
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("Field values must be finite.")
        return value
    return value


def _flatten_fields(value: Mapping[str, Any], prefix: str = "") -> list[tuple[str, object]]:
    flattened: list[tuple[str, object]] = []
    for key in sorted(value, key=str):
        path = f"{prefix}.{key}" if prefix else str(key)
        child = value[key]
        if isinstance(child, Mapping):
            flattened.extend(_flatten_fields(child, path))
        elif isinstance(child, Sequence) and not isinstance(child, (str, bytes)):
            for index, item in enumerate(child):
                item_path = f"{path}[{index}]"
                if isinstance(item, Mapping):
                    flattened.extend(_flatten_fields(item, item_path))
                else:
                    flattened.append((item_path, _freeze(item)))
        else:
            flattened.append((path, _freeze(child)))
    return flattened


def field_level_f1(predicted: Mapping[str, Any], expected: Mapping[str, Any]) -> dict[str, float]:
    """Q1: exact field-path/value F1, retaining duplicates by path."""
    if not isinstance(predicted, Mapping) or not isinstance(expected, Mapping):
        raise ValueError("predicted and expected extraction values must be mappings.")
    predicted_fields = Counter(_flatten_fields(predicted))
    expected_fields = Counter(_flatten_fields(expected))
    matches = sum((predicted_fields & expected_fields).values())
    return _prf(matches, sum(predicted_fields.values()), sum(expected_fields.values()))


def _conversion(unit: object, conversions: Mapping[str, object]) -> tuple[str, float]:
    if not isinstance(unit, str) or not unit:
        raise ValueError("Unit must be a non-empty string.")
    raw = conversions.get(unit)
    if raw is None:
        return unit, 1.0
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) != 2:
        raise ValueError(f"Conversion for {unit} must be (canonical_unit, factor).")
    canonical, factor = raw
    if not isinstance(canonical, str) or not canonical:
        raise ValueError(f"Canonical unit for {unit} must be a non-empty string.")
    parsed_factor = _number(f"conversion factor for {unit}", factor, minimum=0)
    if parsed_factor == 0:
        raise ValueError(f"Conversion factor for {unit} must be greater than zero.")
    return canonical, parsed_factor


def _quantities(records: object, name: str, quantity_field: str, conversions: Mapping[str, object]) -> dict[str, tuple[str, float]]:
    totals: dict[str, tuple[str, float]] = {}
    for index, record in enumerate(_records(name, records)):
        try:
            item = record["item_code"]
            quantity = record[quantity_field]
            unit = record["uom"]
        except KeyError as exc:
            raise ValueError(f"{name}[{index}] is missing {exc.args[0]}.") from None
        if not isinstance(item, str) or not item:
            raise ValueError(f"{name}[{index}].item_code must be a non-empty string.")
        canonical, factor = _conversion(unit, conversions)
        converted = _number(f"{name}[{index}].{quantity_field}", quantity) * factor
        if item in totals and totals[item][0] != canonical:
            raise ValueError(f"Incompatible canonical units for {item}.")
        totals[item] = (canonical, totals.get(item, (canonical, 0.0))[1] + converted)
    return totals


def material_requirements_accuracy(predicted: object, expected: object, *, unit_conversions: Mapping[str, object] | None = None) -> float:
    """Q2: share of expected components having exactly equal canonical quantity."""
    conversions = unit_conversions or {}
    actual = _quantities(predicted, "predicted material requirements", "required_qty", conversions)
    truth = _quantities(expected, "expected material requirements", "required_qty", conversions)
    if not truth:
        return 1.0 if not actual else 0.0
    matches = sum(
        item in actual and actual[item][0] == unit and isclose(actual[item][1], quantity, rel_tol=0, abs_tol=1e-9)
        for item, (unit, quantity) in truth.items()
    )
    return matches / len(truth)


def shortage_scores(predicted: object, expected: object, *, unit_conversions: Mapping[str, object] | None = None) -> dict[str, float | int]:
    """Q3: exact item/shortfall matching with precision, recall and F1."""
    conversions = unit_conversions or {}
    actual = _quantities(predicted, "predicted shortages", "shortfall_qty", conversions)
    truth = _quantities(expected, "expected shortages", "shortfall_qty", conversions)
    matches = sum(
        item in actual and actual[item][0] == unit and isclose(actual[item][1], quantity, rel_tol=0, abs_tol=1e-9)
        for item, (unit, quantity) in truth.items()
    )
    scores = _prf(matches, len(actual), len(truth))
    return {**scores, "missed": len(truth) - matches}


def suggested_po_coverage(predicted_pos: object, expected_shortages: object, *, unit_conversions: Mapping[str, object] | None = None) -> float:
    """Q4: fraction of shortages covered by one timely, sufficient PO each."""
    conversions = unit_conversions or {}
    pos = _records("predicted suggested POs", predicted_pos)
    shortages = _records("expected shortages", expected_shortages)
    if not shortages:
        return 1.0 if not pos else 0.0
    used: set[int] = set()
    covered = 0
    for shortage_index, shortage in enumerate(shortages):
        for required in ("item_code", "shortfall_qty", "uom", "need_by"):
            if required not in shortage:
                raise ValueError(f"expected shortages[{shortage_index}] is missing {required}.")
        shortage_unit, shortage_factor = _conversion(shortage["uom"], conversions)
        needed = _number("shortfall_qty", shortage["shortfall_qty"]) * shortage_factor
        need_by = _date("need_by", shortage["need_by"])
        for po_index, po in enumerate(pos):
            if po_index in used:
                continue
            for required in ("item_code", "qty", "uom", "need_by"):
                if required not in po:
                    raise ValueError(f"predicted suggested POs[{po_index}] is missing {required}.")
            po_unit, po_factor = _conversion(po["uom"], conversions)
            if (
                po["item_code"] == shortage["item_code"]
                and po_unit == shortage_unit
                and _number("PO qty", po["qty"]) * po_factor + 1e-9 >= needed
                and _date("PO need_by", po["need_by"]) <= need_by
            ):
                used.add(po_index)
                covered += 1
                break
    return covered / len(shortages)


def schedule_feasibility_score(violations: object) -> float:
    """Q5: subtract 0.25 per hard violation, floored at zero."""
    return max(0.0, 1.0 - 0.25 * len(_records("schedule violations", violations)))


def _working_day_distance(left: date, right: date, holidays: set[date]) -> int:
    if left == right:
        return 0
    start, end = sorted((left, right))
    distance = sum(
        1
        for offset in range(1, (end - start).days + 1)
        if (candidate := date.fromordinal(start.toordinal() + offset)).weekday() < 5 and candidate not in holidays
    )
    return distance


def due_date_accuracy(predicted: object, expected: object, *, holidays: Sequence[object] = ()) -> dict[str, float]:
    """Q6: mean of finish-date accuracy (±1 working day) and late-status F1."""
    holiday_dates = {_date("holiday", value) for value in holidays}
    actual_records = _records("predicted work orders", predicted)
    truth_records = _records("expected work orders", expected)
    actual = {record.get("wo_id"): record for record in actual_records}
    truth = {record.get("wo_id"): record for record in truth_records}
    if None in actual or None in truth or len(actual) != len(actual_records) or len(truth) != len(truth_records):
        raise ValueError("Work orders require unique wo_id values.")
    if not truth:
        finish_accuracy = 1.0 if not actual else 0.0
    else:
        correct = 0
        for wo_id, expected_order in truth.items():
            predicted_order = actual.get(wo_id)
            if predicted_order is None or "planned_finish" not in predicted_order or "planned_finish" not in expected_order:
                continue
            if _working_day_distance(_date("planned_finish", predicted_order["planned_finish"]), _date("planned_finish", expected_order["planned_finish"]), holiday_dates) <= 1:
                correct += 1
        finish_accuracy = correct / len(truth)
    predicted_late = {wo_id for wo_id, record in actual.items() if record.get("is_late") is True}
    expected_late = {wo_id for wo_id, record in truth.items() if record.get("is_late") is True}
    late = _prf(len(predicted_late & expected_late), len(predicted_late), len(expected_late))
    return {"finish_accuracy": finish_accuracy, "late_f1": late["f1"], "score": (finish_accuracy + late["f1"]) / 2}


def _source_matches(predicted: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    predicted_sources = _records("predicted flag sources", predicted.get("sources")) if "sources" in predicted else []
    expected_sources = _records("expected flag sources", expected.get("sources")) if "sources" in expected else []
    if not expected_sources:
        return False
    return any(
        (source.get("document") and source.get("document") == target.get("document"))
        or (source.get("field") and source.get("field") == target.get("field"))
        for source in predicted_sources
        for target in expected_sources
    )


def flag_scores(predicted: object, expected: object) -> dict[str, Any]:
    """Q7-Q9: one-to-one evidence-aware flag matching and safety gates."""
    actual = _records("predicted flags", predicted)
    truth = _records("expected flags", expected)
    used: set[int] = set()
    matched_expected: set[int] = set()
    silently_accepted: list[int] = []
    for expected_index, target in enumerate(truth):
        if not isinstance(target.get("type"), str):
            raise ValueError(f"expected flags[{expected_index}] requires type.")
        candidates = [
            index
            for index, flag in enumerate(actual)
            if index not in used and flag.get("type") == target["type"] and _source_matches(flag, target)
        ]
        if candidates:
            match = candidates[0]
            used.add(match)
            critical = target["type"] in {"conflict", "compliance_breach"}
            if critical and actual[match].get("needs_human") is not True:
                silently_accepted.append(expected_index)
            else:
                matched_expected.add(expected_index)
        elif target["type"] in {"conflict", "compliance_breach"}:
            silently_accepted.append(expected_index)
    recall = len(matched_expected) / len(truth) if truth else (1.0 if not actual else 0.0)
    false_flags = len(actual) - len(used)
    return {
        "recall": recall,
        "matched": len(matched_expected),
        "missed": len(truth) - len(matched_expected),
        "false_flags": false_flags,
        "q9_gate_triggered": bool(silently_accepted),
        "silently_accepted_conflicts": sum(truth[index]["type"] == "conflict" for index in silently_accepted),
        "silently_accepted_compliance_breaches": sum(
            truth[index]["type"] == "compliance_breach" for index in silently_accepted
        ),
    }


def evaluate_quality(
    *,
    predicted_extraction: Mapping[str, Any],
    expected_extraction: Mapping[str, Any],
    predicted_materials: object,
    expected_materials: object,
    predicted_shortages: object,
    expected_shortages: object,
    predicted_pos: object,
    schedule_violations: object,
    predicted_work_orders: object,
    expected_work_orders: object,
    predicted_flags: object,
    expected_flags: object,
    unit_conversions: Mapping[str, object] | None = None,
    holidays: Sequence[object] = (),
    schema_valid: bool,
    run_success: bool,
    within_budget: bool,
) -> dict[str, Any]:
    """Calculate Q1-Q9, PQS and mandatory pass/fail in one pure integration."""
    q1 = field_level_f1(predicted_extraction, expected_extraction)
    q2 = material_requirements_accuracy(predicted_materials, expected_materials, unit_conversions=unit_conversions)
    q3 = shortage_scores(predicted_shortages, expected_shortages, unit_conversions=unit_conversions)
    q4 = suggested_po_coverage(predicted_pos, expected_shortages, unit_conversions=unit_conversions)
    q5 = schedule_feasibility_score(schedule_violations)
    q6 = due_date_accuracy(predicted_work_orders, expected_work_orders, holidays=holidays)
    q7_q9 = flag_scores(predicted_flags, expected_flags)
    metrics = {
        "q1_extraction_f1": q1["f1"],
        "q2_material_reqs": q2,
        "q3_shortage_f1": q3["f1"],
        "q3_shortage_recall": q3["recall"],
        "q4_suggested_pos": q4,
        "q5_schedule_feasibility": q5,
        "q6_due_dates": q6["score"],
        "q7_flag_recall": q7_q9["recall"],
        "q8_false_flags": q7_q9["false_flags"],
        "q8_penalty": -min(2 * q7_q9["false_flags"], 10),
        "q9_gate_triggered": q7_q9["q9_gate_triggered"],
    }
    pqs = calculate_pqs(metrics, false_flags=q7_q9["false_flags"])
    passed, fail_reasons = determine_pass(
        pqs,
        schema_valid=schema_valid,
        run_success=run_success,
        within_budget=within_budget,
        hard_schedule_violations=len(_records("schedule violations", schedule_violations)),
        missed_shortages=int(q3["missed"]),
        silently_accepted_compliance_breach=q7_q9["silently_accepted_compliance_breaches"] > 0,
    )
    return {"pqs": pqs, "passed": passed, "fail_reasons": fail_reasons, "metrics": metrics}
