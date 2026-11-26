"""Deterministic schedule-feasibility checks for the AI Vengers evaluator.

Temporary input contract
========================
``evaluate_schedule`` accepts flattened operations because ``WorkOrderPlan``
stores them inside work orders. An integration adapter can flatten the final
Pydantic models without changing this evaluator.

``operations`` is an iterable of mappings. Each mapping must contain
``work_order_id``, ``machine_id``, ``start`` and ``end``. Timestamps are ISO
8601 strings or timezone-aware ``datetime`` instances. ``operation_id`` is
optional and is included in reports when supplied.

``machine_capacities`` maps machine IDs to positive integer concurrent
capacities. A missing machine uses capacity one, as required for the ordinary
single-capacity-machine case; an explicitly configured capacity always wins.
``downtime`` and ``shift_windows`` map a machine ID (or ``"*"`` as a default)
to iterable ``{"start": ..., "end": ...}`` intervals. Shift windows are
explicit calendar intervals, never assumed factory hours. ``material_ready``
maps a work-order ID to the timestamp at which all materials required for that
work order are available.

All timestamps across supplied inputs must have the same UTC offset. This is a
deliberately conservative temporary rule until the factory calendar schema
defines its canonical timezone. The evaluator reports malformed input as
structured validation violations and does not compare timestamps after a
validation failure.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime
from numbers import Integral
from typing import Any


def _violation(
    violation_type: str,
    *,
    machine_id: str | None = None,
    work_order_ids: Iterable[str] = (),
    timestamps: Mapping[str, str] | None = None,
    **details: Any,
) -> dict[str, Any]:
    """Build one stable violation record shared by every check."""
    return {
        "type": violation_type,
        "machine_id": machine_id,
        "work_order_ids": list(work_order_ids),
        "timestamps": dict(timestamps or {}),
        **details,
    }


def _parse_timestamp(value: object, field: str, violations: list[dict[str, Any]]) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            result = None
    else:
        result = None

    if result is None or result.tzinfo is None or result.utcoffset() is None:
        violations.append(
            _violation("validation_error", field=field, message="Expected a timezone-aware ISO 8601 timestamp.")
        )
        return None
    return result


def _parse_interval(
    raw: object, field: str, violations: list[dict[str, Any]], *, machine_id: str | None = None
) -> tuple[datetime, datetime] | None:
    if not isinstance(raw, Mapping):
        violations.append(_violation("validation_error", machine_id=machine_id, field=field, message="Expected an interval mapping."))
        return None
    if "start" not in raw or "end" not in raw:
        violations.append(_violation("validation_error", machine_id=machine_id, field=field, message="Interval requires start and end."))
        return None
    start = _parse_timestamp(raw["start"], f"{field}.start", violations)
    end = _parse_timestamp(raw["end"], f"{field}.end", violations)
    if start is None or end is None:
        return None
    if end <= start:
        violations.append(
            _violation("validation_error", machine_id=machine_id, field=field, message="Interval end must be after start.")
        )
        return None
    return start, end


def _normalise_windows(
    raw_windows: object, name: str, violations: list[dict[str, Any]]
) -> dict[str, list[tuple[datetime, datetime]]]:
    if not isinstance(raw_windows, Mapping):
        violations.append(_violation("validation_error", field=name, message="Expected a mapping of machine IDs to intervals."))
        return {}
    parsed: dict[str, list[tuple[datetime, datetime]]] = {}
    for machine_id, intervals in raw_windows.items():
        if not isinstance(machine_id, str) or not machine_id:
            violations.append(_violation("validation_error", field=name, message="Machine IDs must be non-empty strings."))
            continue
        if isinstance(intervals, (str, bytes)) or not isinstance(intervals, Iterable):
            violations.append(_violation("validation_error", machine_id=machine_id, field=name, message="Expected an iterable of intervals."))
            continue
        valid: list[tuple[datetime, datetime]] = []
        for index, interval in enumerate(intervals):
            parsed_interval = _parse_interval(interval, f"{name}.{machine_id}[{index}]", violations, machine_id=machine_id)
            if parsed_interval is not None:
                valid.append(parsed_interval)
        parsed[machine_id] = valid
    return parsed


def _merge(intervals: Iterable[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """Merge overlapping or adjacent availability windows."""
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _overlaps(left: tuple[datetime, datetime], right: tuple[datetime, datetime]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _is_within(interval: tuple[datetime, datetime], windows: Iterable[tuple[datetime, datetime]]) -> bool:
    return any(window_start <= interval[0] and interval[1] <= window_end for window_start, window_end in _merge(windows))


def evaluate_schedule(
    operations: object,
    *,
    machine_capacities: object | None = None,
    downtime: object | None = None,
    material_ready: object | None = None,
    shift_windows: object | None = None,
) -> list[dict[str, Any]]:
    """Return deterministic hard-feasibility violations for a proposed schedule.

    The result is sorted by input validation order and then by machine/start
    time. It does not calculate Q5 itself because Q5's final score depends on
    the yet-to-be-finalised answer-key and feasibility integration contracts.
    """
    violations: list[dict[str, Any]] = []
    if isinstance(operations, (str, bytes)) or not isinstance(operations, Iterable):
        return [_violation("validation_error", field="operations", message="Expected an iterable of operation mappings.")]

    machine_capacities = {} if machine_capacities is None else machine_capacities
    downtime = {} if downtime is None else downtime
    material_ready = {} if material_ready is None else material_ready
    shift_windows = {} if shift_windows is None else shift_windows

    if not isinstance(machine_capacities, Mapping):
        violations.append(_violation("validation_error", field="machine_capacities", message="Expected a mapping."))
        machine_capacities = {}
    capacities: dict[str, int] = {}
    for machine_id, capacity in machine_capacities.items():
        if not isinstance(machine_id, str) or not machine_id or isinstance(capacity, bool) or not isinstance(capacity, Integral) or capacity < 1:
            violations.append(_violation("validation_error", field="machine_capacities", message="Capacity must be a positive integer for a non-empty machine ID."))
        else:
            capacities[machine_id] = int(capacity)

    downtime_by_machine = _normalise_windows(downtime, "downtime", violations)
    shifts_by_machine = _normalise_windows(shift_windows, "shift_windows", violations)

    if not isinstance(material_ready, Mapping):
        violations.append(_violation("validation_error", field="material_ready", message="Expected a work-order to timestamp mapping."))
        material_ready = {}
    ready_by_work_order: dict[str, datetime] = {}
    for work_order_id, ready_at in material_ready.items():
        if not isinstance(work_order_id, str) or not work_order_id:
            violations.append(_violation("validation_error", field="material_ready", message="Work-order IDs must be non-empty strings."))
            continue
        parsed_ready_at = _parse_timestamp(ready_at, f"material_ready.{work_order_id}", violations)
        if parsed_ready_at is not None:
            ready_by_work_order[work_order_id] = parsed_ready_at

    normalised_operations: list[dict[str, Any]] = []
    for index, raw_operation in enumerate(operations):
        field = f"operations[{index}]"
        if not isinstance(raw_operation, Mapping):
            violations.append(_violation("validation_error", field=field, message="Expected an operation mapping."))
            continue
        missing = [name for name in ("work_order_id", "machine_id", "start", "end") if name not in raw_operation]
        if missing:
            violations.append(_violation("validation_error", field=field, message=f"Missing required fields: {', '.join(missing)}."))
            continue
        work_order_id = raw_operation["work_order_id"]
        machine_id = raw_operation["machine_id"]
        if not isinstance(work_order_id, str) or not work_order_id or not isinstance(machine_id, str) or not machine_id:
            violations.append(_violation("validation_error", field=field, message="work_order_id and machine_id must be non-empty strings."))
            continue
        interval = _parse_interval(raw_operation, field, violations, machine_id=machine_id)
        if interval is None:
            continue
        normalised_operations.append(
            {
                "work_order_id": work_order_id,
                "machine_id": machine_id,
                "operation_id": raw_operation.get("operation_id"),
                "start": interval[0],
                "end": interval[1],
            }
        )

    timestamps = [entry["start"] for entry in normalised_operations] + [entry["end"] for entry in normalised_operations]
    timestamps += [instant for intervals in downtime_by_machine.values() for interval in intervals for instant in interval]
    timestamps += [instant for intervals in shifts_by_machine.values() for interval in intervals for instant in interval]
    timestamps += list(ready_by_work_order.values())
    if timestamps and len({timestamp.utcoffset() for timestamp in timestamps}) != 1:
        violations.append(_violation("validation_error", field="timestamps", message="All timestamps must use the same UTC offset."))
    if violations:
        return violations

    operations_by_machine: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for operation in normalised_operations:
        operations_by_machine[operation["machine_id"]].append(operation)

    for machine_id in sorted(operations_by_machine):
        machine_operations = sorted(
            operations_by_machine[machine_id], key=lambda operation: (operation["start"], operation["end"], operation["work_order_id"])
        )
        capacity = capacities.get(machine_id, 1)
        active: list[dict[str, Any]] = []
        for operation in machine_operations:
            active = [other for other in active if other["end"] > operation["start"]]
            if len(active) >= capacity:
                type_ = "machine_overlap" if capacity == 1 else "machine_capacity_exceeded"
                affected = [other["work_order_id"] for other in active] + [operation["work_order_id"]]
                violations.append(
                    _violation(
                        type_,
                        machine_id=machine_id,
                        work_order_ids=affected,
                        timestamps={"start": operation["start"].isoformat(), "end": operation["end"].isoformat()},
                        capacity=capacity,
                        operation_ids=[other["operation_id"] for other in active] + [operation["operation_id"]],
                    )
                )
            active.append(operation)

        downtime_windows = downtime_by_machine.get(machine_id, downtime_by_machine.get("*", []))
        shift_windows_for_machine = shifts_by_machine.get(machine_id, shifts_by_machine.get("*"))
        for operation in machine_operations:
            interval = (operation["start"], operation["end"])
            operation_timestamps = {"start": operation["start"].isoformat(), "end": operation["end"].isoformat()}
            for downtime_start, downtime_end in downtime_windows:
                if _overlaps(interval, (downtime_start, downtime_end)):
                    violations.append(
                        _violation(
                            "downtime_overlap",
                            machine_id=machine_id,
                            work_order_ids=[operation["work_order_id"]],
                            timestamps={**operation_timestamps, "downtime_start": downtime_start.isoformat(), "downtime_end": downtime_end.isoformat()},
                            operation_ids=[operation["operation_id"]],
                        )
                    )
            if shift_windows_for_machine is not None and not _is_within(interval, shift_windows_for_machine):
                violations.append(
                    _violation(
                        "outside_shift_hours",
                        machine_id=machine_id,
                        work_order_ids=[operation["work_order_id"]],
                        timestamps=operation_timestamps,
                        operation_ids=[operation["operation_id"]],
                    )
                )
            ready_at = ready_by_work_order.get(operation["work_order_id"])
            if ready_at is not None and operation["start"] < ready_at:
                violations.append(
                    _violation(
                        "material_unavailable",
                        machine_id=machine_id,
                        work_order_ids=[operation["work_order_id"]],
                        timestamps={**operation_timestamps, "material_ready_at": ready_at.isoformat()},
                        operation_ids=[operation["operation_id"]],
                    )
                )

    return violations
