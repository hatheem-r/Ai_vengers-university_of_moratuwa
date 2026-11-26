"""Single-agent baseline (system B) on the shared harness. No API key: the agent runs on a
fake chat model (multi_agent/fakes.baseline_responder) with the real tools."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from baseline import agent as baseline
from core import runner
from core.schemas import ApprovalDecision, Budget, RunConfig, TraceEvent, WorkOrderPlan
from core.tracing import MemoryTrace, read_trace
from multi_agent import fakes, tools
from multi_agent.planner import PLANNER_TOOLS

BUNDLES = Path(__file__).resolve().parents[1] / "data" / "bundles"
DEV = ["dev_A_S1_clean", "dev_B_S3_typical", "dev_C_S5_messy"]
pytestmark = pytest.mark.skipif(not (BUNDLES / DEV[0]).is_dir(), reason="dev bundles not generated")

_n = 0


def rid() -> str:
    global _n
    _n += 1
    return f"run_base_{_n:03d}"


def install(bundle: str) -> dict:
    key = json.loads((BUNDLES / bundle / "extraction_key.json").read_text())
    return fakes.install(extraction_key=key)


@pytest.fixture(autouse=True)
def cleanup():
    yield
    fakes.uninstall()
    for name in tools.SPECS:
        tools.set_tool(name, None)


@pytest.mark.parametrize("bundle", DEV)
def test_baseline_returns_a_valid_plan_on_dev_bundles(bundle):
    models = install(bundle)
    trace = MemoryTrace()
    result = runner.start_run(rid(), BUNDLES / bundle, RunConfig(mode="baseline"), trace)
    assert result.status == "awaiting_approval", result.error
    plan = WorkOrderPlan.model_validate(result.plan.model_dump(mode="json"))
    assert plan.system_id == "B" and plan.bundle_id == bundle and plan.work_orders
    used = [e.tool for e in trace.events if e.type == "tool_call"]
    n_docs = len(list((BUNDLES / bundle / "inputs").iterdir()))
    assert used.count("read_document") == n_docs
    assert {"mrp_tool", "schedule_tool", "rule_check"} <= set(used)
    assert {e.agent for e in trace.events if e.type == "llm_call"} == {"agent"}
    assert trace.events[-1].type == "interrupt"
    assert len(models["agent"].calls) == sum(e.type == "llm_call" for e in trace.events)


def test_reject_with_notes_replans_and_approve_completes(tmp_path):
    install(DEV[0])
    result, run_dir = runner.run_recorded(BUNDLES / DEV[0], RunConfig(mode="baseline"), runs_root=tmp_path)
    assert result.status == "awaiting_approval" and result.plan_version == 1
    r2 = runner.resume_recorded(run_dir, ApprovalDecision(decision="reject", notes="Do not use overtime"))
    assert r2.status == "awaiting_approval" and r2.plan_version == 2
    assert "Human notes applied." in r2.plan.assumptions
    r3 = runner.resume_recorded(run_dir, ApprovalDecision(decision="approve"))
    assert r3.status == "completed" and r3.plan_version == 2
    files = {p.name for p in run_dir.iterdir()}
    assert {"trace.jsonl", "meta.json", "plan_v1.json", "plan_v2.json", "approval.json"} <= files
    events = read_trace(run_dir)
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    meta = json.loads((run_dir / "meta.json").read_text())
    assert meta["status"] == "completed" and meta["system_id"] == "B"
    assert meta["metrics"]["llm_calls"] == sum(e.type == "llm_call" for e in events)
    assert len(json.loads((run_dir / "approval.json").read_text())) == 2


def test_budget_ends_the_run_cleanly():
    install(DEV[0])
    cfg = RunConfig(mode="baseline", budget=Budget(max_llm_calls=3))
    result = runner.start_run(rid(), BUNDLES / DEV[0], cfg, MemoryTrace())
    assert result.status == "failed" and result.error.code == "budget_exceeded"


def test_bad_final_answers_fail_with_schema_invalid():
    install(DEV[0])
    fakes.install(baseline=lambda messages: fakes._json_reply({"not": "a plan"}))
    result = runner.start_run(rid(), BUNDLES / DEV[0], RunConfig(mode="baseline"), MemoryTrace())
    assert result.status == "failed" and result.error.code == "schema_invalid"


def test_read_document_stays_inside_inputs():
    """Hidden-data rule: the agent cannot read the answer key next to inputs/."""
    run = baseline._Run(bundle_id="x", inputs_dir=BUNDLES / DEV[0] / "inputs", messages=[])
    from core.harness import null_ctx
    msg = baseline._execute(null_ctx(), run, {"name": "read_document", "args": {"name": "../answer_key.json"},
                                              "id": "c1"})
    assert msg.status == "error" and "not a file in inputs" in msg.content


def test_same_tools_and_descriptions_as_the_multi_agent_planner():
    defs = {t["name"]: t for t in baseline.TOOL_DEFS}
    for name, d in zip(PLANNER_TOOLS, tools.anthropic_tool_defs(PLANNER_TOOLS)):
        assert defs[name] == d
    assert defs["rule_check"]["description"].startswith(tools.SPECS["rule_check"].description)


def test_baseline_prompt_is_built_from_the_multi_agent_prompts():
    from multi_agent.prompts import load_prompt
    prompt = baseline.system_prompt()
    assert load_prompt("planner").strip() in prompt
    assert "Every customer-PO line has a work order" in prompt and "{{" not in prompt


def test_both_systems_emit_the_same_llm_call_fields():
    install(DEV[0])
    b, m = MemoryTrace(), MemoryTrace()
    runner.start_run(rid(), BUNDLES / DEV[0], RunConfig(mode="baseline"), b)
    runner.start_run(rid(), BUNDLES / DEV[0], RunConfig(mode="multi_agent"), m)

    def keys(evs: list[TraceEvent]) -> set:
        return set().union(*({k for k, v in e.model_dump().items() if v is not None}
                             for e in evs if e.type == "llm_call"))
    assert keys(b.events) - {"item"} == keys(m.events) - {"item"}
    assert {"input_tokens", "output_tokens", "latency_ms", "model", "payload"} <= keys(b.events)
