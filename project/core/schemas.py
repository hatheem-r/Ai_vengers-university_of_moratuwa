"""Shared Pydantic contracts.

PROVISIONAL. Owner: Hatheem (see TASKS.md §0.2). Shazan wrote this draft so the
harness, tracing and tools could start before the Sat 21 Nov freeze landed.
Every field follows docs/API.md §5 (WorkOrderPlan) and §6 (TraceEvent) and the
runner interface in TASKS.md §0.2. Hatheem: replace or merge freely; if a field
changes, update API.md in the same PR.

Not here yet: extraction schemas (Intake output). Tool input/output models are
added with each tool (section "Tool contracts" at the end).
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


# ---------------------------------------------------------------------------
# Tool contracts: mrp_tool (Shazan)
# ---------------------------------------------------------------------------
# Inputs are what an agent extracts from the bundle. Outputs reuse the plan's own
# MaterialRequirement / Shortage / SuggestedPO models so they can be copied into
# the WorkOrderPlan unchanged.


class MrpWorkOrder(_Model):
    wo_id: str
    item_code: str
    qty: float = Field(gt=0)
    uom: str | None = None
    bom_version: str | None = Field(default=None, description="BOM version to use; required if the item has several")
    need_by: date | None = Field(default=None, description="Date the materials must be on hand (default: planning_start)")


class BomLine(_Model):
    parent_item: str
    component_item: str
    qty_per: float = Field(gt=0, description="Component quantity per 1 unit of the parent, before wastage")
    uom: str = Field(description="Unit of qty_per")
    wastage_pct: float = Field(default=0, ge=0, lt=100)
    bom_version: str | None = None


class StockRecord(_Model):
    item_code: str
    on_hand_qty: float
    uom: str
    reserved_qty: float = Field(default=0, ge=0)
    quality_hold_qty: float = Field(default=0, ge=0, description="On hand but blocked (QC hold, rejected)")


class OpenPO(_Model):
    po_number: str
    item_code: str
    open_qty: float = Field(ge=0, description="Quantity ordered and not yet received")
    uom: str
    expected_date: date
    supplier_id: str | None = None


class SupplierOption(_Model):
    item_code: str
    supplier_id: str
    lead_time_days: int = Field(ge=0, description="Calendar days from ordering to arrival")
    moq: float = Field(default=0, ge=0, description="Minimum order quantity")
    pack_size: float | None = Field(default=None, gt=0, description="Order in multiples of this")
    unit_price: float | None = Field(default=None, ge=0)


class UomConversion(_Model):
    from_uom: str
    to_uom: str
    factor: float = Field(gt=0, description="1 from_uom = factor to_uom")
    item_code: str | None = Field(default=None, description="Only for this item (e.g. fabric kg <-> m)")


class MrpInput(_Model):
    planning_start: date
    work_orders: list[MrpWorkOrder] = Field(min_length=1)
    bom: list[BomLine]
    stock: list[StockRecord] = Field(default_factory=list)
    open_pos: list[OpenPO] = Field(default_factory=list)
    suppliers: list[SupplierOption] = Field(default_factory=list)
    uom_conversions: list[UomConversion] = Field(default_factory=list)
    safety_stock: dict[str, float] = Field(default_factory=dict, description="item_code -> quantity kept back")


class MrpItemDetail(_Model):
    item_code: str
    uom: str
    level: int = Field(description="BOM level: 1 = direct component of a work order item")
    make_or_buy: Literal["make", "buy"]
    gross_required_qty: float
    wastage_qty: float
    on_hand_qty: float
    reserved_qty: float
    quality_hold_qty: float
    safety_stock_qty: float
    available_now_qty: float
    on_order_by_need_qty: float
    on_order_late_qty: float
    open_pos_used: list[str]
    shortfall_qty: float = Field(description="Quantity not available by its need date")
    uncovered_qty: float = Field(description="Quantity no existing stock or open PO ever covers")
    earliest_need_by: date
    covered_by_date: date | None = Field(description="When all demand is covered by stock and open POs; null if never")


class WoMaterialStatus(_Model):
    wo_id: str
    need_by: date
    material_ready_date: date | None = Field(description="When every material is available; null if a new PO is needed")
    short_items: list[str]


class PlannedProduction(_Model):
    item_code: str
    qty: float
    uom: str
    need_by: date
    for_work_orders: list[str]


class MrpResult(_Model):
    material_requirements: list[MaterialRequirement]
    shortages: list[Shortage]
    suggested_pos: list[SuggestedPO]
    planned_production: list[PlannedProduction]
    work_orders: list[WoMaterialStatus]
    details: list[MrpItemDetail]
    issues: list[str]
