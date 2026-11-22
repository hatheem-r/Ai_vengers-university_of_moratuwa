"""Single-agent baseline (system B): one Sonnet 5.5 ReAct agent, same harness and tools.

Fairness (TASKS.md §0.1): this agent uses the same harness (core/harness.py: budgets,
retries, prompt caching, schema repair, trace events), the same tool implementations and
descriptions (multi_agent/tools.py registry, tools/loaders.read_input), the same final
answer and plan assembly (multi_agent/planner.PlannerDraft + assemble_plan) and the same
WorkOrderPlan output as the multi-agent system. Its prompt is built from the multi-agent
prompt files, so the prompt freeze carries over.

What differs, on purpose: one agent ("agent" in traces) reads the raw documents itself with
`read_document`, and calls `rule_check` itself instead of handing the plan to a Checker.

Backend interface (core/runner.py): start() runs until the plan waits for approval;
resume() approves (completed) or adds the human's notes as a new message and re-plans.
The conversation is kept in memory by run_id, like the multi-agent InMemorySaver.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, ConfigDict, ValidationError

from core.harness import (Emit, RunContext, chat_model, close_ctx, open_ctx, run_tool_loop,
                          spec_for)
from core.schemas import (ApprovalDecision, FactoryPolicyFields, RuleCheckInput, RunConfig, RunError, RunResult,
                          ScheduleToolInput, WorkOrderPlan)
from multi_agent.llm import parse_json_reply
from multi_agent.planner import PlannerDraft, assemble_plan
from multi_agent.prompts import load_prompt as load_multi_agent_prompt
from multi_agent.tools import SPECS, anthropic_tool_defs, dump, run_tool
from tools.loaders import list_documents, read_input

AGENT = "agent"
PROMPT_DIR = Path(__file__).parent / "prompts"
MAX_TURNS = 30          # per planning round; the run's 40-call budget still applies
MAX_REPAIRS = 2         # same as the multi-agent Planner


class RuleCheckArgs(BaseModel):
    """rule_check as the baseline calls it: the draft (assembled with the latest tool outputs)
    plus the factory policy as read from the documents."""
    model_config = ConfigDict(extra="forbid")
    draft: PlannerDraft
    policy: Optional[FactoryPolicyFields] = None


TOOL_DEFS: list[dict] = [
    {"name": "read_document",
     "description": "Read one file from the bundle's inputs folder. Spreadsheets come back as rows "
                    "(r1, r2, ... with every sheet), PDFs as text per page plus page images when the "
                    "text layer is thin.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string", "description": "File name"}},
                      "required": ["name"], "additionalProperties": False}},
    *anthropic_tool_defs(["mrp_tool", "schedule_tool"]),
    {"name": "rule_check", "description": SPECS["rule_check"].description
        + " Send your draft final answer as `draft` (same JSON as the final answer) and the factory "
          "working-hours policy as `policy`; work centres, downtime and material dates come from your "
          "latest schedule_tool and mrp_tool calls.",
     "input_schema": RuleCheckArgs.model_json_schema()},
]


def system_prompt() -> str:
    """The baseline prompt: baseline preface + the Planner prompt + the Checker's checklist."""
    planner = load_multi_agent_prompt("planner")
    checker = load_multi_agent_prompt("checker")
    checks = checker.split("Check:", 1)[1].split("Reply with", 1)[0].strip() if "Check:" in checker else checker
    template = (PROMPT_DIR / "agent.md").read_text(encoding="utf-8")
    return template.replace("{{PLANNER}}", planner.strip()).replace("{{CHECKER_CHECKS}}", checks)


@dataclass
class _Run:
    bundle_id: str
    inputs_dir: Path
    messages: list[BaseMessage]
    tool_outputs: dict[str, Any] = field(default_factory=dict)
    tool_inputs: dict[str, Any] = field(default_factory=dict)
    plan: Optional[WorkOrderPlan] = None
    plan_version: int = 0


_RUNS: dict[str, _Run] = {}
_LOCK = threading.Lock()


# --- tools ---------------------------------------------------------------------------------------

def _execute(ctx: RunContext, run: _Run, tc: dict) -> ToolMessage:
    name, args, call_id = tc.get("name"), tc.get("args") or {}, tc["id"]
    try:
        if name == "read_document":
            t0 = time.monotonic()
            doc = read_input(run.inputs_dir, str(args.get("name", "")))
            ctx.record_tool()
            ctx.emit("tool_call", agent=AGENT, tool="read_document", item=doc.name,
                     latency_ms=int((time.monotonic() - t0) * 1000), cost_usd=0,
                     summary=f"read_document: {doc.name} ({doc.kind}, {len(doc.text)} chars"
                             + (f", {len(doc.images())} image(s)" if doc.images() else "") + ")")
            return ToolMessage(content=doc.content_blocks(), tool_call_id=call_id)
        if name in ("mrp_tool", "schedule_tool"):
            out = run_tool(ctx, AGENT, name, args)
            run.tool_outputs[name] = out.model_dump(mode="json")
            run.tool_inputs[name] = SPECS[name].input_model.model_validate(args).model_dump(mode="json")
            return ToolMessage(dump(out), tool_call_id=call_id)
        if name == "rule_check":
            out = run_tool(ctx, AGENT, "rule_check", _rule_check_input(run, RuleCheckArgs.model_validate(args)))
            return ToolMessage(dump(out), tool_call_id=call_id)
        return ToolMessage(f"Unknown tool {name!r}. Available: {[t['name'] for t in TOOL_DEFS]}",
                           tool_call_id=call_id, status="error")
    except (ValidationError, ValueError, FileNotFoundError) as exc:
        return ToolMessage(f"{name}: {str(exc)[:2000]}", tool_call_id=call_id, status="error")
    except Exception as exc:  # noqa: BLE001 - a tool bug goes back to the model, not up the stack
        ctx.emit("error", agent=AGENT, tool=name, summary=f"{name} failed: {type(exc).__name__}: {exc}")
        return ToolMessage(f"{name} failed: {type(exc).__name__}: {exc}", tool_call_id=call_id, status="error")


def _rule_check_input(run: _Run, args: RuleCheckArgs) -> RuleCheckInput:
    plan = assemble_plan(args.draft, run.tool_outputs, bundle_id=run.bundle_id, system_id="B")
    sched_in = run.tool_inputs.get("schedule_tool")
    if sched_in is None:
        raise ValueError("call schedule_tool before rule_check")
    sched = ScheduleToolInput.model_validate(sched_in)
    material_ready = (run.tool_outputs.get("mrp_tool") or {}).get("material_ready", {})
    return RuleCheckInput(plan=plan, policy=args.policy or FactoryPolicyFields(), work_centres=sched.work_centres,
                          downtime=sched.downtime, material_ready=material_ready, calendar=sched.calendar)


# --- one planning round ----------------------------------------------------------------------------

def _plan_round(ctx: RunContext, run: _Run, reason: str) -> RunResult:
    spec = spec_for(AGENT, ctx.config)
    llm, model_id = chat_model(AGENT, spec)
    version = run.plan_version + 1
    ctx.emit("agent_start", agent=AGENT, model=model_id, summary=f"{reason} (plan v{version})")

    def finish(reply) -> WorkOrderPlan:
        draft = PlannerDraft.model_validate(parse_json_reply(reply))
        return assemble_plan(draft, run.tool_outputs, bundle_id=run.bundle_id, system_id=ctx.config.system_id)

    loop = run_tool_loop(ctx, AGENT, llm, model_id, run.messages, tools=TOOL_DEFS,
                         execute=lambda tc: _execute(ctx, run, tc), finish=finish,
                         max_turns=MAX_TURNS, max_repairs=MAX_REPAIRS, purpose=f"plan v{version}",
                         repair_hint="Fix it (call the tools again if needed) and reply with the corrected "
                                     "final JSON.")  # HarnessError -> runner -> status=failed
    run.messages = loop.messages
    run.plan, run.plan_version = loop.value, version
    plan = run.plan
    summary = (f"plan v{version}: {len(plan.work_orders)} WO, {len(plan.shortages)} shortage(s), "
               f"{len(plan.flags)} flag(s), {sum(w.is_late for w in plan.work_orders)} late")
    ctx.emit("agent_end", agent=AGENT, model=model_id, summary=summary,
             payload={"plan_version": version, "turns": loop.turns, "repairs": loop.repairs})
    ctx.handoff(AGENT, "approval", f"Agent → Approval: {summary}", {"plan_version": version})
    ctx.emit("interrupt", agent="approval", summary=f"Plan v{version} waiting for approval",
             payload={"plan_version": version})
    return RunResult(run_id=ctx.run_id, status="awaiting_approval", plan=plan, plan_version=version)


def _first_message(bundle_id: str, inputs_dir: Path) -> str:
    names = [p.name for p in list_documents(inputs_dir)]
    return (f"Bundle: {bundle_id}\n\nDocuments in the bundle ({len(names)}):\n"
            + "\n".join(f"- {n}" for n in names)
            + "\n\nRead the documents, plan with the tools, check the plan with rule_check, then give the "
              "final JSON.")


# --- runner backend ------------------------------------------------------------------------------

def start(run_id: str, inputs_dir: Path, config: RunConfig, emit: Emit) -> RunResult:
    inputs_dir = Path(inputs_dir)
    bundle_id = inputs_dir.parent.name if inputs_dir.name == "inputs" else inputs_dir.name
    run = _Run(bundle_id=bundle_id, inputs_dir=inputs_dir,
               messages=[SystemMessage(system_prompt()), HumanMessage(_first_message(bundle_id, inputs_dir))])
    with _LOCK:
        _RUNS[run_id] = run
    ctx = open_ctx(run_id, config, emit)
    ctx.begin_segment()
    try:
        result = _plan_round(ctx, run, "Planning")
    except BaseException:
        _forget(run_id)
        raise
    finally:
        ctx.end_segment()
    return result


def resume(run_id: str, decision: ApprovalDecision, config: RunConfig, emit: Emit) -> RunResult:
    run = _RUNS.get(run_id)
    if run is None or run.plan is None:
        return RunResult(run_id=run_id, status="failed",
                         error=RunError(code="internal", message=f"run {run_id} is not waiting for approval"))
    if decision.decision == "approve":
        _forget(run_id)
        return RunResult(run_id=run_id, status="completed", plan=run.plan, plan_version=run.plan_version)
    ctx = open_ctx(run_id, config, emit)
    ctx.handoff("approval", AGENT, f"Approval → Agent: rejected plan v{run.plan_version}: {decision.notes}",
                {"notes": decision.notes})
    run.messages.append(HumanMessage(
        "The human planner rejected your plan. Their notes are binding constraints:\n"
        f"- {decision.notes or '(no notes)'}\n"
        "Re-plan: call the tools again for anything the notes change, check with rule_check, then give the "
        "final JSON."))
    ctx.begin_segment()
    try:
        result = _plan_round(ctx, run, "Re-planning after human rejection")
    except BaseException:
        _forget(run_id)
        raise
    finally:
        ctx.end_segment()
    return result


def _forget(run_id: str) -> None:
    with _LOCK:
        _RUNS.pop(run_id, None)
    close_ctx(run_id)
