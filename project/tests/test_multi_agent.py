"""Multi-agent graph tests. No API key needed: every agent runs on a fake chat model
(multi_agent/fakes.py) and the tools are the stubs, on the sample bundle dev_A_S1_clean."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from core import runner
from core.schemas import (AnswerKey, ApprovalDecision, Budget, MrpToolOutput, RunConfig, ScheduleToolOutput,
                          TraceEvent, WorkOrderPlan, parse_extracted_document)
from multi_agent import fakes, tools
from multi_agent.context import null_ctx
from multi_agent.intake import doc_type_catalogue, document_content, extract_document, list_documents

BUNDLE = Path(__file__).resolve().parents[1] / "data" / "bundles" / "dev_A_S1_clean"
pytestmark = pytest.mark.skipif(not BUNDLE.is_dir(), reason="sample bundle dev_A_S1_clean not generated")


@pytest.fixture
def key() -> dict:
    return json.loads((BUNDLE / "extraction_key.json").read_text())


@pytest.fixture
def answer() -> AnswerKey:
    return AnswerKey.model_validate_json((BUNDLE / "answer_key.json").read_text())


@pytest.fixture(autouse=True)
def stub_tools(monkeypatch):
    monkeypatch.setenv("WOP_STUB_TOOLS", "1")
    yield
    fakes.uninstall()
    for name in tools.SPECS:
        tools.set_tool(name, None)


_n = 0


def run_id() -> str:
    global _n
    _n += 1
    return f"run_test_{_n:03d}"


def start(cfg: RunConfig = None, events: list = None):
    events = [] if events is None else events
    rid = run_id()
    return rid, runner.start_run(rid, BUNDLE, cfg or RunConfig(mode="multi_agent"), events.append), events


# --- Intake ---------------------------------------------------------------------------

def test_document_content_reads_pdf_and_xlsx():
    pdf = document_content(BUNDLE / "inputs" / "customer_po_NWO-PO-2408.pdf")
    assert "NWO-PO-2408" in pdf[-1]["text"] and all(b["type"] == "text" for b in pdf)  # text layer, not a scan
    xlsx = document_content(BUNDLE / "inputs" / "capacity_shift_plan.xlsx")[-1]["text"]
    assert "### Sheet: Work centres" in xlsx and "CUT-01" in xlsx and "2026-12-07" in xlsx


def test_doc_type_catalogue_covers_every_type():
    cat = doc_type_catalogue()
    for t in ("customer_po", "bill_of_materials", "stock_report", "capacity_shift_plan", "factory_policy", "other"):
        assert f"- {t}\n" in cat
    assert "po_number: str" in cat and "delivery_date?: date | null" in cat


def test_extract_document_with_fake_model_matches_key(key):
    model = fakes.FakeChatModel(responder=fakes.intake_responder(key), calls=[])
    for doc in key["documents"]:
        got = extract_document(BUNDLE / "inputs" / doc["document"], model=model)
        assert got == parse_extracted_document(doc)


def test_extract_document_repairs_once_then_falls_back():
    replies = iter([AIMessage(content="not json"), AIMessage(content='{"doc_type": "customer_po", "fields": {}}'),
                    AIMessage(content="still not json"), AIMessage(content='{"doc_type": "routing", "lines": [{}]}')])
    model = fakes.FakeChatModel(responder=lambda m: next(replies), calls=[])
    path = BUNDLE / "inputs" / "maintenance_log.xlsx"
    doc = extract_document(path, model=model)       # 2 calls: invalid, repair invalid -> fallback
    assert doc.doc_type == "other" and doc.issues and "schema_invalid" in doc.issues[0]
    doc = extract_document(path, model=model)       # next 2 replies: invalid then invalid routing
    assert doc.doc_type == "other" and len(model.calls) == 4


def test_list_documents_skips_hidden_files(tmp_path):
    (tmp_path / ".DS_Store").write_text("x")
    (tmp_path / "a.pdf").write_bytes(b"%PDF")
    assert [p.name for p in list_documents(tmp_path)] == ["a.pdf"]


# --- stub tools --------------------------------------------------------------------------

def test_stub_tools_match_answer_key_materials(key, answer):
    docs = key["documents"]
    mrp_in, sched_in = fakes.build_tool_inputs(docs)
    ctx = null_ctx()
    mrp = tools.run_tool(ctx, "planner", "mrp_tool", mrp_in)
    assert isinstance(mrp, MrpToolOutput)
    got = {m.item_code: (m.required_qty, m.net_shortage_qty) for m in mrp.material_requirements}
    want = {m.item_code: (m.required_qty, m.net_shortage_qty) for m in answer.material_requirements}
    assert got == want
    sched = tools.run_tool(ctx, "planner", "schedule_tool", sched_in)
    assert isinstance(sched, ScheduleToolOutput) and sched.status == "feasible"
    assert sched.scheduled[0].operations[0].end == answer.work_orders[0].operations[0].end  # cutting: same formula


def test_tool_override_is_used(key):
    fakes.install(extraction_key=key)
    seen = []
    tools.set_tool("rule_check", lambda inp: (seen.append(inp), {"violations": []})[1])
    _, r, events = start()
    assert r.status == "awaiting_approval" and len(seen) == 1
    tc = [e for e in events if e.type == "tool_call" and e.tool == "rule_check"][0]
    assert tc.payload["stub"] is False


# --- graph end to end ----------------------------------------------------------------------

def test_end_to_end_on_sample_bundle(key, answer):
    fakes.install(extraction_key=key)
    rid, r, events = start()
    assert r.status == "awaiting_approval" and r.error is None and r.plan_version == 1
    plan = WorkOrderPlan.model_validate(r.plan.model_dump(mode="json"))  # round-trips as the API will serve it
    assert plan.system_id == "M-mix" and plan.bundle_id == "dev_A_S1_clean"
    assert plan.planning_start == answer.planning_start
    wo, ans = plan.work_orders[0], answer.work_orders[0]
    assert (wo.item_code, wo.qty, wo.due_date, wo.customer_po) == (ans.item_code, ans.qty, ans.due_date, ans.customer_po)
    assert plan.checker and plan.checker.verdict == "pass"

    # every document read once, in parallel branches, under the intake agent
    items = [e.item for e in events if e.type == "agent_end" and e.agent == "intake" and e.item]
    assert sorted(items) == sorted(p.name for p in (BUNDLE / "inputs").iterdir() if not p.name.startswith("."))
    handoffs = [(e.from_agent, e.to_agent) for e in events if e.type == "handoff"]
    assert handoffs == [("intake", "planner"), ("planner", "checker"), ("checker", "approval")]
    assert {e.agent for e in events if e.type in ("agent_start", "agent_end")} == {"intake", "planner", "checker"}
    assert [e.type for e in events][-1] == "interrupt"
    assert {e.tool for e in events if e.type == "tool_call"} == {"mrp_tool", "schedule_tool", "rule_check"}
    assert all(isinstance(e, TraceEvent) for e in events)

    r = runner.resume_run(rid, ApprovalDecision(decision="approve"), events.append)
    assert r.status == "completed" and r.plan_version == 1 and events[-1].type == "run_end"


def test_checker_sends_back_once_then_passes(key):
    fake = fakes.install(extraction_key=key, checker_verdicts=["revise", "pass"])
    _, r, events = start()
    assert r.status == "awaiting_approval" and r.plan_version == 2
    assert r.plan.checker.verdict == "pass" and r.plan.checker.revisions_used == 1
    handoffs = [(e.from_agent, e.to_agent) for e in events if e.type == "handoff"]
    assert ("checker", "planner") in handoffs and handoffs[-1] == ("checker", "approval")
    second_planner_prompt = [c for c in fake["planner"].calls if "Checker feedback" in c[1].content]
    assert second_planner_prompt, "checker issues must reach the planner"


def test_second_checker_failure_goes_to_approval_with_open_issues(key):
    fakes.install(extraction_key=key, checker_verdicts=["revise", "revise", "revise"])
    _, r, events = start()
    assert r.status == "awaiting_approval" and r.plan_version == 2  # only one revision
    assert r.plan.checker.verdict == "fail_after_revision" and r.plan.checker.open_issues
    assert sum(e.type == "checker_verdict" for e in events) == 2


def test_reject_with_notes_replans_then_approve(key):
    fake = fakes.install(extraction_key=key)
    rid, r, events = start()
    r = runner.resume_run(rid, ApprovalDecision(decision="reject", notes="No overtime on LINE-1."), events.append)
    assert r.status == "awaiting_approval" and r.plan_version == 2
    assert "Human notes applied." in r.plan.assumptions
    assert any("No overtime on LINE-1." in c[1].content for c in fake["planner"].calls)
    assert ("approval", "planner") in [(e.from_agent, e.to_agent) for e in events if e.type == "handoff"]
    assert sum(e.type == "interrupt" for e in events) == 2
    r = runner.resume_run(rid, ApprovalDecision(decision="approve"), events.append)
    assert r.status == "completed" and r.plan_version == 2


def test_auto_approve_completes(key):
    fakes.install(extraction_key=key)
    _, r, _ = start(RunConfig(mode="multi_agent", auto_approve=True))
    assert r.status == "completed"


def test_sonnet_profile_sets_system_id(key):
    fakes.install(extraction_key=key)
    _, r, events = start(RunConfig(mode="multi_agent", model_profile="sonnet"))
    assert r.plan.system_id == "M-S"
    assert {e.model for e in events if e.type == "agent_start" and e.agent == "intake"} == {"claude-sonnet-5-5"}


def test_budget_exceeded_fails_cleanly(key):
    fakes.install(extraction_key=key)
    _, r, events = start(RunConfig(mode="multi_agent", budget=Budget(max_llm_calls=5)))
    assert r.status == "failed" and r.error.code == "budget_exceeded"
    assert events[-1].type == "run_end"
