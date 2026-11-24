"""The single-agent baseline (system ``B``).

One Claude Sonnet 5.5 agent reads the bundle, plans and checks, using exactly
the same harness (``project/core/harness.py``), tools
(``project/tools/registry.py``), budgets and output schema as the multi-agent
system. Only the architecture differs: one agent and one context instead of
Intake -> Planner -> Checker.

Trace for one run (API.md §4.4 steps for baseline: ``agent``, ``approval``)::

    run_start -> agent_start(agent) -> llm_call / tool_call ... -> agent_end(agent)
      -> handoff(agent -> approval) -> interrupt(approval)            awaiting approval
                                    -> human_decision -> run_end      auto_approve
    any failure -> error -> agent_end -> run_end(failed)

``run_baseline`` is what ``runner.start_run(mode="baseline")`` calls.
Reject-and-resume is added on top of :class:`BaselineSession` (Task 7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.memory import InMemorySaver

from project.core.harness import AgentHandle, RunBudget, create_harness_agent, run_agent
from project.core.schemas import (
    SCHEMA_VERSION,
    RunConfig,
    RunError,
    RunResult,
    WorkOrderPlan,
)
from project.core.tracing import Emit, Tracer, now
from project.baseline.prompts import BaselinePrompt, build_system_prompt, build_user_message
from project.tools.loaders import document_list_text, inputs_dir
from project.tools.registry import build_tools

BASELINE_MODEL = "claude-sonnet-5-5"
SYSTEM_ID = "B"
AGENT_NAME = "agent"


@dataclass
class BaselineSession:
    """Everything one baseline run needs between its start and a later resume."""

    run_id: str
    bundle_dir: Path
    bundle_id: str
    config: RunConfig
    tracer: Tracer
    budget: RunBudget
    handle: AgentHandle
    prompt: BaselinePrompt
    planning_start: date | None
    plan_version: int = 0
    plans: list[WorkOrderPlan] = field(default_factory=list)

    @property
    def plan(self) -> WorkOrderPlan | None:
        return self.plans[-1] if self.plans else None


def create_session(
    run_id: str,
    bundle_dir: Path | str,
    config: RunConfig,
    emit: Emit,
    *,
    planning_start: date | None = None,
    run_dir: Path | str | None = None,
    model: str | BaseChatModel = BASELINE_MODEL,
) -> BaselineSession:
    """Build the agent for one run. ``model`` is always Sonnet 5.5 outside tests."""
    bundle_dir = Path(bundle_dir)
    if not inputs_dir(bundle_dir).is_dir():
        raise FileNotFoundError(f"Bundle folder not found: {bundle_dir}")
    tracer = Tracer(run_id, emit=emit, run_dir=run_dir)
    budget = RunBudget(config.budgets)
    prompt = build_system_prompt()
    tools = build_tools(bundle_dir)
    handle = create_harness_agent(
        name=AGENT_NAME,
        model=model,
        tools=list(tools.values()),
        system_prompt=prompt.text,
        tracer=tracer,
        budget=budget,
        output_schema=WorkOrderPlan,
        checkpointer=InMemorySaver(),  # keeps the conversation for reject-and-resume
    )
    return BaselineSession(
        run_id=run_id, bundle_dir=bundle_dir, bundle_id=bundle_dir.name, config=config, tracer=tracer,
        budget=budget, handle=handle, prompt=prompt, planning_start=planning_start,
    )


def finalize_plan(plan: WorkOrderPlan, bundle_id: str) -> WorkOrderPlan:
    """Set the bookkeeping fields the system owns, whatever the model wrote there."""
    return plan.model_copy(update={
        "schema_version": SCHEMA_VERSION, "bundle_id": bundle_id, "system_id": SYSTEM_ID,
        "created_at": now(), "checker": None,
    })


def _metrics(session: BaselineSession) -> dict[str, Any]:
    b = session.budget
    return {"llm_calls": b.llm_calls, "tokens": b.tokens, "elapsed_s": round(b.elapsed_s, 1)}


def _fail(session: BaselineSession, error: RunError) -> RunResult:
    session.tracer.emit("run_end", summary=f"Run failed: {error.code}",
                        payload={"status": "failed", "error": error.model_dump(), **_metrics(session)})
    return RunResult(status="failed", plan=session.plan, error=error)


def _after_agent(session: BaselineSession, output: Any, error: RunError | None) -> RunResult:
    """Turn the agent's result into a plan version and either wait for approval or finish."""
    if error is not None:
        return _fail(session, error)
    if output is None:
        return _fail(session, RunError(code="schema_invalid", message="The agent finished without a plan."))
    plan = finalize_plan(output, session.bundle_id)
    session.plans.append(plan)
    session.plan_version += 1
    t = session.tracer
    t.handoff(AGENT_NAME, "approval",
              f"Agent → Approval: plan v{session.plan_version} ({len(plan.work_orders)} WO, "
              f"{len(plan.shortages)} shortage(s), {len(plan.flags)} flag(s))",
              payload={"plan_version": session.plan_version})
    if session.config.auto_approve:
        t.emit("human_decision", agent="approval", summary=f"Plan v{session.plan_version} auto-approved (benchmark run)",
               payload={"decision": "approve", "auto": True, "plan_version": session.plan_version})
        t.emit("run_end", summary="Run completed",
               payload={"status": "completed", "plan_version": session.plan_version, **_metrics(session)})
        return RunResult(status="completed", plan=plan)
    t.emit("interrupt", agent="approval", summary=f"Waiting for human approval of plan v{session.plan_version}",
           payload={"plan_version": session.plan_version, **_metrics(session)})
    return RunResult(status="awaiting_approval", plan=plan)


def start(session: BaselineSession) -> RunResult:
    """Run the agent until it submits a plan (or fails). Blocks."""
    t = session.tracer
    t.emit(
        "run_start", model=session.handle.model_name, summary=f"Baseline run on {session.bundle_id}",
        payload={
            "mode": "baseline", "system_id": SYSTEM_ID, "bundle_id": session.bundle_id,
            "model": session.handle.model_name, "prompt_version": session.prompt.version,
            "prompt_drafts": list(session.prompt.drafts),
            "planning_start": session.planning_start.isoformat() if session.planning_start else None,
            "budgets": session.config.budgets.model_dump(), "seed": session.config.seed,
        },
    )
    message = build_user_message(session.bundle_id, document_list_text(session.bundle_dir), session.planning_start)
    result = run_agent(session.handle, message, thread_id=session.run_id)
    return _after_agent(session, result.output, result.error)


def run_baseline(
    run_id: str,
    bundle_dir: Path | str,
    config: RunConfig,
    emit: Emit,
    *,
    planning_start: date | None = None,
    run_dir: Path | str | None = None,
    model: str | BaseChatModel = BASELINE_MODEL,
) -> tuple[RunResult, BaselineSession]:
    """Create a session and run it to the first plan. Returns the result and the session (for resume)."""
    session = create_session(run_id, bundle_dir, config, emit, planning_start=planning_start, run_dir=run_dir, model=model)
    return start(session), session
