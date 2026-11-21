"""Shared contracts for the Work Order Planner (source of truth for docs/API.md §5 and §6).

Sections
--------
1. Common types and enums
2. WorkOrderPlan (API.md §5): the output of every system (B, M-S, M-mix, human)
3. AnswerKey: WorkOrderPlan plus the scoring-only fields in data/bundles/<id>/answer_key.json
4. Extraction schemas: what the Intake agent returns per document
   (same shape as data/bundles/<id>/extraction_key.json)
5. Tool input/output models: mrp_tool, schedule_tool, rule_check (implemented by Shazan)
6. Run contracts: RunConfig, RunResult, ApprovalDecision (used by core/runner.py)
7. TraceEvent (API.md §6). Stub: Shazan owns the final form in core/tracing.py

Conventions
-----------
- Dates are ISO dates. Datetimes carry a UTC offset; a naive datetime is read as factory
  local time (Asia/Colombo, +05:30), because LLMs often drop the offset.
- Quantities in extractions and plans are in the item's base unit of measure (pcs, kg, m,
  cone, ...). Converting "Gross" or "Thousand" to base units is the Intake agent's job.
- Plan models forbid unknown fields, so a typo in an LLM's JSON fails validation and
  triggers the harness's schema-repair retry instead of being silently dropped.
- Changing anything here: follow API.md §8 (PR that changes this file and API.md together).
"""
from __future__ import annotations

import datetime as _dt
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import (AfterValidator, BaseModel, ConfigDict, Field, TypeAdapter, computed_field,
                      field_validator, model_validator)

SCHEMA_VERSION = "1.0"
FACTORY_TZ = timezone(timedelta(hours=5, minutes=30), name="Asia/Colombo")


# ---------------------------------------------------------------------------
# 1. Common types and enums
# ---------------------------------------------------------------------------

def _ensure_tz(value: datetime) -> datetime:
    return value.replace(tzinfo=FACTORY_TZ) if value.tzinfo is None else value


LocalDateTime = Annotated[datetime, AfterValidator(_ensure_tz)]
"""A datetime that always has an offset; naive input is taken as factory local time."""

SystemId = Literal["B", "M-S", "M-mix", "human"]
RunMode = Literal["multi_agent", "baseline"]
ModelProfile = Literal["mix", "sonnet"]
AgentName = Literal["intake", "planner", "checker", "agent", "approval"]
"""Step keys from API.md §4.4. `agent` is the single baseline agent."""

Severity = Literal["low", "medium", "high"]
FlagType = Literal[
    "conflict", "missing_field", "shortage", "supplier_delay", "machine_down",
    "capacity_overload", "due_date_risk", "compliance_breach", "invoice_po_mismatch",
    "needs_approval",
]
ShortageCause = Literal["insufficient_stock", "supplier_delay", "quality_reject", "other"]
CheckerVerdict = Literal["pass", "revise", "fail_after_revision"]
RunStatus = Literal["queued", "running", "awaiting_approval", "completed", "failed"]
ErrorCode = Literal["budget_exceeded", "schema_invalid", "tool_error", "model_error", "internal"]

Qty = Annotated[float, Field(ge=0)]


class _Strict(BaseModel):
    """Base for contract models: unknown fields are an error."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def _dupes(values: list[str]) -> list[str]:
    seen: set[str] = set()
    return sorted({v for v in values if v in seen or seen.add(v)})


# ---------------------------------------------------------------------------
# 2. WorkOrderPlan (API.md §5)
# ---------------------------------------------------------------------------

class SourceRef(_Strict):
    """Where a fact came from. A flag only scores if it names the right document or field."""
    document: str = Field(description="File name in inputs/, e.g. 'customer_po_450009009.pdf'")
    field: str = Field(description="Field name, e.g. 'delivery_date' or 'on_hand'")
    value: Union[str, float, int, None] = None
    page: Optional[int] = Field(default=None, ge=1)
    row: Optional[int] = Field(default=None, ge=1)


class Variant(_Strict):
    """Apparel variant. Data uses one work order per PO line with a size breakdown
    (`sizes`); a single-size order may use `size` instead."""
    colour: Optional[str] = None
    size: Optional[str] = None
    sizes: Optional[dict[str, int]] = None


class Operation(_Strict):
    seq: int = Field(ge=0)
    operation: str
    machine_id: str = Field(description="Master-data work centre / machine ID, e.g. 'LINE-2'")
    start: LocalDateTime
    end: LocalDateTime
    qty: Qty
    uses_overtime: bool = False

    @model_validator(mode="after")
    def _end_after_start(self) -> "Operation":
        if self.end < self.start:
            raise ValueError(f"operation {self.seq} ends before it starts")
        return self


class WorkOrder(_Strict):
    wo_id: str = Field(description="'WO-001', unique in the plan")
    item_code: str = Field(description="Master-data item code, not the name or buyer article number")
    qty: float = Field(gt=0)
    uom: str
    customer_po: str
    variant: Optional[Variant] = None
    bom_version: Optional[str] = None
    priority: int = Field(ge=1, description="1 = highest")
    due_date: date
    planned_start: LocalDateTime
    planned_finish: LocalDateTime
    is_late: bool
    days_late: int = Field(ge=0, description="Working days late, 0 if on time")
    operations: list[Operation]

    @model_validator(mode="after")
    def _consistent(self) -> "WorkOrder":
        if self.planned_finish < self.planned_start:
            raise ValueError(f"{self.wo_id}: planned_finish is before planned_start")
        if self.is_late != (self.days_late > 0):
            raise ValueError(f"{self.wo_id}: is_late={self.is_late} but days_late={self.days_late}")
        seqs = [op.seq for op in self.operations]
        if seqs != sorted(seqs) or len(set(seqs)) != len(seqs):
            raise ValueError(f"{self.wo_id}: operations must be in strictly increasing seq order")
        return self


class MaterialRequirement(_Strict):
    item_code: str
    uom: str
    required_qty: Qty = Field(description="Including wastage")
    on_hand_qty: Qty
    reserved_qty: Qty = 0
    on_order_qty: Qty = Field(default=0, description="Open POs usable before the need date")
    net_shortage_qty: Qty
    for_work_orders: list[str]


class Shortage(_Strict):
    item_code: str
    uom: str
    shortfall_qty: float = Field(gt=0)
    need_by: date
    affected_work_orders: list[str]
    cause: ShortageCause


class SuggestedPO(_Strict):
    item_code: str
    qty: float = Field(gt=0)
    uom: str
    supplier_id: Optional[str] = None
    need_by: date
    expedite: bool = False
    reason: str


class Flag(_Strict):
    flag_id: str = Field(description="'F1', 'F2', ...")
    type: FlagType
    severity: Severity
    message: str = Field(description="One sentence a planner can act on")
    sources: list[SourceRef]
    needs_human: bool = Field(description="True when the system did not resolve it")
    resolution: Optional[str] = None


class CheckerReview(_Strict):
    verdict: CheckerVerdict
    revisions_used: int = Field(ge=0, le=1)
    issues: list[str] = []
    open_issues: list[str] = []


class WorkOrderPlan(_Strict):
    """The one output format every system produces and score.py scores."""
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    bundle_id: str
    system_id: SystemId
    created_at: LocalDateTime
    planning_start: date
    work_orders: list[WorkOrder]
    material_requirements: list[MaterialRequirement]
    shortages: list[Shortage] = []
    suggested_pos: list[SuggestedPO] = []
    flags: list[Flag] = []
    assumptions: list[str] = []
    rationale: str
    checker: Optional[CheckerReview] = None

    @model_validator(mode="after")
    def _references(self) -> "WorkOrderPlan":
        wo_ids = [wo.wo_id for wo in self.work_orders]
        if d := _dupes(wo_ids):
            raise ValueError(f"duplicate wo_id: {d}")
        if d := _dupes([f.flag_id for f in self.flags]):
            raise ValueError(f"duplicate flag_id: {d}")
        known = set(wo_ids)
        refs = [w for m in self.material_requirements for w in m.for_work_orders]
        refs += [w for s in self.shortages for w in s.affected_work_orders]
        if unknown := sorted(set(refs) - known):
            raise ValueError(f"references to unknown work orders: {unknown}")
        return self


# ---------------------------------------------------------------------------
# 3. Answer key (data/bundles/<id>/answer_key.json). Format still to agree with Ajey.
# ---------------------------------------------------------------------------

class AnswerKeySuggestedPO(SuggestedPO):
    expected_usable_date: Optional[date] = Field(
        default=None, description="Arrival + receiving/QC day; the earliest the material can be used")


class AnswerKeyFlag(Flag):
    required: bool = Field(description="True: a system must raise it. False: optional, not penalised")


class AnswerKey(WorkOrderPlan):
    """Reference plan from data/generator/solver.py plus scoring hints. Never shown to a system."""
    system_id: Literal["answer_key"] = "answer_key"  # type: ignore[assignment]
    suggested_pos: list[AnswerKeySuggestedPO] = []  # type: ignore[assignment]
    flags: list[AnswerKeyFlag] = []  # type: ignore[assignment]
    scoring_notes: dict[str, str] = {}
    ot_work_centres: list[str] = Field(default=[], description="Work centres where the solver used overtime")


# ---------------------------------------------------------------------------
# 4. Extraction schemas (Intake output; same shape as extraction_key.json)
#
# One ExtractedDocument per input file: {document, doc_type, fields, lines}.
# `fields` holds header values, `lines` holds table rows. Values are normalised:
# ISO dates, base units of measure, master-data item codes where the document allows it.
# Columns that only one ERP has (Odoo / SAP B1 / Tally) are optional.
# ---------------------------------------------------------------------------

DocType = Literal[
    "customer_po", "sales_order_confirmation", "bill_of_materials", "routing",
    "operation_bulletin", "stock_report", "capacity_shift_plan", "production_wip_report",
    "maintenance_log", "supplier_purchase_order", "supplier_tax_invoice", "delivery_note",
    "supplier_email", "factory_policy", "other",
]


class _NoFields(_Strict):
    pass


class _ExtractedBase(_Strict):
    document: str = Field(description="File name in inputs/")
    issues: list[str] = Field(
        default=[], description="Intake only: unreadable, ambiguous or missing values. Not in extraction_key.json")


# customer_po ---------------------------------------------------------------
class CustomerPOFields(_Strict):
    po_number: str
    po_date: Optional[date] = None
    customer: Optional[str] = None
    currency: Optional[str] = None
    delivery_date: Optional[date] = Field(default=None, description="None when the PO omits it (flag missing_field)")


class CustomerPOLine(_Strict):
    item_code: Optional[str] = Field(description="Master-data code; None if it cannot be mapped")
    buyer_ref: Optional[str] = Field(default=None, description="Buyer's article number / SKU as printed")
    qty: Qty
    uom: str
    unit_price: Optional[float] = None
    sizes: Optional[dict[str, int]] = None


class CustomerPODocument(_ExtractedBase):
    doc_type: Literal["customer_po"] = "customer_po"
    fields: CustomerPOFields
    lines: list[CustomerPOLine]

    @model_validator(mode="before")
    @classmethod
    def _lift_lines(cls, data: Any) -> Any:
        # extraction_key.json repeats the lines inside `fields`; the canonical place is top-level.
        if isinstance(data, dict) and isinstance(data.get("fields"), dict) and "lines" in data["fields"]:
            inner = data["fields"]["lines"]
            data = {**data, "fields": {k: v for k, v in data["fields"].items() if k != "lines"}}
            if not data.get("lines"):
                data["lines"] = inner
        return data


# sales_order_confirmation ---------------------------------------------------
class SalesOrderConfirmationFields(_Strict):
    order: str
    customer_reference: Optional[str] = None
    item_code: Optional[str] = None
    qty: Optional[Qty] = None
    commitment_date: Optional[date] = None


class SalesOrderConfirmationDocument(_ExtractedBase):
    doc_type: Literal["sales_order_confirmation"] = "sales_order_confirmation"
    fields: SalesOrderConfirmationFields
    lines: list[dict[str, Any]] = []


# bill_of_materials -----------------------------------------------------------
class BomFields(_Strict):
    products: list[str] = Field(description="Parent item codes covered by this BoM file")


class BomLine(_Strict):
    parent: str
    component: str
    qty_per: float = Field(ge=0, description="Per 1 unit of parent, before wastage")
    uom: str
    wastage_pct: float = Field(default=0, ge=0)
    consumed_in: Optional[str] = Field(default=None, description="Operation that consumes it (apparel tech pack)")
    qty_per_1000_incl_loss: Optional[float] = Field(default=None, description="As printed in Tally BoMs")


class BomDocument(_ExtractedBase):
    doc_type: Literal["bill_of_materials"] = "bill_of_materials"
    fields: BomFields
    lines: list[BomLine]


# routing ---------------------------------------------------------------------
class RoutingLine(_Strict):
    style: Optional[str] = Field(default=None, description="Apparel style number (profile A)")
    item: Optional[str] = Field(default=None, description="Item code (profiles B and C)")
    seq: int
    operation: str
    work_centre: str
    std_min_per_unit: float = Field(ge=0)
    setup_min: float = Field(default=0, ge=0)
    rated_units_per_min: Optional[float] = None


class RoutingDocument(_ExtractedBase):
    doc_type: Literal["routing"] = "routing"
    fields: _NoFields = _NoFields()
    lines: list[RoutingLine]


# operation_bulletin ----------------------------------------------------------
class OperationBulletinLine(_Strict):
    style: str
    operation: str
    machine_type: str
    smv: float = Field(ge=0)


class OperationBulletinDocument(_ExtractedBase):
    doc_type: Literal["operation_bulletin"] = "operation_bulletin"
    fields: _NoFields = _NoFields()
    lines: list[OperationBulletinLine]


# stock_report ----------------------------------------------------------------
class StockReportFields(_Strict):
    as_of: Optional[date] = None
    system: Optional[Literal["odoo", "sapb1", "tally"]] = None


class StockLine(_Strict):
    """One stock row. Quantities in base `uom`; `shown_uom` is what the document printed."""
    item_code: str
    uom: str
    shown_uom: Optional[str] = None
    status: Optional[str] = Field(default=None, description="'OK', 'Quality hold', ...")
    # Odoo
    location: Optional[str] = None
    lot: Optional[str] = None
    on_hand: Optional[float] = None
    reserved: Optional[float] = None
    # SAP Business One
    warehouse: Optional[str] = None
    in_stock: Optional[float] = None
    committed: Optional[float] = None
    ordered: Optional[float] = None
    # Tally
    godown: Optional[str] = None
    batch: Optional[str] = None
    qty: Optional[float] = None
    expiry: Optional[date] = None

    @property
    def physical_qty(self) -> float:
        """On-hand quantity whatever the ERP called it."""
        return next((v for v in (self.on_hand, self.in_stock, self.qty) if v is not None), 0.0)

    @property
    def allocated_qty(self) -> float:
        return next((v for v in (self.reserved, self.committed) if v is not None), 0.0)


class StockReportDocument(_ExtractedBase):
    doc_type: Literal["stock_report"] = "stock_report"
    fields: StockReportFields
    lines: list[StockLine]


# capacity_shift_plan -----------------------------------------------------------
class OvertimeProposal(_Strict):
    wc: str
    proposed_by: Optional[str] = None
    weekday_ot_hours: Optional[float] = None
    sunday_hours: Optional[float] = None
    weekly_total_hours: Optional[float] = None
    weeks: Optional[int] = None


class CapacityShiftPlanFields(_Strict):
    ot_proposal: Optional[OvertimeProposal] = None


class CapacityLine(_Strict):
    work_centre: str
    units: int = Field(ge=1, description="Parallel machines / operators counted as one resource")
    efficiency: float = Field(gt=0, le=1.5)
    regular_min_per_day: int = Field(default=480, ge=0)
    ot_max_min_per_day: int = Field(default=0, ge=0)
    alternates: list[str] = []
    status: str = Field(default="Running", description="'Running' or 'DOWN'")


class CapacityShiftPlanDocument(_ExtractedBase):
    doc_type: Literal["capacity_shift_plan"] = "capacity_shift_plan"
    fields: CapacityShiftPlanFields = CapacityShiftPlanFields()
    lines: list[CapacityLine]


# production_wip_report -----------------------------------------------------------
class WipReportFields(_Strict):
    date: Optional[_dt.date] = None  # module-qualified: the field name shadows the type


class WipLine(_Strict):
    order: str
    item: str
    work_centre: str
    remaining_qty: Qty
    load_minutes: Qty


class WipReportDocument(_ExtractedBase):
    doc_type: Literal["production_wip_report"] = "production_wip_report"
    fields: WipReportFields = WipReportFields()
    lines: list[WipLine]


# maintenance_log -------------------------------------------------------------------
class MaintenanceLine(_Strict):
    work_centre: str
    machine: str
    start: date
    status: str = Field(description="'Open' or 'Closed'")
    unavailable_until_incl: Optional[date] = None
    expected_return: Optional[date] = None


class MaintenanceLogDocument(_ExtractedBase):
    doc_type: Literal["maintenance_log"] = "maintenance_log"
    fields: _NoFields = _NoFields()
    lines: list[MaintenanceLine]


# supplier_purchase_order --------------------------------------------------------------
class SupplierPOFields(_Strict):
    po_number: str
    supplier_id: Optional[str] = None
    item_code: str
    qty: Qty
    uom: str
    expected_arrival_printed: Optional[date] = None


class SupplierPODocument(_ExtractedBase):
    doc_type: Literal["supplier_purchase_order"] = "supplier_purchase_order"
    fields: SupplierPOFields
    lines: list[dict[str, Any]] = []


# supplier_tax_invoice and delivery_note (same fields) -----------------------------------
class SupplyDocumentFields(_Strict):
    document_no: str
    supplier_id: Optional[str] = None
    item_code: str
    lot: Optional[str] = None
    qty: Qty
    uom: str
    currency: Optional[str] = None
    unit_price: Optional[float] = None
    date_of_supply: Optional[date] = None


class SupplierInvoiceDocument(_ExtractedBase):
    doc_type: Literal["supplier_tax_invoice"] = "supplier_tax_invoice"
    fields: SupplyDocumentFields
    lines: list[dict[str, Any]] = []


class DeliveryNoteDocument(_ExtractedBase):
    doc_type: Literal["delivery_note"] = "delivery_note"
    fields: SupplyDocumentFields
    lines: list[dict[str, Any]] = []


# supplier_email ---------------------------------------------------------------------------
class SupplierEmailFields(_Strict):
    po_number: str
    item_code: Optional[str] = None
    old_eta: Optional[date] = None
    new_eta: Optional[date] = None


class SupplierEmailDocument(_ExtractedBase):
    doc_type: Literal["supplier_email"] = "supplier_email"
    fields: SupplierEmailFields
    lines: list[dict[str, Any]] = []


# factory_policy ---------------------------------------------------------------------------
class FactoryPolicyFields(_Strict):
    regular_hours_week: float = 48
    max_total_hours_week: float = 60
    max_ot_hours_day: float = 2
    max_ot_hours_week: float = 12
    days_off_per_7: int = 1
    ot_voluntary: bool = True
    ot_premium_pct: Optional[float] = None


class FactoryPolicyDocument(_ExtractedBase):
    doc_type: Literal["factory_policy"] = "factory_policy"
    fields: FactoryPolicyFields
    lines: list[dict[str, Any]] = []


# anything Intake cannot classify -----------------------------------------------------------
class OtherDocument(_ExtractedBase):
    doc_type: Literal["other"] = "other"
    fields: dict[str, Any] = {}
    lines: list[dict[str, Any]] = []


ExtractedDocument = Annotated[
    Union[
        CustomerPODocument, SalesOrderConfirmationDocument, BomDocument, RoutingDocument,
        OperationBulletinDocument, StockReportDocument, CapacityShiftPlanDocument, WipReportDocument,
        MaintenanceLogDocument, SupplierPODocument, SupplierInvoiceDocument, DeliveryNoteDocument,
        SupplierEmailDocument, FactoryPolicyDocument, OtherDocument,
    ],
    Field(discriminator="doc_type"),
]
"""Intake output for one file. Validate a dict with `parse_extracted_document`."""

_EXTRACTED_ADAPTER: TypeAdapter = TypeAdapter(ExtractedDocument)


def parse_extracted_document(data: Any) -> Any:
    """Validate one extracted document (dict or JSON string) into its typed model."""
    if isinstance(data, (str, bytes)):
        return _EXTRACTED_ADAPTER.validate_json(data)
    return _EXTRACTED_ADAPTER.validate_python(data)


class BundleExtraction(_Strict):
    """All Intake outputs for one bundle (the shape of extraction_key.json)."""
    bundle_id: str
    documents: list[ExtractedDocument]

    def of_type(self, doc_type: DocType) -> list[Any]:
        return [d for d in self.documents if d.doc_type == doc_type]


# ---------------------------------------------------------------------------
# 5. Tool input/output models (tools implemented by Shazan in project/tools/)
#
# Tools take normalised, ERP-independent inputs. Where an extraction model already
# fits (BomLine, CapacityLine, FactoryPolicyFields) it is reused, so the Planner can
# pass Intake output straight through. Outputs reuse the plan models, so results can
# be copied into the WorkOrderPlan without renaming.
# ---------------------------------------------------------------------------

class PlanningCalendar(_Strict):
    working_weekdays: list[int] = Field(default=[0, 1, 2, 3, 4, 5], description="0 = Monday; Mon-Sat by default")
    holidays: list[date] = []
    shift_start: time = time(8, 0)


# mrp_tool ------------------------------------------------------------------
class MrpDemand(_Strict):
    wo_id: str
    item_code: str
    qty: float = Field(gt=0)
    uom: str
    need_by: date = Field(description="Date the first operation that consumes material may start")


class StockPosition(_Strict):
    item_code: str
    uom: str
    on_hand_qty: Qty
    reserved_qty: Qty = 0
    on_hold_qty: Qty = Field(default=0, description="Quality hold or expired; not usable")


class OpenSupply(_Strict):
    """An open supplier PO, with the latest known ETA (after any supplier email)."""
    po_number: str
    item_code: str
    qty: Qty
    uom: str
    eta: date
    supplier_id: Optional[str] = None


class SupplierOption(_Strict):
    supplier_id: str
    item_code: str
    lead_time_working_days: int = Field(ge=0)


class MrpToolInput(_Strict):
    planning_start: date
    demands: list[MrpDemand]
    bom: list[BomLine] = Field(description="All levels; sub-assemblies are exploded recursively")
    stock: list[StockPosition]
    open_supply: list[OpenSupply] = []
    suppliers: list[SupplierOption] = Field(default=[], description="Optional; without it suggested POs have no supplier")
    receipt_lag_working_days: int = Field(default=1, ge=0, description="Goods received on day D are usable from D + lag")
    calendar: PlanningCalendar = PlanningCalendar()


class MrpToolOutput(_Strict):
    material_requirements: list[MaterialRequirement]
    shortages: list[Shortage] = []
    suggested_pos: list[SuggestedPO] = []
    material_ready: dict[str, date] = Field(
        default={}, description="wo_id -> earliest date all its materials are usable (feeds schedule_tool)")
    warnings: list[str] = []


# schedule_tool ----------------------------------------------------------------
class ScheduleOperation(_Strict):
    seq: int
    operation: str
    work_centre: str
    std_min_per_unit: float = Field(ge=0)
    setup_min: float = Field(default=0, ge=0)


class ScheduleJob(_Strict):
    wo_id: str
    item_code: str
    qty: float = Field(gt=0)
    due_date: date
    priority: Optional[int] = Field(default=None, ge=1)
    earliest_start: Optional[date] = Field(default=None, description="From MrpToolOutput.material_ready")
    operations: list[ScheduleOperation]


class DowntimeWindow(_Strict):
    work_centre: str
    start: date
    end_incl: date
    reason: Optional[str] = None


class WipLoad(_Strict):
    work_centre: str
    load_minutes: Qty
    order: Optional[str] = None


class ScheduleToolInput(_Strict):
    planning_start: date
    jobs: list[ScheduleJob]
    work_centres: list[CapacityLine]
    downtime: list[DowntimeWindow] = []
    wip: list[WipLoad] = Field(default=[], description="Loaded on its work centre before new jobs")
    calendar: PlanningCalendar = PlanningCalendar()
    allow_overtime: bool = True
    ot_work_centres: Optional[list[str]] = Field(default=None, description="None = any work centre may use overtime")
    objective: Literal["min_lateness", "min_makespan"] = "min_lateness"
    time_limit_s: float = Field(default=10, gt=0)


class ScheduledWorkOrder(_Strict):
    wo_id: str
    planned_start: LocalDateTime
    planned_finish: LocalDateTime
    is_late: bool
    days_late: int = Field(ge=0)
    operations: list[Operation]


class ScheduleConflict(_Strict):
    type: Literal["capacity_overload", "machine_down", "material_late", "due_date_risk", "infeasible"]
    message: str
    wo_id: Optional[str] = None
    work_centre: Optional[str] = None


class ScheduleToolOutput(_Strict):
    status: Literal["optimal", "feasible", "infeasible", "timeout", "error"]
    scheduled: list[ScheduledWorkOrder] = []
    conflicts: list[ScheduleConflict] = []
    infeasibility_reason: Optional[str] = None
    solve_time_s: Optional[float] = None


# rule_check ---------------------------------------------------------------------
RuleId = Literal[
    "double_booking",        # two operations on one work centre at the same time (beyond its units)
    "downtime",              # operation on a work centre that is down
    "material_not_ready",    # operation starts before its material is usable
    "operation_order",       # operation starts before the previous one ends
    "non_working_day",       # work on a holiday or rest day
    "overtime_day_limit",    # more overtime in a day than the policy allows
    "overtime_week_limit",   # more total hours in a week than the policy allows
    "unknown_work_centre",   # machine_id not in the capacity plan
    "qty_mismatch",          # work order qty differs from the customer PO line
    "missing_flag",          # a known problem (late order, shortage, breach) has no flag
]


class RuleCheckInput(_Strict):
    plan: WorkOrderPlan
    policy: FactoryPolicyFields
    work_centres: list[CapacityLine]
    downtime: list[DowntimeWindow] = []
    material_ready: dict[str, date] = Field(default={}, description="wo_id -> date, from mrp_tool")
    order_lines: list[CustomerPOLine] = Field(default=[], description="For qty_mismatch checks")
    calendar: PlanningCalendar = PlanningCalendar()


class RuleViolation(_Strict):
    rule: RuleId
    severity: Severity
    message: str
    wo_id: Optional[str] = None
    seq: Optional[int] = None
    work_centre: Optional[str] = None


class RuleCheckOutput(_Strict):
    violations: list[RuleViolation] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def passed(self) -> bool:
        return not self.violations


# ---------------------------------------------------------------------------
# 6. Run contracts (core/runner.py, API.md §4.3, §4.4, §4.7)
# ---------------------------------------------------------------------------

class Budget(_Strict):
    """Same budgets for baseline and multi-agent (TASKS.md §0.1)."""
    max_llm_calls: int = Field(default=40, ge=1)
    max_tokens: int = Field(default=1_500_000, ge=1)
    max_seconds: float = Field(default=600, gt=0)


class RunConfig(_Strict):
    mode: RunMode
    model_profile: ModelProfile = Field(default="mix", description="Ignored for baseline (always Sonnet)")
    auto_approve: bool = Field(default=False, description="True in the benchmark: skip the human step")
    budget: Budget = Budget()
    seed: Optional[int] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def system_id(self) -> SystemId:
        if self.mode == "baseline":
            return "B"
        return "M-S" if self.model_profile == "sonnet" else "M-mix"


class RunError(_Strict):
    code: ErrorCode
    message: str


class RunResult(_Strict):
    run_id: str
    status: RunStatus
    plan: Optional[WorkOrderPlan] = None
    plan_version: int = Field(default=0, ge=0, description="0 until the first plan exists")
    error: Optional[RunError] = None

    @model_validator(mode="after")
    def _consistent(self) -> "RunResult":
        if self.status == "failed" and self.error is None:
            raise ValueError("a failed run needs an error")
        if self.status in ("awaiting_approval", "completed") and self.plan is None:
            raise ValueError(f"status {self.status} needs a plan")
        return self


class ApprovalDecision(_Strict):
    decision: Literal["approve", "reject"]
    notes: Optional[str] = Field(default=None, max_length=2000)
    decided_at: Optional[LocalDateTime] = None

    @field_validator("notes")
    @classmethod
    def _strip(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() or None if v is not None else None

    @model_validator(mode="after")
    def _notes_for_reject(self) -> "ApprovalDecision":
        if self.decision == "reject" and not self.notes:
            raise ValueError("notes are required when rejecting a plan")
        return self


# ---------------------------------------------------------------------------
# 7. TraceEvent (API.md §6). STUB: Shazan owns the final form (core/tracing.py).
# ---------------------------------------------------------------------------

TraceEventType = Literal[
    "run_start", "agent_start", "llm_call", "tool_call", "handoff", "checker_verdict",
    "interrupt", "human_decision", "agent_end", "run_end", "error",
]


class TraceEvent(_Strict):
    """One line of runs/<run_id>/trace.jsonl. Both systems emit the same events through emit()."""
    run_id: str
    seq: int = Field(ge=1)
    ts: LocalDateTime
    type: TraceEventType
    agent: Optional[AgentName] = None
    item: Optional[str] = Field(default=None, description="Document name for Intake events")
    model: Optional[str] = None
    tool: Optional[str] = None
    input_tokens: Optional[int] = None
    cached_input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    latency_ms: Optional[int] = None
    cost_usd: Optional[float] = None
    from_agent: Optional[AgentName] = None
    to_agent: Optional[AgentName] = None
    summary: str = ""
    payload: Optional[dict[str, Any]] = None
    payload_ref: Optional[str] = None

