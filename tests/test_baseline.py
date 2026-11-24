"""Baseline end-to-end tests with a scripted model (no API calls). Every tool runs for real."""

import json
from functools import partial

import pytest
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

import project.baseline.__main__ as cli
from project.baseline.agent import create_session, finalize_plan, run_baseline
from project.baseline.prompts import SECTIONS, build_system_prompt, build_user_message
from project.core.schemas import Budgets, RunConfig, WorkOrderPlan
from project.tools.registry import TOOL_NAMES
from tests.conftest import ScriptedChatModel, ai
from tests.test_mrp import polo_input
from tests.test_rules import base_plan


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "dev_A_S1_clean"
    (root / "inputs").mkdir(parents=True)
    (root / "inputs" / "orders.csv").write_text("customer_po,item,qty,due\nPO-4471,POLO-PIQ-NVY,2400,2026-12-08\n")
    (root / "inputs" / "bom.csv").write_text("parent,component,qty_per,uom,wastage\nPOLO-PIQ-NVY,FAB-PIQ-NVY,0.25,kg,2\n")
    (root / "answer_key.json").write_text("{}")  # hidden: must never reach the agent
    return root


SCHEDULE_ARGS = {
    "planning_start": "2026-11-24",
    "work_orders": [{"wo_id": "WO-001", "item_code": "POLO-PIQ-NVY", "qty": 2400, "due_date": "2026-12-08",
                     "earliest_start": "2026-11-26",
                     "routing": [{"seq": 10, "operation": "Cutting", "machine_ids": ["CUT-01"], "hours": 12},
                                 {"seq": 20, "operation": "Sewing", "machine_ids": ["LINE-2"], "hours": 50}]}],
    "machines": [{"machine_id": "CUT-01", "operations": ["Cutting"]}, {"machine_id": "LINE-2", "operations": ["Sewing"]}],
}


def submitted_plan():
    plan = base_plan()
    plan.update(system_id="M-S", bundle_id="wrong", created_at="2020-01-01T00:00:00+05:30")
    return plan


def full_script():
    return [
        ai(tool_calls=[("read_document", {"file_name": "orders.csv"}), ("read_document", {"file_name": "bom.csv"})]),
        ai(tool_calls=[("mrp_tool", polo_input())]),
        ai(tool_calls=[("schedule_tool", SCHEDULE_ARGS)]),
        ai(tool_calls=[("rule_check", {"plan": base_plan(), "customer_orders": [
            {"customer_po": "PO-4471", "item_code": "POLO-PIQ-NVY", "qty": 2400, "due_date": "2026-12-08",
             "source": {"document": "orders.csv", "field": "qty", "row": 2}}]})]),
        ai(tool_calls=[("WorkOrderPlan", submitted_plan())]),
    ]


def run(bundle, script, events, **config):
    model = ScriptedChatModel(script=script, seen=[])
    result, session = run_baseline("run_test", bundle, RunConfig(mode="baseline", **config), events.append,
                                   planning_start=None, model=model)
    return result, session, model


def test_full_run_reaches_approval_with_a_finalized_plan(bundle, events):
    result, session, model = run(bundle, full_script(), events)
    assert result.status == "awaiting_approval" and result.error is None
    plan = result.plan
    assert isinstance(plan, WorkOrderPlan)
    assert (plan.system_id, plan.bundle_id, plan.checker) == ("B", "dev_A_S1_clean", None)
    assert plan.created_at.year >= 2026 and session.plan_version == 1

    types = [e.type for e in events]
    assert types[:2] == ["run_start", "agent_start"] and types[-3:] == ["agent_end", "handoff", "interrupt"]
    assert sorted({e.tool for e in events if e.type == "tool_call"}) == sorted(TOOL_NAMES)
    assert not any(e.payload and e.payload.get("is_error") for e in events if e.type == "tool_call")
    assert sum(e.type == "llm_call" for e in events) == 5
    assert [e.seq for e in events] == list(range(1, len(events) + 1))

    start = events[0].payload
    assert start["model"] == "claude-sonnet-5-5" and start["system_id"] == "B" and len(start["prompt_version"]) == 12
    assert set(start["prompt_drafts"]) <= set(SECTIONS)
    end = next(e for e in events if e.type == "agent_end")
    assert end.latency_ms is None and end.payload["duration_ms"] >= 0  # not double-counted in latency sums
    assert events[-1].agent == "approval" and "plan v1" in events[-1].summary


def test_agent_sees_combined_prompt_and_only_input_documents(bundle, events):
    _, _, model = run(bundle, full_script(), events)
    system, user = model.seen[0][0], model.seen[0][1]
    assert isinstance(system, SystemMessage) and isinstance(user, HumanMessage)
    for heading in ("Part 1: Reading the documents", "Part 2: Planning", "Part 3: Checking", "The plan you submit"):
        assert heading in system.content
    assert "<!--" not in system.content and "{intake}" not in system.content
    assert "- bom.csv (csv" in user.content and "- orders.csv (csv" in user.content
    assert "answer_key" not in user.content and "Planning start: not given" in user.content
    # The tool results reached the model (MRP shortage of 244.8 kg)
    mrp_msg = next(m for m in model.seen[2] if isinstance(m, ToolMessage) and m.name == "mrp_tool")
    assert json.loads(mrp_msg.content)["shortages"][0]["shortfall_qty"] == 244.8


def test_auto_approve_completes_the_run(bundle, events):
    result, _, _ = run(bundle, full_script(), events, auto_approve=True)
    assert result.status == "completed"
    assert [e.type for e in events][-2:] == ["human_decision", "run_end"]
    assert events[-1].payload["status"] == "completed" and events[-2].payload["auto"] is True


def test_budget_hit_fails_cleanly(bundle, events):
    result, _, model = run(bundle, full_script(), events, budgets=Budgets(max_llm_calls=2))
    assert result.status == "failed" and result.error.code == "budget_exceeded" and result.plan is None
    assert model.calls == 2
    assert [e.type for e in events][-3:] == ["error", "agent_end", "run_end"]
    assert events[-1].payload["status"] == "failed" and events[-1].payload["llm_calls"] == 2


def test_no_plan_after_one_nudge_is_schema_invalid(bundle, events):
    result, _, _ = run(bundle, [ai("Here is my plan in prose."), ai("Still prose.")], events)
    assert result.status == "failed" and result.error.code == "schema_invalid"


def test_missing_bundle_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        create_session("r", tmp_path / "nope", RunConfig(mode="baseline"), lambda e: None)


def test_finalize_overrides_bookkeeping_only():
    plan = WorkOrderPlan.model_validate(submitted_plan())
    out = finalize_plan(plan, "A_S3_typical")
    assert (out.bundle_id, out.system_id) == ("A_S3_typical", "B")
    assert out.work_orders == plan.work_orders and out.flags == plan.flags


def test_prompt_parts_and_user_message(tmp_path):
    p = build_system_prompt()
    assert p.text == build_system_prompt().text and p.sha256 == build_system_prompt().sha256  # deterministic, cacheable
    for name in SECTIONS:
        (tmp_path / f"{name}.md").write_text(f"<!-- note -->\n{name.upper()} TEXT")
    (tmp_path / "baseline.md").write_text("A {intake} B {planner} C {checker} D {output_rules}")
    q = build_system_prompt(tmp_path)
    assert q.text == "A INTAKE TEXT B PLANNER TEXT C CHECKER TEXT D OUTPUT_RULES TEXT" and q.drafts == ()
    assert "Planning start: 2026-11-24" in build_user_message("b", "- x.csv", __import__("datetime").date(2026, 11, 24))


def test_cli_writes_run_folder(bundle, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-used")
    model = ScriptedChatModel(script=full_script(), seen=[])
    monkeypatch.setattr(cli, "run_baseline", partial(run_baseline, model=model))
    code = cli.main(["--bundle", str(bundle), "--planning-start", "2026-11-24", "--runs-dir", str(tmp_path / "runs")])
    assert code == 0
    (run_dir,) = (tmp_path / "runs").iterdir()
    meta = json.loads((run_dir / "meta.json").read_text())
    assert meta["status"] == "awaiting_approval" and meta["llm_calls"] == 5 and meta["tool_calls"] == 5
    assert meta["cost_usd"] > 0
    WorkOrderPlan.model_validate_json((run_dir / "plan_v1.json").read_text())
    assert "Status: awaiting_approval" in capsys.readouterr().out


def test_cli_needs_api_key(monkeypatch, bundle):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(cli, "load_dotenv", lambda: None)
    assert cli.main(["--bundle", str(bundle)]) == 2
