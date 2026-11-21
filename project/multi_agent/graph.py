"""LangGraph pipeline for the multi-agent systems (M-mix, M-S) and its runner backend.

    START -> intake_start --Send x N--> intake_document -> intake_join -> planner -> checker
    checker --revise (max once)--> planner
    checker --pass / fail_after_revision--> approval  (interrupt(): waits for the human)
    approval --approve--> END
    approval --reject with notes--> planner  (notes become binding constraints)

`start()` / `resume()` follow the backend interface in core/runner.py. Runs are checkpointed
by run_id (thread_id), so a resumed run continues where it stopped. The default checkpointer
is in memory (the API keeps runs in one process); `set_checkpointer()` swaps in a persistent one.
"""
from __future__ import annotations

import operator
from pathlib import Path
from typing import Annotated, Any, Optional, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

from core.schemas import ApprovalDecision, RunConfig, RunError, RunResult, WorkOrderPlan

from .checker import checker
from .context import Emit, close_ctx, get_ctx, open_ctx
from .intake import intake_document, intake_join, intake_start
from .planner import planner


class PlanState(TypedDict, total=False):
    run_id: str
    bundle_id: str
    inputs_dir: str
    documents: list[str]
    extractions: Annotated[list[dict], operator.add]   # one per document, from parallel Intake nodes
    plan: Optional[dict]                                # latest WorkOrderPlan (JSON)
    plan_version: int
    tool_outputs: dict[str, Any]                        # latest mrp_tool / schedule_tool outputs
    checker_feedback: list[str]
    checker_revisions: int                              # revisions the Checker asked for this round (max 1)
    route: str                                          # checker's next step: "planner" | "approval"
    human_notes: list[str]
    human_revisions: int
    approved: bool


def fan_out_intake(state: PlanState) -> list[Send]:
    return [Send("intake_document", {"run_id": state["run_id"], "inputs_dir": state["inputs_dir"], "document": d})
            for d in state["documents"]]


def after_checker(state: PlanState) -> str:
    return state.get("route", "approval")


def approval(state: PlanState) -> dict:
    """Pause for the human. Nothing runs before interrupt(), so resuming replays nothing."""
    decision = ApprovalDecision.model_validate(
        interrupt({"plan_version": state.get("plan_version"), "verdict": (state.get("plan") or {}).get("checker")}))
    if decision.decision == "approve":
        return {"approved": True}
    ctx = get_ctx(state["run_id"])
    ctx.handoff("approval", "planner", f"Approval → Planner: rejected plan v{state.get('plan_version')}: {decision.notes}",
                {"notes": decision.notes})
    return {"approved": False, "human_notes": [*state.get("human_notes", []), decision.notes or ""],
            "human_revisions": state.get("human_revisions", 0) + 1,
            "checker_revisions": 0, "checker_feedback": []}


def after_approval(state: PlanState) -> str:
    return END if state.get("approved") else "planner"


def build_graph(checkpointer=None):
    g = StateGraph(PlanState)
    g.add_node("intake_start", intake_start)
    g.add_node("intake_document", intake_document)
    g.add_node("intake_join", intake_join)
    g.add_node("planner", planner)
    g.add_node("checker", checker)
    g.add_node("approval", approval)
    g.add_edge(START, "intake_start")
    g.add_conditional_edges("intake_start", fan_out_intake, ["intake_document"])
    g.add_edge("intake_document", "intake_join")
    g.add_edge("intake_join", "planner")
    g.add_edge("planner", "checker")
    g.add_conditional_edges("checker", after_checker, {"planner": "planner", "approval": "approval"})
    g.add_conditional_edges("approval", after_approval, {"planner": "planner", END: END})
    return g.compile(checkpointer=checkpointer if checkpointer is not None else InMemorySaver())


_GRAPH = None


def get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    return _GRAPH


def set_checkpointer(checkpointer) -> None:
    """Rebuild the graph with another checkpointer (e.g. SqliteSaver for runs that survive a restart)."""
    global _GRAPH
    _GRAPH = build_graph(checkpointer)


# --- runner backend (core/runner.py) ------------------------------------------------------

def _thread(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}, "recursion_limit": 50}


def start(run_id: str, inputs_dir: Path, config: RunConfig, emit: Emit) -> RunResult:
    inputs_dir = Path(inputs_dir)
    bundle_id = inputs_dir.parent.name if inputs_dir.name == "inputs" else inputs_dir.name
    ctx = open_ctx(run_id, config, emit)
    ctx.begin_segment()
    try:
        get_graph().invoke({"run_id": run_id, "bundle_id": bundle_id, "inputs_dir": str(inputs_dir),
                            "plan_version": 0, "checker_revisions": 0, "human_notes": [], "human_revisions": 0},
                           _thread(run_id))
        return _result(run_id)
    except BaseException:
        close_ctx(run_id)
        raise
    finally:
        ctx.end_segment()
        _cleanup_if_done(run_id)


def resume(run_id: str, decision: ApprovalDecision, config: RunConfig, emit: Emit) -> RunResult:
    graph = get_graph()
    snapshot = graph.get_state(_thread(run_id))
    if not snapshot.next:
        return RunResult(run_id=run_id, status="failed",
                         error=RunError(code="internal", message=f"run {run_id} is not waiting for approval"))
    ctx = open_ctx(run_id, config, emit)
    ctx.begin_segment()
    try:
        graph.invoke(Command(resume=decision.model_dump(mode="json")), _thread(run_id))
        return _result(run_id)
    except BaseException:
        close_ctx(run_id)
        raise
    finally:
        ctx.end_segment()
        _cleanup_if_done(run_id)


def _result(run_id: str) -> RunResult:
    snapshot = get_graph().get_state(_thread(run_id))
    values = snapshot.values
    plan = WorkOrderPlan.model_validate(values["plan"]) if values.get("plan") else None
    version = int(values.get("plan_version", 0))
    if snapshot.next:  # paused in approval
        ctx = get_ctx(run_id)
        verdict = plan.checker.verdict if plan and plan.checker else None
        ctx.emit("interrupt", agent="approval", summary=f"Plan v{version} waiting for approval (Checker: {verdict})",
                 payload={"plan_version": version, "checker_verdict": verdict})
        return RunResult(run_id=run_id, status="awaiting_approval", plan=plan, plan_version=version)
    if values.get("approved"):
        return RunResult(run_id=run_id, status="completed", plan=plan, plan_version=version)
    return RunResult(run_id=run_id, status="failed", plan_version=version,
                     error=RunError(code="internal", message="graph ended without approval"))


def _cleanup_if_done(run_id: str) -> None:
    try:
        done = not get_graph().get_state(_thread(run_id)).next
    except Exception:  # noqa: BLE001
        done = True
    if done:
        close_ctx(run_id)
