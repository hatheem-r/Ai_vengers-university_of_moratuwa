"""Shared Pydantic contracts.

PROVISIONAL. Owner: Hatheem (see TASKS.md §0.2). Shazan wrote this draft so the
harness, tracing and tools could start before the Sat 21 Nov freeze landed.
Every field follows docs/API.md §5 (WorkOrderPlan) and §6 (TraceEvent) and the
runner interface in TASKS.md §0.2. Hatheem: replace or merge freely; if a field
changes, update API.md in the same PR.

Not here yet: extraction schemas (Intake output) and tool input/output models.
Tool models are added with each tool.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# WorkOrderPlan (API.md §5)
# ---------------------------------------------------------------------------

SystemId = Literal["B", "M-S", "M-mix", "human"]
FlagType = Literal[
    "conflict",
    "missing_field",
    "shortage",
    "supplier_delay",
    "machine_down",
    "capacity_overload",
    "due_date_risk",
    "compliance_breach",
    "invoice_po_mismatch",
    "needs_approval",
]
Severity = Literal["low", "medium", "high"]
ShortageCause = Literal["insufficient_stock", "supplier_delay", "quality_reject", "other"]


class Operation(_Model):
    seq: int
    operation: str
    machine_id: str
    start: datetime
    end: datetime
    qty: float
    uses_overtime: bool = False


class WorkOrder(_Model):
    wo_id: str
    item_code: str
    qty: float
    uom: str
    customer_po: str
    variant: dict[str, str] | None = None
    bom_version: str | None = None
    priority: int = Field(ge=1)
    due_date: date
    planned_start: datetime
    planned_finish: datetime
    is_late: bool
    days_late: int = Field(ge=0)
    operations: list[Operation]


class MaterialRequirement(_Model):
    item_code: str
    uom: str
    required_qty: float
    on_hand_qty: float
    reserved_qty: float
    on_order_qty: float
    net_shortage_qty: float = Field(ge=0)
    for_work_orders: list[str]


class Shortage(_Model):
    item_code: str
    uom: str
    shortfall_qty: float
    need_by: date
    affected_work_orders: list[str]
    cause: ShortageCause


class SuggestedPO(_Model):
    item_code: str
    qty: float
    uom: str
    supplier_id: str | None = None
    need_by: date
    expedite: bool
    reason: str


class SourceRef(_Model):
    document: str
    field: str
    value: str | float | int | None = None
    page: int | None = None
    row: int | None = None


class Flag(_Model):
    flag_id: str
    type: FlagType
    severity: Severity
    message: str
    sources: list[SourceRef]
    needs_human: bool
    resolution: str | None = None


class CheckerReview(_Model):
    verdict: Literal["pass", "revise", "fail_after_revision"]
    revisions_used: int = Field(ge=0, le=1)
    issues: list[str]
    open_issues: list[str]


class WorkOrderPlan(_Model):
    schema_version: str = SCHEMA_VERSION
    bundle_id: str
    system_id: SystemId
    created_at: datetime
    planning_start: date
    work_orders: list[WorkOrder]
    material_requirements: list[MaterialRequirement]
    shortages: list[Shortage]
    suggested_pos: list[SuggestedPO]
    flags: list[Flag]
    assumptions: list[str]
    rationale: str
    checker: CheckerReview | None = None


# ---------------------------------------------------------------------------
# TraceEvent (API.md §6)
# ---------------------------------------------------------------------------

TraceEventType = Literal[
    "run_start",
    "agent_start",
    "llm_call",
    "tool_call",
    "handoff",
    "checker_verdict",
    "interrupt",
    "human_decision",
    "agent_end",
    "run_end",
    "error",
]
AgentName = Literal["intake", "planner", "checker", "agent", "approval"]


class TraceEvent(_Model):
    run_id: str
    seq: int
    ts: datetime
    type: TraceEventType
    agent: AgentName | None = None
    item: str | None = None
    model: str | None = None
    tool: str | None = None
    # Token semantics: input_tokens is the TOTAL prompt size, including cached
    # tokens (LangChain usage_metadata convention). cached_input_tokens is the
    # part read from cache. Cache writes are folded into input_tokens; cost_usd
    # prices them correctly.
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    cost_usd: float | None = None
    from_agent: str | None = None
    to_agent: str | None = None
    summary: str = ""
    payload: dict[str, Any] | None = None
    payload_ref: str | None = None


# ---------------------------------------------------------------------------
# Runner contract (TASKS.md §0.2)
# ---------------------------------------------------------------------------


class Budgets(_Model):
    """Per-run limits, identical for every system (TASKS.md §0.1)."""

    max_llm_calls: int = 40
    max_tokens: int = 1_500_000
    max_seconds: int = 600


class RunConfig(_Model):
    mode: Literal["multi_agent", "baseline"]
    model_profile: Literal["mix", "sonnet"] = "mix"
    auto_approve: bool = False
    budgets: Budgets = Field(default_factory=Budgets)
    seed: int | None = None


ErrorCode = Literal["budget_exceeded", "schema_invalid", "tool_error", "model_error", "internal"]


class RunError(_Model):
    code: ErrorCode
    message: str


class RunResult(_Model):
    status: Literal["awaiting_approval", "completed", "failed"]
    plan: WorkOrderPlan | None = None
    error: RunError | None = None


class ApprovalDecision(_Model):
    decision: Literal["approve", "reject"]
    notes: str | None = Field(default=None, max_length=2000)


def system_id_for(config: RunConfig) -> SystemId:
    """baseline -> B, multi_agent + sonnet -> M-S, multi_agent + mix -> M-mix (API.md §4.3)."""
    if config.mode == "baseline":
        return "B"
    return "M-S" if config.model_profile == "sonnet" else "M-mix"
