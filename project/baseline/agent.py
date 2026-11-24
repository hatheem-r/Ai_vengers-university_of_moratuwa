"""Single-agent baseline (system B), the runner backend for mode="baseline".

One Sonnet 5.5 agent reads the documents, plans and checks in one conversation. Everything
except the architecture is shared with the multi-agent system, so the comparison is fair:

| | multi-agent | baseline |
|---|---|---|
| LLM calls, budget, `llm_call` traces | multi_agent/llm.py `call_llm` + RunContext | same |
| Model | core/models.py registry | same (`agent` -> Sonnet 5.5) |
| mrp_tool / schedule_tool / rule_check | multi_agent/tools.py `run_tool` -> project/tools | same |
| Tool descriptions | multi_agent/tools.py SPECS | same (rule_check also takes the draft, see below) |
| How a document reaches the model | multi_agent/intake.py `document_content` | same, via read_document |
| Prompts | multi_agent/prompts/*.md | the three combined (baseline/prompt.py) |
| Final answer -> WorkOrderPlan | planner.py `PlannerDraft` + `assemble_plan` | same |

rule_check: the Checker passes a full WorkOrderPlan. The single agent passes its draft (the
same JSON as its final answer); the plan is assembled from its latest mrp_tool and
schedule_tool outputs exactly as for the final answer, and material readiness comes from
mrp_tool, as in the Checker.

Trace (API.md §4.4 steps for baseline: `agent`, `approval`):
    agent_start -> llm_call / tool_call ... -> agent_end -> handoff(agent -> approval) -> interrupt
    reject: handoff(approval -> agent) -> agent_start ... -> interrupt (new plan version)
The runner emits run_start, run_end and human_decision, and handles auto_approve.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.schemas import (ApprovalDecision, CapacityLine, CustomerPOLine, DowntimeWindow, FactoryPolicyFields,
                          PlanningCalendar, RuleCheckInput, RunConfig, RunError, RunResult, WorkOrderPlan)
from multi_agent.context import Emit, RunContext, close_ctx, open_ctx
from multi_agent.intake import document_content, list_documents
from multi_agent.llm import call_llm, chat_model, parse_json_reply, spec_for
from multi_agent.planner import PlanAssemblyError, PlannerDraft, assemble_plan
from multi_agent.tools import SPECS, anthropic_tool_defs, dump, run_tool

from .prompt import prompt_version, system_prompt

AGENT = "agent"
MAX_AGENT_TURNS = 40  # the run budget (40 LLM calls) is the real limit


class BaselineError(RuntimeError):
    pass


class BaselineRuleCheckArgs(BaseModel):
    """rule_check input for the single agent: its draft instead of a full plan."""
    model_config = ConfigDict(extra="forbid")
    draft: PlannerDraft = Field(description="Your draft plan: the same JSON as your final answer")
    policy: FactoryPolicyFields = FactoryPolicyFields()
    work_centres: list[CapacityLine]
    downtime: list[DowntimeWindow] = []
    order_lines: list[CustomerPOLine] = Field(default=[], description="Customer PO lines, for qty_mismatch checks")
    calendar: PlanningCalendar = PlanningCalendar()


READ_DOCUMENT_TOOL = {
    "name": "read_document",
    "description": ("Read one document of the bundle by file name. Spreadsheets come back as tab-separated "
                    "rows (r1, r2, ...), CSV files likewise, PDFs as text per page with the PDF itself attached "
                    "when its text layer is thin (a scan), images as images."),
    "input_schema": {"type": "object", "additionalProperties": False, "required": ["file_name"],
                     "properties": {"file_name": {"type": "string",
                                                  "description": "Exact file name from the document list"}}},
}
RULE_CHECK_TOOL = {
    "name": "rule_check",
    "description": (SPECS["rule_check"].description + " Pass your draft plan as `draft` (the same JSON as your "
                    "final answer); the plan is assembled from your latest mrp_tool and schedule_tool outputs, "
                    "and material readiness comes from mrp_tool."),
    "input_schema": BaselineRuleCheckArgs.model_json_schema(),
}
TOOLS = [READ_DOCUMENT_TOOL, *anthropic_tool_defs(["mrp_tool", "schedule_tool"]), RULE_CHECK_TOOL]


@dataclass
class BaselineSession:
    """One run's conversation, kept between start() and resume() (in this process)."""
    run_id: str
    inputs_dir: Path
    bundle_id: str
    messages: list[BaseMessage]
    tool_outputs: dict[str, Any] = field(default_factory=dict)
    plan: Optional[WorkOrderPlan] = None
    plan_version: int = 0


_SESSIONS: dict[str, BaselineSession] = {}
_SESSIONS_LOCK = threading.Lock()


# --- runner backend ------------------------------------------------------------------------

def start(run_id: str, inputs_dir: Path, config: RunConfig, emit: Emit) -> RunResult:
    inputs_dir = Path(inputs_dir)
    bundle_id = inputs_dir.parent.name if inputs_dir.name == "inputs" else inputs_dir.name
    ctx = open_ctx(run_id, config, emit)
    ctx.begin_segment()
    try:
        docs = [p.name for p in list_documents(inputs_dir)]
        user = (f"Bundle: {bundle_id}\n\nDocuments in the bundle ({len(docs)}):\n"
                + "\n".join(f"- {d}" for d in docs)
                + "\n\nRead the documents, plan with the tools, check the plan, then give the final draft JSON.")
        session = BaselineSession(run_id, inputs_dir, bundle_id, [SystemMessage(system_prompt()), HumanMessage(user)])
        with _SESSIONS_LOCK:
            _SESSIONS[run_id] = session
        return _round(ctx, session, "Planning")
    except BaseException:
        _forget(run_id)
        raise
    finally:
        ctx.end_segment()


def resume(run_id: str, decision: ApprovalDecision, config: RunConfig, emit: Emit) -> RunResult:
    with _SESSIONS_LOCK:
        session = _SESSIONS.get(run_id)
    if session is None or session.plan is None:
        return RunResult(run_id=run_id, status="failed",
                         error=RunError(code="internal", message=f"run {run_id} is not waiting for approval"))
    if decision.decision == "approve":
        _forget(run_id)
        return RunResult(run_id=run_id, status="completed", plan=session.plan, plan_version=session.plan_version)

    ctx = open_ctx(run_id, config, emit)
    ctx.begin_segment()
    try:
        ctx.handoff("approval", AGENT, f"Approval → Agent: rejected plan v{session.plan_version}: {decision.notes}",
                    {"notes": decision.notes})
        # Same wording as the Planner gets on a rejection (planner._context_message).
        session.messages.append(HumanMessage(
            "## The human planner rejected an earlier plan. Their notes are binding constraints:\n"
            f"- {decision.notes or ''}\n\n"
            "Your last tool outputs are still valid if you do not change their inputs; call the tools again for "
            "anything the notes change. Then give the corrected final draft JSON."))
        return _round(ctx, session, "Re-planning after human rejection")
    except BaseException:
        _forget(run_id)
        raise
    finally:
        ctx.end_segment()


# --- the agent loop ----------------------------------------------------------------------

def _round(ctx: RunContext, session: BaselineSession, reason: str) -> RunResult:
    """Run the agent until it gives a valid final draft; then wait for approval."""
    spec = spec_for(AGENT, ctx.config)
    llm, model_id = chat_model(AGENT, spec)
    version = session.plan_version + 1
    ctx.emit("agent_start", agent=AGENT, model=model_id, summary=f"{reason} (plan v{version})",
             payload={"prompt_version": prompt_version(), "plan_version": version})

    plan: Optional[WorkOrderPlan] = None
    for _ in range(MAX_AGENT_TURNS):
        reply = call_llm(ctx, AGENT, llm, model_id, session.messages, tools=TOOLS, purpose=f"plan v{version}")
        session.messages.append(reply)
        if reply.tool_calls:
            for tc in reply.tool_calls:
                session.messages.append(_run_tool_call(ctx, session, tc))
            continue
        try:
            draft = PlannerDraft.model_validate(parse_json_reply(reply))
            plan = assemble_plan(draft, session.tool_outputs, bundle_id=session.bundle_id,
                                 system_id=ctx.config.system_id)
            break
        except (ValueError, ValidationError) as exc:  # same repair message as the Planner
            session.messages.append(HumanMessage(
                f"Your final answer could not be used: {str(exc)[:2000]}\n"
                "Fix it (call the tools again if needed) and reply with the corrected draft JSON."))
    if plan is None:
        raise BaselineError(f"Baseline agent gave no valid plan in {MAX_AGENT_TURNS} turns")

    session.plan, session.plan_version = plan, version
    summary = (f"plan v{version}: {len(plan.work_orders)} WO, {len(plan.shortages)} shortage(s), "
               f"{len(plan.flags)} flag(s), {sum(w.is_late for w in plan.work_orders)} late")
    ctx.emit("agent_end", agent=AGENT, model=model_id, summary=summary, payload={"plan_version": version})
    ctx.handoff(AGENT, "approval", f"Agent → Approval: {summary}", {"plan_version": version})
    ctx.emit("interrupt", agent="approval", summary=f"Plan v{version} waiting for approval",
             payload={"plan_version": version})
    return RunResult(run_id=session.run_id, status="awaiting_approval", plan=plan, plan_version=version)


def _run_tool_call(ctx: RunContext, session: BaselineSession, tc: dict) -> ToolMessage:
    name, args, call_id = tc.get("name"), tc.get("args") or {}, tc["id"]
    try:
        if name == "read_document":
            return ToolMessage(_read_document(ctx, session, args), tool_call_id=call_id)
        if name in ("mrp_tool", "schedule_tool"):
            out = run_tool(ctx, AGENT, name, args)
            session.tool_outputs[name] = out.model_dump(mode="json")
            return ToolMessage(dump(out), tool_call_id=call_id)
        if name == "rule_check":
            return ToolMessage(dump(run_tool(ctx, AGENT, "rule_check", _rule_check_input(session, args))),
                               tool_call_id=call_id)
        return ToolMessage(f"Unknown tool {name!r}. Available: {[t['name'] for t in TOOLS]}",
                           tool_call_id=call_id, status="error")
    except ValidationError as exc:
        return ToolMessage(f"Invalid arguments for {name}: {str(exc)[:2000]}", tool_call_id=call_id, status="error")
    except (FileNotFoundError, PlanAssemblyError) as exc:
        return ToolMessage(f"{name}: {exc}", tool_call_id=call_id, status="error")
    except Exception as exc:  # noqa: BLE001 - a tool bug goes back to the model, not up the stack
        ctx.emit("error", agent=AGENT, tool=name, summary=f"{name} failed: {type(exc).__name__}: {exc}")
        return ToolMessage(f"{name} failed: {type(exc).__name__}: {exc}", tool_call_id=call_id, status="error")


def _read_document(ctx: RunContext, session: BaselineSession, args: dict) -> list[dict]:
    """The same content blocks Intake's model gets, only for files in inputs/ (hidden-data rule)."""
    name = str(args.get("file_name", "")).strip()
    available = [p.name for p in list_documents(session.inputs_dir)]
    if name not in available:
        raise FileNotFoundError(f"{name!r} is not a document of this bundle. Available: {', '.join(available)}")
    t0 = time.monotonic()
    blocks = document_content(session.inputs_dir / name)
    with ctx._lock:
        ctx.tool_calls += 1
    ctx.emit("tool_call", agent=AGENT, tool="read_document", item=name,
             latency_ms=int((time.monotonic() - t0) * 1000), cost_usd=0, summary=f"read_document: {name}",
             payload={"document": name})
    return blocks


def _rule_check_input(session: BaselineSession, args: dict) -> RuleCheckInput:
    a = BaselineRuleCheckArgs.model_validate(args)
    plan = assemble_plan(a.draft, session.tool_outputs, bundle_id=session.bundle_id, system_id="B")
    material_ready = (session.tool_outputs.get("mrp_tool") or {}).get("material_ready", {})
    return RuleCheckInput(plan=plan, policy=a.policy, work_centres=a.work_centres, downtime=a.downtime,
                          material_ready=material_ready, order_lines=a.order_lines, calendar=a.calendar)


def _forget(run_id: str) -> None:
    with _SESSIONS_LOCK:
        _SESSIONS.pop(run_id, None)
    close_ctx(run_id)
