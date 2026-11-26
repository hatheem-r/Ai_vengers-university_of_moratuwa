"""Tool registry for the Planner and Checker: one place to swap stubs for real tools.

Resolution order for each tool:
  1. an override from `set_tool(name, fn)` (tests);
  2. Shazan's implementation, if `project/tools/<module>.py` defines `<function>`
     (skipped when the env var WOP_STUB_TOOLS=1);
  3. the stub in multi_agent/stub_tools.py.

A tool function takes the input model and returns the output model (a dict that
validates into it is accepted too). Every call is traced as a `tool_call` event whose
payload says whether a stub ran.
"""
from __future__ import annotations

import importlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from pydantic import BaseModel

from core.schemas import (AgentName, MrpToolInput, MrpToolOutput, RuleCheckInput, RuleCheckOutput,
                          ScheduleToolInput, ScheduleToolOutput)

from . import stub_tools
from .context import RunContext


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    real: tuple[str, str]           # (module, function) in project/tools/
    stub: Callable[[Any], BaseModel]


SPECS: dict[str, ToolSpec] = {
    "mrp_tool": ToolSpec(
        "mrp_tool",
        "Material requirements planning. Explodes the BoM (with wastage) for each demand, nets it "
        "against usable stock (on hand - reserved - on hold) and open supplier POs that are usable "
        "before need_by, and returns material requirements, shortages, suggested POs and the date "
        "each work order's materials are ready.",
        MrpToolInput, MrpToolOutput, ("tools.mrp", "mrp_tool"), stub_tools.stub_mrp_tool),
    "schedule_tool": ToolSpec(
        "schedule_tool",
        "Finite-capacity scheduler. Schedules every job's operations on its work centres, respecting "
        "work-centre capacity (units x efficiency), shifts, rest days, holidays, downtime, WIP already "
        "loaded and each job's earliest_start. Returns start/finish per operation, lateness and conflicts.",
        ScheduleToolInput, ScheduleToolOutput, ("tools.schedule", "schedule_tool"), stub_tools.stub_schedule_tool),
    "rule_check": ToolSpec(
        "rule_check",
        "Checks a plan against hard rules: double booking, downtime, material readiness, operation "
        "order, rest days, overtime limits, unknown work centres, quantity mismatches, missing flags.",
        RuleCheckInput, RuleCheckOutput, ("tools.rules", "rule_check"), stub_tools.stub_rule_check),
}

_OVERRIDES: dict[str, Callable[[Any], Any]] = {}


def set_tool(name: str, fn: Optional[Callable[[Any], Any]]) -> None:
    """Override one tool (None removes the override)."""
    if name not in SPECS:
        raise KeyError(f"unknown tool {name!r}")
    if fn is None:
        _OVERRIDES.pop(name, None)
    else:
        _OVERRIDES[name] = fn


def resolve(name: str) -> tuple[Callable[[Any], Any], bool]:
    """(implementation, is_stub)."""
    spec = SPECS[name]
    if name in _OVERRIDES:
        return _OVERRIDES[name], False
    if os.environ.get("WOP_STUB_TOOLS", "0") != "1":
        module, func = spec.real
        try:
            fn = getattr(importlib.import_module(module), func, None)
        except ImportError:
            fn = None
        if callable(fn):
            return fn, False
    return spec.stub, True


def run_tool(ctx: RunContext, agent: AgentName, name: str, args: Any) -> BaseModel:
    """Validate args, run the tool, validate its output, emit a `tool_call` event.

    Raises pydantic.ValidationError on bad args (the Planner gets the error back)."""
    spec = SPECS[name]
    inp = args if isinstance(args, spec.input_model) else spec.input_model.model_validate(args)
    fn, is_stub = resolve(name)
    t0 = time.monotonic()
    out = fn(inp)
    latency_ms = int((time.monotonic() - t0) * 1000)
    out = out if isinstance(out, spec.output_model) else spec.output_model.model_validate(out)
    ctx.record_tool()
    ctx.emit("tool_call", agent=agent, tool=name, latency_ms=latency_ms, cost_usd=0,
             summary=f"{name}{' (stub)' if is_stub else ''}: {summarise(name, out)}",
             payload={"stub": is_stub, "summary": summarise(name, out)})
    return out


def summarise(name: str, out: BaseModel) -> str:
    if isinstance(out, MrpToolOutput):
        s = ", ".join(f"{x.item_code} {x.shortfall_qty:g} {x.uom}" for x in out.shortages[:3])
        return f"{len(out.material_requirements)} components, {len(out.shortages)} shortage(s)" + (f" ({s})" if s else "")
    if isinstance(out, ScheduleToolOutput):
        late = sum(w.is_late for w in out.scheduled)
        return f"{out.status}, {len(out.scheduled)} WO scheduled, {late} late, {len(out.conflicts)} conflict(s)"
    if isinstance(out, RuleCheckOutput):
        return "passed" if out.passed else f"{len(out.violations)} violation(s)"
    return type(out).__name__


def anthropic_tool_defs(names: list[str]) -> list[dict]:
    """Tool definitions in the Anthropic format, for `bind_tools`."""
    return [{"name": n, "description": SPECS[n].description,
             "input_schema": SPECS[n].input_model.model_json_schema()} for n in names]


def dump(model: BaseModel) -> str:
    return json.dumps(model.model_dump(mode="json", exclude_none=True), separators=(",", ":"))
