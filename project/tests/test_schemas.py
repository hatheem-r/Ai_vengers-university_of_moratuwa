"""Contract tests for core/schemas.py: API.md example, every generated bundle, and key rules."""
from __future__ import annotations

import copy
import json
from datetime import date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.schemas import (FACTORY_TZ, AnswerKey, ApprovalDecision, BundleExtraction, MrpToolInput,
                          MrpToolOutput, RuleCheckInput, RuleCheckOutput, RunConfig, RunResult,
                          ScheduleToolInput, ScheduleToolOutput, TraceEvent, WorkOrderPlan,
                          parse_extracted_document)

PROJECT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
BUNDLES = sorted(p for p in (PROJECT / "data" / "bundles").glob("*") if (p / "answer_key.json").exists())
bundle_ids = [b.name for b in BUNDLES]


@pytest.fixture
def example() -> dict:
    return json.loads((FIXTURES / "api_example_plan.json").read_text())


# --- WorkOrderPlan -----------------------------------------------------------

def test_api_example_validates(example):
    plan = WorkOrderPlan.model_validate(example)
    assert plan.work_orders[0].operations[1].machine_id == "LINE-2"
    assert plan.flags[0].sources[0].row == 14
    assert plan.checker and plan.checker.verdict == "pass"


def test_api_example_round_trips(example):
    plan = WorkOrderPlan.model_validate(example)
    again = WorkOrderPlan.model_validate_json(plan.model_dump_json())
    assert again == plan


def test_naive_datetime_gets_factory_offset(example):
    example["created_at"] = "2026-11-23T09:31:40"
    plan = WorkOrderPlan.model_validate(example)
    assert plan.created_at.utcoffset() == FACTORY_TZ.utcoffset(None)


@pytest.mark.parametrize("mutate, message", [
    (lambda p: p.update(surprise=1), "Extra inputs are not permitted"),
    (lambda p: p.update(system_id="X"), "system_id"),
    (lambda p: p["flags"][0].update(type="other"), "type"),
    (lambda p: p["work_orders"].append(copy.deepcopy(p["work_orders"][0])), "duplicate wo_id"),
    (lambda p: p["shortages"][0].update(affected_work_orders=["WO-999"]), "unknown work orders"),
    (lambda p: p["work_orders"][0].update(days_late=0), "is_late"),
    (lambda p: p["work_orders"][0]["operations"].reverse(), "increasing seq"),
    (lambda p: p["material_requirements"][0].update(net_shortage_qty=-1), "greater than or equal"),
    (lambda p: p["checker"].update(revisions_used=2), "less than or equal"),
])
def test_invalid_plans_are_rejected(example, mutate, message):
    mutate(example)
    with pytest.raises(ValidationError, match=message):
        WorkOrderPlan.model_validate(example)


def test_plan_json_schema_is_exportable():
    schema = WorkOrderPlan.model_json_schema()
    assert "work_orders" in schema["required"]


# --- Generated data ---------------------------------------------------------------

def test_bundles_found():
    assert len(BUNDLES) >= 24, f"expected the 24 generated bundles under {PROJECT / 'data' / 'bundles'}"


@pytest.mark.parametrize("bundle", BUNDLES, ids=bundle_ids)
def test_answer_key_validates(bundle):
    key = AnswerKey.model_validate_json((bundle / "answer_key.json").read_text())
    assert key.bundle_id == bundle.name
    # The scoring view of an answer key is a valid WorkOrderPlan once the extras are dropped.
    plan = key.model_dump(mode="json", exclude={"scoring_notes", "ot_work_centres"})
    plan["system_id"] = "human"
    for f in plan["flags"]:
        f.pop("required")
    for po in plan["suggested_pos"]:
        po.pop("expected_usable_date", None)
    WorkOrderPlan.model_validate(plan)


@pytest.mark.parametrize("bundle", BUNDLES, ids=bundle_ids)
def test_extraction_key_validates_and_round_trips(bundle):
    raw = json.loads((bundle / "extraction_key.json").read_text())
    ext = BundleExtraction.model_validate(raw)
    dumped = ext.model_dump(mode="json", exclude_unset=True)  # ERP columns a document lacks stay absent
    for orig, out in zip(raw["documents"], dumped["documents"]):
        orig = copy.deepcopy(orig)
        orig["fields"].pop("lines", None)  # customer POs repeat lines inside fields
        assert out == orig, orig["document"]


@pytest.mark.parametrize("bundle", BUNDLES, ids=bundle_ids)
def test_extraction_covers_every_input_file(bundle):
    ext = BundleExtraction.model_validate_json((bundle / "extraction_key.json").read_text())
    inputs = {p.name for p in (bundle / "inputs").iterdir()}
    assert {d.document for d in ext.documents} <= inputs


def test_extracted_document_dispatches_on_doc_type():
    doc = parse_extracted_document({
        "document": "stock_on_hand_odoo.xlsx", "doc_type": "stock_report",
        "fields": {"as_of": "2026-12-05", "system": "odoo"},
        "lines": [{"item_code": "FAB-PIQ-NVY", "uom": "kg", "on_hand": 10.5, "reserved": 2}],
    })
    assert type(doc).__name__ == "StockReportDocument"
    assert doc.lines[0].physical_qty == 10.5 and doc.lines[0].allocated_qty == 2
    with pytest.raises(ValidationError):
        parse_extracted_document({"document": "x.pdf", "doc_type": "unknown_type", "fields": {}})


# --- Tool models -------------------------------------------------------------------

def test_tool_models_accept_bundle_data():
    """Tool inputs can be built from extraction models without renaming fields."""
    ext = BundleExtraction.model_validate_json((PROJECT / "data/bundles/dev_A_S1_clean/extraction_key.json").read_text())
    bom = ext.of_type("bill_of_materials")[0]
    cap = ext.of_type("capacity_shift_plan")[0]
    policy = ext.of_type("factory_policy")[0]
    po = ext.of_type("customer_po")[0]
    key = AnswerKey.model_validate_json((PROJECT / "data/bundles/dev_A_S1_clean/answer_key.json").read_text())

    MrpToolInput(planning_start=key.planning_start,
                 demands=[{"wo_id": "WO-001", "item_code": po.lines[0].item_code, "qty": po.lines[0].qty,
                           "uom": "pcs", "need_by": key.planning_start}],
                 bom=bom.lines, stock=[{"item_code": "FAB-PIQ-NVY", "uom": "kg", "on_hand_qty": 100}])
    MrpToolOutput(material_requirements=key.material_requirements, shortages=key.shortages,
                  material_ready={"WO-001": date(2026, 12, 1)})
    ScheduleToolInput(planning_start=key.planning_start, work_centres=cap.lines, jobs=[{
        "wo_id": "WO-001", "item_code": po.lines[0].item_code, "qty": po.lines[0].qty, "due_date": date(2026, 12, 20),
        "operations": [{"seq": 10, "operation": "Cutting", "work_centre": "CUT-01", "std_min_per_unit": 0.9}]}])
    wo = key.work_orders[0]
    out = ScheduleToolOutput(status="feasible", scheduled=[{
        "wo_id": wo.wo_id, "planned_start": wo.planned_start, "planned_finish": wo.planned_finish,
        "is_late": wo.is_late, "days_late": wo.days_late, "operations": wo.operations}])
    assert out.scheduled[0].operations == wo.operations
    plan = WorkOrderPlan.model_validate({**key.model_dump(include=set(WorkOrderPlan.model_fields)),
                                         "system_id": "M-S", "flags": [], "suggested_pos": []})
    RuleCheckInput(plan=plan, policy=policy.fields, work_centres=cap.lines)
    result = RuleCheckOutput()
    assert result.passed and result.model_dump()["passed"] is True


# --- Run contracts ------------------------------------------------------------------

@pytest.mark.parametrize("mode, profile, expected", [
    ("baseline", "mix", "B"), ("baseline", "sonnet", "B"),
    ("multi_agent", "sonnet", "M-S"), ("multi_agent", "mix", "M-mix"),
])
def test_system_id(mode, profile, expected):
    assert RunConfig(mode=mode, model_profile=profile).system_id == expected


def test_run_config_defaults_match_budgets():
    c = RunConfig(mode="multi_agent")
    assert (c.budget.max_llm_calls, c.budget.max_tokens, c.budget.max_seconds) == (40, 1_500_000, 600)
    assert c.auto_approve is False


def test_reject_needs_notes():
    with pytest.raises(ValidationError, match="notes are required"):
        ApprovalDecision(decision="reject", notes="   ")
    assert ApprovalDecision(decision="approve").notes is None
    with pytest.raises(ValidationError):
        ApprovalDecision(decision="reject", notes="x" * 2001)


def test_run_result_rules(example):
    with pytest.raises(ValidationError):
        RunResult(run_id="r", status="failed")
    with pytest.raises(ValidationError):
        RunResult(run_id="r", status="awaiting_approval")
    ok = RunResult(run_id="r", status="awaiting_approval", plan=example, plan_version=1)
    assert ok.plan.bundle_id == "A_S3_typical"


def test_trace_event_matches_api_example_lines():
    lines = [
        '{"run_id":"run_20261123_093000_a1b2","seq":14,"ts":"2026-11-23T09:30:41+05:30","type":"tool_call","agent":"planner","item":null,"model":null,"tool":"mrp_tool","input_tokens":null,"cached_input_tokens":null,"output_tokens":null,"latency_ms":212,"cost_usd":0,"from_agent":null,"to_agent":null,"summary":"mrp_tool: 14 components, 1 shortage (FAB-PIQ-NVY 244.8 kg)","payload":null,"payload_ref":"payloads/14.json"}',
        '{"run_id":"run_20261123_093000_a1b2","seq":19,"ts":"2026-11-23T09:31:05+05:30","type":"handoff","agent":"planner","item":null,"model":null,"tool":null,"input_tokens":null,"cached_input_tokens":null,"output_tokens":null,"latency_ms":null,"cost_usd":null,"from_agent":"planner","to_agent":"checker","summary":"Planner → Checker: plan v1","payload":{"plan_version":1},"payload_ref":null}',
    ]
    for line in lines:
        ev = TraceEvent.model_validate_json(line)
        assert json.loads(ev.model_dump_json()) == json.loads(line) | {"ts": ev.ts.isoformat()}
    with pytest.raises(ValidationError):
        TraceEvent(run_id="r", seq=1, ts=datetime.now(FACTORY_TZ), type="agent_start", agent="writer")
