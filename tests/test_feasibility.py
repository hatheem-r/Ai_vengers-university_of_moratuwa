"""Tests for the schema-independent schedule-feasibility foundation."""

from copy import deepcopy

import pytest

from evaluation.feasibility import evaluate_schedule


TZ = "+05:30"


def timestamp(hour: int, minute: int = 0) -> str:
    return f"2026-11-24T{hour:02d}:{minute:02d}:00{TZ}"


def operation(wo_id: str, machine_id: str, start: int, end: int, *, operation_id: str | None = None):
    return {
        "work_order_id": wo_id,
        "machine_id": machine_id,
        "operation_id": operation_id or f"{wo_id}-{machine_id}",
        "start": timestamp(start),
        "end": timestamp(end),
    }


@pytest.fixture
def valid_operations():
    return [operation("WO-001", "CUT-01", 8, 10), operation("WO-002", "CUT-01", 10, 12)]


@pytest.fixture
def factory_inputs():
    return {
        "machine_capacities": {"CUT-01": 1, "LINE-2": 2},
        "downtime": {"CUT-01": [{"start": timestamp(12), "end": timestamp(13)}]},
        "shift_windows": {"*": [{"start": timestamp(8), "end": timestamp(17)}]},
        "material_ready": {"WO-001": timestamp(8), "WO-002": timestamp(8)},
    }


def types(violations):
    return [violation["type"] for violation in violations]


def test_valid_schedule_has_no_violations(valid_operations, factory_inputs):
    assert evaluate_schedule(valid_operations, **factory_inputs) == []


def test_adjacent_operations_do_not_overlap(valid_operations):
    assert evaluate_schedule(valid_operations, machine_capacities={"CUT-01": 1}) == []


def test_overlapping_operations_on_single_capacity_machine_are_reported():
    violations = evaluate_schedule(
        [operation("WO-001", "CUT-01", 8, 11), operation("WO-002", "CUT-01", 10, 12)],
        machine_capacities={"CUT-01": 1},
    )
    assert types(violations) == ["machine_overlap"]
    violation = violations[0]
    assert violation["machine_id"] == "CUT-01"
    assert violation["work_order_ids"] == ["WO-001", "WO-002"]
    assert violation["timestamps"] == {"start": timestamp(10), "end": timestamp(12)}


def test_explicit_multi_capacity_machine_allows_parallel_operations():
    assert evaluate_schedule(
        [operation("WO-001", "LINE-2", 8, 10), operation("WO-002", "LINE-2", 8, 10)],
        machine_capacities={"LINE-2": 2},
    ) == []


def test_machine_capacity_excess_is_reported_when_explicit_capacity_is_exceeded():
    violations = evaluate_schedule(
        [
            operation("WO-001", "LINE-2", 8, 11),
            operation("WO-002", "LINE-2", 8, 11),
            operation("WO-003", "LINE-2", 9, 10),
        ],
        machine_capacities={"LINE-2": 2},
    )
    assert types(violations) == ["machine_capacity_exceeded"]
    assert violations[0]["capacity"] == 2
    assert violations[0]["work_order_ids"] == ["WO-001", "WO-002", "WO-003"]


def test_operation_during_downtime_is_reported():
    violations = evaluate_schedule(
        [operation("WO-001", "CUT-01", 9, 11)],
        downtime={"CUT-01": [{"start": timestamp(10), "end": timestamp(12)}]},
    )
    assert types(violations) == ["downtime_overlap"]
    assert violations[0]["timestamps"]["downtime_start"] == timestamp(10)


def test_operation_touching_downtime_boundary_is_allowed():
    assert evaluate_schedule(
        [operation("WO-001", "CUT-01", 8, 10)],
        downtime={"CUT-01": [{"start": timestamp(10), "end": timestamp(12)}]},
    ) == []


def test_material_delay_is_reported():
    violations = evaluate_schedule(
        [operation("WO-001", "CUT-01", 8, 10)], material_ready={"WO-001": timestamp(9)}
    )
    assert types(violations) == ["material_unavailable"]
    assert violations[0]["timestamps"]["material_ready_at"] == timestamp(9)


def test_material_available_at_start_is_allowed():
    assert evaluate_schedule(
        [operation("WO-001", "CUT-01", 8, 10)], material_ready={"WO-001": timestamp(8)}
    ) == []


def test_shift_violation_is_reported():
    violations = evaluate_schedule(
        [operation("WO-001", "CUT-01", 7, 9)],
        shift_windows={"CUT-01": [{"start": timestamp(8), "end": timestamp(17)}]},
    )
    assert types(violations) == ["outside_shift_hours"]


def test_adjacent_shift_windows_cover_an_operation():
    assert evaluate_schedule(
        [operation("WO-001", "CUT-01", 8, 17)],
        shift_windows={"CUT-01": [{"start": timestamp(8), "end": timestamp(12)}, {"start": timestamp(12), "end": timestamp(17)}]},
    ) == []


def test_default_shift_calendar_is_used_when_machine_has_no_override():
    violations = evaluate_schedule(
        [operation("WO-001", "CUT-01", 17, 18)],
        shift_windows={"*": [{"start": timestamp(8), "end": timestamp(17)}]},
    )
    assert types(violations) == ["outside_shift_hours"]


def test_machine_specific_calendar_overrides_default_calendar():
    assert evaluate_schedule(
        [operation("WO-001", "CUT-01", 17, 18)],
        shift_windows={"*": [{"start": timestamp(8), "end": timestamp(17)}], "CUT-01": [{"start": timestamp(17), "end": timestamp(18)}]},
    ) == []


@pytest.mark.parametrize(
    "operation_change",
    [
        {"work_order_id": None},
        {"machine_id": ""},
        {"start": "not-a-time"},
        {"start": "2026-11-24T08:00:00"},
        {"end": timestamp(8)},
    ],
)
def test_malformed_operation_data_is_a_structured_validation_error(operation_change):
    candidate = operation("WO-001", "CUT-01", 8, 10)
    candidate.update(operation_change)
    violations = evaluate_schedule([candidate])
    assert types(violations) == ["validation_error"]
    assert violations[0]["type"] == "validation_error"


def test_missing_required_operation_fields_are_reported():
    violations = evaluate_schedule([{"work_order_id": "WO-001"}])
    assert types(violations) == ["validation_error"]
    assert "machine_id" in violations[0]["message"]


@pytest.mark.parametrize("capacity", [0, -1, 1.0, True, "2"])
def test_invalid_machine_capacity_is_reported(capacity):
    violations = evaluate_schedule([], machine_capacities={"CUT-01": capacity})
    assert types(violations) == ["validation_error"]


def test_malformed_calendar_and_downtime_intervals_are_reported():
    violations = evaluate_schedule(
        [],
        downtime={"CUT-01": [{"start": timestamp(9)}]},
        shift_windows={"CUT-01": [{"start": timestamp(17), "end": timestamp(8)}]},
    )
    assert types(violations) == ["validation_error", "validation_error"]


def test_timezone_offset_mismatch_stops_timing_checks():
    violations = evaluate_schedule(
        [operation("WO-001", "CUT-01", 8, 10)],
        downtime={"CUT-01": [{"start": "2026-11-24T09:00:00+00:00", "end": "2026-11-24T10:00:00+00:00"}]},
    )
    assert types(violations) == ["validation_error"]
    assert violations[0]["field"] == "timestamps"


def test_invalid_top_level_inputs_are_reported():
    violations = evaluate_schedule("not operations", material_ready=[])
    assert types(violations) == ["validation_error"]
    assert violations[0]["field"] == "operations"


def test_multiple_violation_types_are_all_returned():
    violations = evaluate_schedule(
        [operation("WO-001", "CUT-01", 7, 11), operation("WO-002", "CUT-01", 8, 10)],
        downtime={"CUT-01": [{"start": timestamp(9), "end": timestamp(10)}]},
        material_ready={"WO-001": timestamp(8)},
        shift_windows={"CUT-01": [{"start": timestamp(8), "end": timestamp(17)}]},
    )
    assert types(violations) == [
        "machine_overlap",
        "downtime_overlap",
        "outside_shift_hours",
        "material_unavailable",
        "downtime_overlap",
    ]
    for violation in violations:
        assert {"type", "machine_id", "work_order_ids", "timestamps"} <= violation.keys()


def test_inputs_are_not_modified(valid_operations, factory_inputs):
    operations_before = deepcopy(valid_operations)
    factory_before = deepcopy(factory_inputs)
    evaluate_schedule(valid_operations, **factory_inputs)
    assert valid_operations == operations_before
    assert factory_inputs == factory_before
