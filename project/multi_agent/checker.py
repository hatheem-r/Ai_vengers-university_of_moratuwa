"""Checker agent: rule_check (deterministic) + an LLM review. Sends a plan back to the
Planner at most once per round; a second failure goes to approval with the open issues."""
from __future__ import annotations

import json
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, ValidationError

from core.schemas import (CapacityLine, CheckerReview, CustomerPOLine, DowntimeWindow, FactoryPolicyFields,
                          RuleCheckInput, RuleCheckOutput, WorkOrderPlan)

from .context import get_ctx
from .llm import call_llm, chat_model, parse_json_reply, spec_for
from .prompts import load_prompt
from .tools import run_tool

MAX_CHECKER_REVISIONS = 1


class CheckerReply(BaseModel):
    model_config = ConfigDict(extra="ignore")
    verdict: str
    issues: list[str] = []


def _of_type(extractions: list[dict], doc_type: str) -> list[dict]:
    return [d for d in extractions if d.get("doc_type") == doc_type]


def _valid(model: type[BaseModel], data: Any) -> Optional[BaseModel]:
    try:
        return model.model_validate(data)
    except ValidationError:
        return None


def rule_check_input(plan: WorkOrderPlan, extractions: list[dict], tool_outputs: dict) -> RuleCheckInput:
    """Build rule_check's input from the extractions. Invalid lines are skipped, not fatal."""
    policy = next((_valid(FactoryPolicyFields, d.get("fields", {})) for d in _of_type(extractions, "factory_policy")),
                  None) or FactoryPolicyFields()
    work_centres = [c for d in _of_type(extractions, "capacity_shift_plan")
                    for c in (_valid(CapacityLine, ln) for ln in d.get("lines", [])) if c]
    downtime = []
    for d in _of_type(extractions, "maintenance_log"):
        for ln in d.get("lines", []):
            if str(ln.get("status", "")).lower() != "open" or not ln.get("start") or not ln.get("work_centre"):
                continue
            end = ln.get("unavailable_until_incl") or ln.get("expected_return") or ln["start"]
            if w := _valid(DowntimeWindow, {"work_centre": ln["work_centre"], "start": ln["start"], "end_incl": end,
                                            "reason": ln.get("machine")}):
                downtime.append(w)
    order_lines = [c for d in _of_type(extractions, "customer_po")
                   for c in (_valid(CustomerPOLine, ln) for ln in d.get("lines", [])) if c]
    material_ready = (tool_outputs.get("mrp_tool") or {}).get("material_ready", {})
    return RuleCheckInput(plan=plan, policy=policy, work_centres=work_centres, downtime=downtime,
                          material_ready=material_ready, order_lines=order_lines)


def checker(state: dict) -> dict:
    ctx = get_ctx(state["run_id"])
    spec = spec_for("checker", ctx.config)
    llm, model_id = chat_model("checker", spec)
    plan = WorkOrderPlan.model_validate(state["plan"])
    version = state.get("plan_version", 1)
    used = int(state.get("checker_revisions", 0))
    ctx.emit("agent_start", agent="checker", model=model_id,
             summary=f"Reviewing plan v{version}" + (" (second review)" if used else ""))

    rc: RuleCheckOutput = run_tool(ctx, "checker", "rule_check",
                                   rule_check_input(plan, state.get("extractions", []), state.get("tool_outputs", {})))
    hard = [f"[rule_check:{v.rule}] {v.message}" for v in rc.violations if v.severity == "high"]
    soft = [f"[rule_check:{v.rule}] {v.message}" for v in rc.violations if v.severity != "high"]

    review = _llm_review(ctx, llm, model_id, state, plan, rc)
    llm_revise = review is not None and review.verdict.strip().lower() == "revise"
    llm_issues = review.issues if review else ["Checker LLM reply was unreadable; relied on rule_check only."]

    wants_revision = bool(hard) or llm_revise
    issues = hard + (llm_issues if llm_revise else []) + soft
    if wants_revision and used < MAX_CHECKER_REVISIONS:
        verdict, route, new_used, open_issues = "revise", "planner", used + 1, []
    elif wants_revision:
        verdict, route, new_used, open_issues = "fail_after_revision", "approval", used, issues
    else:
        verdict, route, new_used, open_issues = "pass", "approval", used, []
        issues = issues + [i for i in llm_issues if i not in issues]

    result = CheckerReview(verdict=verdict, revisions_used=used if verdict != "revise" else new_used,  # type: ignore[arg-type]
                           issues=issues, open_issues=open_issues)
    plan.checker = result
    ctx.emit("checker_verdict", agent="checker", model=model_id,
             summary=f"Checker: {verdict} on plan v{version} ({len(issues)} issue(s))",
             payload=result.model_dump(mode="json"))
    ctx.emit("agent_end", agent="checker", model=model_id, summary=f"Checker verdict: {verdict}")
    if route == "planner":
        ctx.handoff("checker", "planner", f"Checker → Planner: revise plan v{version} ({len(issues)} issue(s))",
                    {"plan_version": version, "issues": issues})
    else:
        ctx.handoff("checker", "approval", f"Checker → Approval: plan v{version} ({verdict})", {"plan_version": version})
    return {"plan": plan.model_dump(mode="json"), "checker_revisions": new_used, "route": route,
            "checker_feedback": issues if route == "planner" else []}


def _llm_review(ctx, llm, model_id: str, state: dict, plan: WorkOrderPlan, rc: RuleCheckOutput) -> Optional[CheckerReply]:
    docs = sorted(state.get("extractions", []), key=lambda d: d["document"])
    body = ["## Plan", plan.model_dump_json(exclude={"checker"}, exclude_none=True),
            "## rule_check result", rc.model_dump_json(),
            "## Source documents (Intake extractions)", "\n".join(json.dumps(d, separators=(",", ":")) for d in docs)]
    if state.get("human_notes"):
        body += ["## Human planner notes (binding)", "\n".join(f"- {n}" for n in state["human_notes"])]
    messages = [SystemMessage(load_prompt("checker")), HumanMessage("\n\n".join(body))]
    for attempt in range(2):
        reply = call_llm(ctx, "checker", llm, model_id, messages, purpose="review" if attempt == 0 else "schema repair")
        try:
            parsed = CheckerReply.model_validate(parse_json_reply(reply))
            if parsed.verdict.strip().lower() in ("pass", "revise"):
                return parsed
            err = f"verdict must be 'pass' or 'revise', got {parsed.verdict!r}"
        except (ValueError, ValidationError) as exc:
            err = str(exc)[:1000]
        messages += [reply, HumanMessage(f"Invalid reply: {err}. Reply with the JSON object only.")]
    return None
