"""Convert the human Excel template to a schema-neutral WorkOrderPlan mapping.

The output uses API.md field names but is not presented as a validated final
schema. Callers must validate ``result['plan']`` with Hatheem's Pydantic model
when it becomes available. Workbook reading uses openpyxl lazily so importing
the evaluation package does not require spreadsheet dependencies.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


TABLE_SHEETS = {
    "Work Orders": "work_orders",
    "Operations": "operations",
    "Materials": "material_requirements",
    "Shortages": "shortages",
    "Suggested POs": "suggested_pos",
    "Flags": "flags",
}


def _serialise(value: object) -> object:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _rows(sheet: Any, *, header_row: int = 5) -> list[dict[str, Any]]:
    headers = [cell.value for cell in sheet[header_row]]
    if not headers or any(not isinstance(header, str) or not header for header in headers):
        raise ValueError(f"{sheet.title} row {header_row} must contain complete headers.")
    records = []
    for values in sheet.iter_rows(min_row=header_row + 1, max_col=len(headers), values_only=True):
        if all(value is None or value == "" for value in values):
            continue
        records.append({header: _serialise(value) for header, value in zip(headers, values)})
    return records


def _required(record: dict[str, Any], fields: tuple[str, ...], context: str) -> None:
    missing = [field for field in fields if record.get(field) in (None, "")]
    if missing:
        raise ValueError(f"{context} missing required values: {', '.join(missing)}.")


def _json_value(value: object, default: object, context: str) -> object:
    if value in (None, ""):
        return default
    if not isinstance(value, str):
        raise ValueError(f"{context} must contain JSON text.")
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {context}: {exc.msg}.") from None


def _boolean(value: object, context: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise ValueError(f"{context} must be TRUE or FALSE.")


def convert_workbook(path: Path) -> dict[str, Any]:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("openpyxl is required to convert the human workbook.") from exc

    workbook = load_workbook(path, data_only=True, read_only=True)
    required_sheets = {"Session", *TABLE_SHEETS}
    missing_sheets = sorted(required_sheets - set(workbook.sheetnames))
    if missing_sheets:
        raise ValueError(f"Workbook is missing sheets: {', '.join(missing_sheets)}.")

    session_sheet = workbook["Session"]
    session = {
        str(session_sheet.cell(row=row, column=1).value): _serialise(session_sheet.cell(row=row, column=2).value)
        for row in range(4, 17)
        if session_sheet.cell(row=row, column=1).value
    }
    for field in ("participant_id", "bundle_id", "started_at", "finished_at"):
        if session.get(field) in (None, ""):
            raise ValueError(f"Session is missing {field}.")

    extracted = {target: _rows(workbook[sheet]) for sheet, target in TABLE_SHEETS.items()}
    work_orders_by_id: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(extracted["work_orders"]):
        _required(row, ("wo_id", "item_code", "qty", "uom", "customer_po", "priority", "due_date", "planned_start", "planned_finish", "is_late", "days_late"), f"Work Orders row {index + 6}")
        wo_id = str(row["wo_id"])
        if wo_id in work_orders_by_id:
            raise ValueError(f"Duplicate wo_id: {wo_id}.")
        row["variant"] = _json_value(row.pop("variant_json", None), None, f"Work Orders {wo_id} variant_json")
        row["is_late"] = _boolean(row["is_late"], f"Work Orders {wo_id} is_late")
        row["operations"] = []
        work_orders_by_id[wo_id] = row

    for index, operation in enumerate(extracted.pop("operations")):
        _required(operation, ("wo_id", "seq", "operation", "machine_id", "start", "end", "qty", "uses_overtime"), f"Operations row {index + 6}")
        wo_id = str(operation.pop("wo_id"))
        if wo_id not in work_orders_by_id:
            raise ValueError(f"Operation references unknown wo_id: {wo_id}.")
        operation["uses_overtime"] = _boolean(operation["uses_overtime"], f"Operations {wo_id} uses_overtime")
        work_orders_by_id[wo_id]["operations"].append(operation)

    for shortage in extracted["shortages"]:
        shortage["affected_work_orders"] = _json_value(shortage.pop("affected_work_orders_json", None), [], "Shortages affected_work_orders_json")
    for material in extracted["material_requirements"]:
        material["for_work_orders"] = _json_value(material.pop("for_work_orders_json", None), [], "Materials for_work_orders_json")
    for flag in extracted["flags"]:
        flag["sources"] = _json_value(flag.pop("sources_json", None), [], "Flags sources_json")
        flag["needs_human"] = _boolean(flag["needs_human"], "Flags needs_human")
    for suggested_po in extracted["suggested_pos"]:
        suggested_po["expedite"] = _boolean(suggested_po["expedite"], "Suggested POs expedite")

    started = datetime.fromisoformat(str(session["started_at"]).replace("Z", "+00:00"))
    finished = datetime.fromisoformat(str(session["finished_at"]).replace("Z", "+00:00"))
    if started.tzinfo is None or finished.tzinfo is None or finished < started:
        raise ValueError("Session timestamps must be timezone-aware and ordered.")
    elapsed_minutes = (finished - started).total_seconds() / 60
    hourly_rate = session.get("hourly_rate")
    cost = None
    if hourly_rate not in (None, ""):
        if isinstance(hourly_rate, bool) or not isinstance(hourly_rate, (int, float)) or hourly_rate < 0:
            raise ValueError("hourly_rate must be a nonnegative number.")
        cost = elapsed_minutes / 60 * float(hourly_rate)

    assumptions = _json_value(session.get("assumptions_json"), [], "Session assumptions_json")
    created_at = session.get("finished_at") or datetime.now(timezone.utc).isoformat()
    plan = {
        "schema_version": "1.0",
        "bundle_id": session["bundle_id"],
        "system_id": "human",
        "created_at": created_at,
        "planning_start": session.get("planning_start"),
        "work_orders": list(work_orders_by_id.values()),
        "material_requirements": extracted["material_requirements"],
        "shortages": extracted["shortages"],
        "suggested_pos": extracted["suggested_pos"],
        "flags": extracted["flags"],
        "assumptions": assumptions,
        "rationale": session.get("rationale") or "",
        "checker": None,
    }
    return {
        "plan": plan,
        "session": {
            **session,
            "elapsed_minutes": elapsed_minutes,
            "estimated_cost": cost,
            "rate_source": session.get("rate_source"),
        },
        "schema_validated": False,
    }
