"""Single-agent baseline tests. No API key: the agent runs on a fake chat model
(baseline/fakes.py) with the real tools, on the dev bundles, through core/runner.py."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from baseline import agent as baseline_agent
from baseline import fakes
from baseline.prompt import prompt_version, system_prompt
from core import runner
from core.schemas import ApprovalDecision, Budget, RunConfig, TraceEvent, WorkOrderPlan
from multi_agent import fakes as ma_fakes
from multi_agent.intake import document_content, doc_type_catalogue
from multi_agent.planner import PLANNER_TOOLS
from multi_agent.prompts import load_prompt
from multi_agent import tools
from multi_agent.tools import anthropic_tool_defs

BUNDLES = Path(__file__).resolve().parents[1] / "data" / "bundles"
DEV = ["dev_A_S1_clean", "dev_B_S3_typical", "dev_C_S5_messy"]
pytestmark = pytest.mark.skipif(not (BUNDLES / DEV[0]).is_dir(), reason="dev bundles not generated")


def key(bundle: str) -> dict:
    return json.loads((BUNDLES / bundle / "extraction_key.json").read_text())


@pytest.fixture(autouse=True)
def clean():
    yield
    fakes.uninstall()
    ma_fakes.uninstall()
    baseline_agent._SESSIONS.clear()


def run(bundle: str, events: list, **config):
    rid = runner.new_run_id()
    return rid, runner.start_run(rid, BUNDLES / bundle, RunConfig(mode="baseline", **config), events.append)


@pytest.mark.parametrize("bundle", DEV)
def test_dev_bundles_reach_approval_with_a_valid_plan(bundle):
    fakes.install(key(bundle))
    events: list[TraceEvent] = []
    _, r = run(bundle, events)
    assert r.status == "awaiting_approval" and r.plan_version == 1
    plan = WorkOrderPlan.model_validate(r.plan.model_dump())
    assert plan.system_id == "B" and plan.bundle_id == bundle and plan.checker is None
    types = [e.type for e in events]
    assert types[:2] == ["run_start", "agent_start"] and types[-3:] == ["agent_end", "handoff", "interrupt"]
    used = {e.tool for e in events if e.type == "tool_call"}
    assert used == {"read_document", "mrp_tool", "schedule_tool", "rule_check"}
    assert not any("(stub)" in e.summary for e in events if e.type == "tool_call")  # real tools
    assert all(e.agent == "agent" for e in events if e.type in ("llm_call", "tool_call"))
    start = next(e for e in events if e.type == "agent_start")
    assert start.payload["prompt_version"] == prompt_version()


@pytest.mark.parametrize("bundle", DEV)
def test_same_inputs_give_the_same_plan_as_the_multi_agent_planner(bundle):
    """Fairness: given the same facts, both systems send byte-identical tool inputs and assemble the
    plan the same way. Tool calls go through a cache keyed by the exact input, so CP-SAT's
    tie-breaking between equally good schedules (8 workers) cannot make the comparison flaky."""
    cache: dict[tuple[str, str], object] = {}
    for name in ("mrp_tool", "schedule_tool"):
        real, _ = tools.resolve(name)

        def cached(inp, name=name, real=real):
            return cache.setdefault((name, inp.model_dump_json()), real(inp))
        tools.set_tool(name, cached)
    try:
        ma_fakes.install(extraction_key=key(bundle))
        ma = runner.start_run(runner.new_run_id(), BUNDLES / bundle, RunConfig(mode="multi_agent"), lambda e: None)
        ma_fakes.uninstall()
        fakes.install(key(bundle))
        _, b = run(bundle, [])
    finally:
        for name in ("mrp_tool", "schedule_tool"):
            tools.set_tool(name, None)
    assert ma.status == b.status == "awaiting_approval"
    assert sorted(n for n, _ in cache) == ["mrp_tool", "schedule_tool"]  # one distinct input per tool
    for field in ("planning_start", "work_orders", "material_requirements", "shortages", "suggested_pos"):
        assert getattr(b.plan, field) == getattr(ma.plan, field), field


def test_reject_replans_in_the_same_conversation_then_approve():
    bundle = "dev_A_S1_clean"
    model = fakes.install(key(bundle))
    events: list[TraceEvent] = []
    rid, r = run(bundle, events)
    calls_before = len(model.calls)
    r = runner.resume_run(rid, ApprovalDecision(decision="reject", notes="Do not use overtime on Sunday."), events.append)
    assert r.status == "awaiting_approval" and r.plan_version == 2
    assert any("Human notes applied" in a for a in r.plan.assumptions)
    resumed = model.calls[calls_before]
    assert any(isinstance(m, ToolMessage) for m in resumed)  # history kept
    assert "Do not use overtime on Sunday." in resumed[-1].content
    handoffs = [(e.from_agent, e.to_agent) for e in events if e.type == "handoff"]
    assert handoffs == [("agent", "approval"), ("approval", "agent"), ("agent", "approval")]

    r = runner.resume_run(rid, ApprovalDecision(decision="approve"), events.append)
    assert r.status == "completed" and r.plan_version == 2
    assert events[-1].type == "run_end" and rid not in baseline_agent._SESSIONS
    with pytest.raises(KeyError):
        runner.resume_run(rid, ApprovalDecision(decision="approve"), events.append)


def test_auto_approve_completes():
    fakes.install(key("dev_B_S3_typical"))
    _, r = run("dev_B_S3_typical", [], auto_approve=True)
    assert r.status == "completed" and r.plan_version == 1


def test_budget_ends_the_run_cleanly():
    fakes.install(key("dev_A_S1_clean"))
    rid, r = run("dev_A_S1_clean", [], budget=Budget(max_llm_calls=2))
    assert r.status == "failed" and r.error.code == "budget_exceeded"
    assert rid not in baseline_agent._SESSIONS


def test_read_document_gives_intake_view_and_never_hidden_files():
    bundle = "dev_A_S1_clean"
    inputs = BUNDLES / bundle / "inputs"
    name = sorted(p.name for p in inputs.iterdir())[0]
    script = iter([
        AIMessage(content="", tool_calls=[
            {"name": "read_document", "args": {"file_name": name}, "id": "a"},
            {"name": "read_document", "args": {"file_name": "../answer_key.json"}, "id": "b"},
            {"name": "read_document", "args": {"file_name": "state.json"}, "id": "c"}]),
    ])
    model = fakes.install(key(bundle), responder=lambda msgs: next(script))
    with pytest.raises(StopIteration):  # stop the loop after one tool round
        baseline_agent.start("run_hidden", inputs, RunConfig(mode="baseline"), lambda e: None)
    ok, bad1, bad2 = [m for m in baseline_agent_messages(model) if isinstance(m, ToolMessage)]
    assert ok.content == document_content(inputs / name)
    assert bad1.status == bad2.status == "error" and "not a document of this bundle" in bad1.content
    everything = json.dumps([str(m.content) for m in baseline_agent_messages(model)])
    assert '"target_' not in everything and "answer_key.json" not in everything.replace("../answer_key.json", "")


def baseline_agent_messages(model):
    return model.calls[-1] if model.calls else []


def test_bad_final_answer_gets_the_planners_repair_message():
    bundle = "dev_A_S1_clean"
    real = fakes.agent_responder(key(bundle))
    state = {"bad": True}

    def responder(msgs):
        reply = real(msgs)
        if not reply.tool_calls and state.pop("bad", False):
            return AIMessage(content="Here is my plan, but not as JSON.")
        return reply
    fakes.install(key(bundle), responder=responder)
    _, r = run(bundle, [])
    assert r.status == "awaiting_approval"


def test_rule_check_before_planning_tools_is_an_error_for_the_model():
    session = baseline_agent.BaselineSession("r", BUNDLES / "dev_A_S1_clean" / "inputs", "dev_A_S1_clean", [])
    from multi_agent.context import null_ctx
    draft = {"planning_start": "2026-12-07", "work_orders": [], "rationale": "x"}
    msg = baseline_agent._run_tool_call(null_ctx(RunConfig(mode="baseline")), session,
                                        {"name": "rule_check", "args": {"draft": draft, "work_centres": []}, "id": "t"})
    assert msg.status == "error" and "call mrp_tool before" in msg.content


def test_same_tools_and_prompts_as_the_multi_agent_system():
    tools = {t["name"]: t for t in baseline_agent.TOOLS}
    planner_defs = {t["name"]: t for t in anthropic_tool_defs(PLANNER_TOOLS)}
    for name in PLANNER_TOOLS:
        assert tools[name] == planner_defs[name]  # identical names, descriptions and schemas
    prompt = system_prompt()
    for part in (load_prompt("planner").strip(), load_prompt("checker").strip(), doc_type_catalogue()):
        assert part in prompt
    assert "{{DOC_TYPES}}" not in prompt and len(prompt_version()) == 12


def test_runner_routes_baseline_to_this_module():
    assert runner.BACKENDS["baseline"] == "baseline.agent"
