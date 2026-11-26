from pathlib import Path

import pytest

from evaluation.human.human_to_plan import convert_workbook


pytest.importorskip("openpyxl", reason="human workbook conversion requires the project openpyxl dependency")


TEMPLATE = Path("evaluation/human/human_plan_template.xlsx")


def filled_workbook(tmp_path):
    from openpyxl import load_workbook

    workbook = load_workbook(TEMPLATE)
    session = workbook["Session"]
    values = {
        "participant_id": "H1", "bundle_id": "A_S1_clean", "planning_start": "2026-11-24",
        "started_at": "2026-11-24T08:00:00+05:30", "finished_at": "2026-11-24T09:30:00+05:30",
        "hourly_rate": 1000, "rate_source": "Example test fixture only", "rationale": "Due-date priority.",
        "assumptions_json": '["No substitutions"]',
    }
    for row in range(4, 17):
        key = session.cell(row, 1).value
        if key in values:
            session.cell(row, 2).value = values[key]

    headers = [cell.value for cell in workbook["Work Orders"][5]]
    order = dict(zip(headers, ["WO-1", "SKU", 10, "pcs", "PO-1", None, "v1", 1, "2026-11-30", "2026-11-24T08:00:00+05:30", "2026-11-24T10:00:00+05:30", False, 0]))
    for column, value in enumerate(order.values(), 1):
        workbook["Work Orders"].cell(6, column).value = value

    operation_values = ["WO-1", 10, "Cutting", "CUT-01", "2026-11-24T08:00:00+05:30", "2026-11-24T10:00:00+05:30", 10, False]
    for column, value in enumerate(operation_values, 1):
        workbook["Operations"].cell(6, column).value = value

    target = tmp_path / "human.xlsx"
    workbook.save(target)
    return target


def test_template_exists_and_contains_required_sheets():
    from openpyxl import load_workbook

    workbook = load_workbook(TEMPLATE, read_only=True)
    assert workbook.sheetnames == ["Session", "Work Orders", "Operations", "Materials", "Shortages", "Suggested POs", "Flags"]


def test_converter_creates_api_shaped_plan_and_session_cost(tmp_path):
    result = convert_workbook(filled_workbook(tmp_path))
    assert result["schema_validated"] is False
    assert result["plan"]["system_id"] == "human"
    assert result["plan"]["work_orders"][0]["operations"][0]["machine_id"] == "CUT-01"
    assert result["session"]["elapsed_minutes"] == 90
    assert result["session"]["estimated_cost"] == 1500


def test_converter_rejects_unknown_work_order_reference(tmp_path):
    from openpyxl import load_workbook

    path = filled_workbook(tmp_path)
    workbook = load_workbook(path)
    workbook["Operations"]["A6"] = "WO-MISSING"
    workbook.save(path)
    with pytest.raises(ValueError, match="unknown wo_id"):
        convert_workbook(path)


def test_converter_rejects_invalid_session_order(tmp_path):
    from openpyxl import load_workbook

    path = filled_workbook(tmp_path)
    workbook = load_workbook(path)
    workbook["Session"]["B8"] = "2026-11-24T07:00:00+05:30"
    workbook.save(path)
    with pytest.raises(ValueError, match="ordered"):
        convert_workbook(path)
