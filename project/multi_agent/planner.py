"""Planner agent: turns the Intake extractions into a WorkOrderPlan using mrp_tool and schedule_tool.

The Planner decides (work orders, tool inputs, flags, rationale); the tools compute. To keep
output short and numbers exact, the Planner's final answer is a *draft* (work-order headers,
flags, assumptions, rationale) and `assemble_plan()` copies material requirements, shortages,
suggested POs and the schedule from the latest tool outputs into the WorkOrderPlan.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Optional

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.schemas import (FACTORY_TZ, Flag, MrpToolOutput, ScheduleToolOutput, Variant, WorkOrder,
                          WorkOrderPlan)

from .context import get_ctx
from core.harness import HarnessError, SchemaRepairFailed, run_tool_loop

from .llm import chat_model, parse_json_reply, spec_for
from .prompts import load_prompt
from .tools import anthropic_tool_defs, dump, run_tool

PLANNER_TOOLS = ["mrp_tool", "schedule_tool"]
MAX_PLANNER_TURNS = 8
MAX_PLANNER_REPAIRS = 2   # a final answer can fail assembly (e.g. a WO missing from the schedule)


class PlannerError(HarnessError):
    pass


class PlanAssemblyError(ValueError):
    pass


class DraftWorkOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    wo_id: str
    item_code: str
    qty: float = Field(gt=0)
    uom: str
    customer_po: str
    variant: Optional[Variant] = None
    bom_version: Optional[str] = None
    priority: int = Field(ge=1)
    due_date: date


class PlannerDraft(BaseModel):
    """The Planner's final answer. The rest of the plan comes from the tool outputs."""
    model_config = ConfigDict(extra="forbid")
    planning_start: date
    work_orders: list[DraftWorkOrder]
    flags: list[Flag] = []
    assumptions: list[str] = []
    rationale: str


def assemble_plan(draft: PlannerDraft, tool_outputs: dict[str, Any], *, bundle_id: str, system_id: str,
                  created_at: Optional[datetime] = None) -> WorkOrderPlan:
    """Draft + latest mrp_tool and schedule_tool outputs -> validated WorkOrderPlan."""
    if "mrp_tool" not in tool_outputs:
        raise PlanAssemblyError("call mrp_tool before giving the final plan")
    if "schedule_tool" not in tool_outputs:
        raise PlanAssemblyError("call schedule_tool before giving the final plan")
    mrp = MrpToolOutput.model_validate(tool_outputs["mrp_tool"])
    sched = ScheduleToolOutput.model_validate(tool_outputs["schedule_tool"])
    by_wo = {s.wo_id: s for s in sched.scheduled}
    missing = [w.wo_id for w in draft.work_orders if w.wo_id not in by_wo]
    if missing:
        raise PlanAssemblyError(
            f"work orders {missing} are not in the latest schedule_tool output "
            f"(scheduled: {sorted(by_wo)}). Call schedule_tool with every work order, using the same wo_id values.")
    work_orders = []
    for w in draft.work_orders:
        s = by_wo[w.wo_id]
        work_orders.append(WorkOrder(**w.model_dump(), planned_start=s.planned_start, planned_finish=s.planned_finish,
                                     is_late=s.is_late, days_late=s.days_late, operations=s.operations))
    try:
        return WorkOrderPlan(bundle_id=bundle_id, system_id=system_id,  # type: ignore[arg-type]
                             created_at=created_at or datetime.now(FACTORY_TZ),
                             planning_start=draft.planning_start, work_orders=work_orders,
                             material_requirements=mrp.material_requirements, shortages=mrp.shortages,
                             suggested_pos=mrp.suggested_pos, flags=draft.flags, assumptions=draft.assumptions,
                             rationale=draft.rationale)
    except ValidationError as exc:
        raise PlanAssemblyError(f"the assembled plan is invalid (check that mrp_tool demands use the "
                                f"same wo_id values as the work orders): {exc}") from exc


def _context_message(state: dict) -> str:
    """The Planner's user message: extractions plus any feedback from a previous round."""
    docs = sorted(state.get("extractions", []), key=lambda d: d["document"])
    parts = [f"Bundle: {state['bundle_id']}",
             "## Extracted documents (Intake output, one JSON object per file)",
             "\n".join(json.dumps(d, separators=(",", ":")) for d in docs)]
    if state.get("plan"):
        prev = {k: v for k, v in state["plan"].items() if k in ("planning_start", "work_orders", "shortages",
                                                                 "suggested_pos", "flags", "assumptions")}
        parts += [f"## Your previous plan (version {state.get('plan_version', 1)})", json.dumps(prev, separators=(",", ":"))]
    if state.get("checker_feedback"):
        parts += ["## Checker feedback: fix these issues in this revision",
                  "\n".join(f"- {i}" for i in state["checker_feedback"])]
    if state.get("human_notes"):
        parts += ["## The human planner rejected an earlier plan. Their notes are binding constraints:",
                  "\n".join(f"- {n}" for n in state["human_notes"])]
    if state.get("tool_outputs"):
        parts.append("Your last tool outputs are still valid if you do not change their inputs; call the tools "
                     "again for anything the feedback changes.")
    parts.append("Plan now: call the tools, then give the final draft JSON.")
    return "\n\n".join(parts)


def planner(state: dict) -> dict:
    ctx = get_ctx(state["run_id"])
    spec = spec_for("planner", ctx.config)
    llm, model_id = chat_model("planner", spec)
    version = int(state.get("plan_version", 0)) + 1
    reason = ("Re-planning after human rejection" if state.get("human_notes") and not state.get("checker_feedback")
              else "Revising after Checker feedback" if state.get("checker_feedback") else "Planning")
    ctx.emit("agent_start", agent="planner", model=model_id, summary=f"{reason} (plan v{version})")

    tools = anthropic_tool_defs(PLANNER_TOOLS)
    tool_outputs: dict[str, Any] = dict(state.get("tool_outputs") or {})
    messages: list[BaseMessage] = [SystemMessage(load_prompt("planner")), HumanMessage(_context_message(state))]

    def finish(reply) -> WorkOrderPlan:  # ValueError / ValidationError -> sent back for repair
        draft = PlannerDraft.model_validate(parse_json_reply(reply))
        return assemble_plan(draft, tool_outputs, bundle_id=state["bundle_id"], system_id=ctx.config.system_id)

    try:
        plan: WorkOrderPlan = run_tool_loop(
            ctx, "planner", llm, model_id, messages, tools=tools,
            execute=lambda tc: _run_tool_call(ctx, tc, tool_outputs), finish=finish,
            max_turns=MAX_PLANNER_TURNS, max_repairs=MAX_PLANNER_REPAIRS, purpose=f"plan v{version}",
            repair_hint="Fix it (call the tools again if needed) and reply with the corrected draft JSON.").value
    except HarnessError as exc:
        if isinstance(exc, SchemaRepairFailed):
            raise
        raise PlannerError(str(exc)) from exc

    summary = (f"plan v{version}: {len(plan.work_orders)} WO, {len(plan.shortages)} shortage(s), "
               f"{len(plan.flags)} flag(s), {sum(w.is_late for w in plan.work_orders)} late")
    ctx.emit("agent_end", agent="planner", model=model_id, summary=summary, payload={"plan_version": version})
    ctx.handoff("planner", "checker", f"Planner → Checker: {summary}", {"plan_version": version})
    return {"plan": plan.model_dump(mode="json"), "plan_version": version, "tool_outputs": tool_outputs}


def _run_tool_call(ctx, tc: dict, tool_outputs: dict[str, Any]) -> ToolMessage:
    name = tc.get("name")
    if name not in PLANNER_TOOLS:
        return ToolMessage(f"Unknown tool {name!r}. Available: {PLANNER_TOOLS}", tool_call_id=tc["id"], status="error")
    try:
        out = run_tool(ctx, "planner", name, tc.get("args") or {})
    except ValidationError as exc:
        return ToolMessage(f"Invalid arguments for {name}: {str(exc)[:2000]}", tool_call_id=tc["id"], status="error")
    except Exception as exc:  # noqa: BLE001 - a tool bug goes back to the model, not up the stack
        ctx.emit("error", agent="planner", tool=name, summary=f"{name} failed: {type(exc).__name__}: {exc}")
        return ToolMessage(f"{name} failed: {type(exc).__name__}: {exc}", tool_call_id=tc["id"], status="error")
    tool_outputs[name] = out.model_dump(mode="json")
    return ToolMessage(dump(out), tool_call_id=tc["id"])
