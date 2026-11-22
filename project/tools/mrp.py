"""mrp_tool: material requirements planning.

Plain Python, deterministic, no LLM. Written independently of Hatheem's
``data/generator/solver.py`` on purpose: if the two disagree on a dev bundle,
one of them has a bug (TASKS.md §2).

Steps:

1. **BOM explosion.** Each work order's item is exploded through the BOM,
   level by level (low-level coding), applying ``wastage_pct``. Discrete units
   (pcs, sets, ...) are rounded up per work order.
2. **Netting, time-phased.** For every component, demands are sorted by need
   date and served first-in-first-out from supply lots: usable stock now
   (on hand - reserved - quality hold - safety stock), then open POs in order of
   expected date. A demand is short if any part of it is served by a PO that
   arrives after its need date, or by nothing at all.
3. **Made components** (items that have their own BOM): stock is netted first;
   the rest becomes planned production and is exploded further.
4. **Shortages and suggested POs** for bought components. Cause is
   ``supplier_delay`` when an open PO covers it late, ``quality_reject`` when
   quality-held stock would have covered it, otherwise ``insufficient_stock``.
   Suggested POs cover only what no existing supply covers, rounded to MOQ and
   pack size, from the supplier that can deliver earliest.

Everything the tool assumed or could not resolve (unknown units, ambiguous BOM
version, overdue POs, cycles) is listed in ``issues``. Nothing is dropped
silently.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from langchain_core.tools import StructuredTool, ToolException

from project.core.schemas import (
    BomLine,
    MaterialRequirement,
    MrpInput,
    MrpItemDetail,
    MrpResult,
    PlannedProduction,
    Shortage,
    SuggestedPO,
    WoMaterialStatus,
)
from project.tools.common import validation_error_message

EPS = 1e-6

# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------

UOM_ALIASES = {
    "pc": "pcs", "piece": "pcs", "pieces": "pcs", "nos": "pcs", "no": "pcs", "unit": "pcs", "units": "pcs",
    "ea": "pcs", "each": "pcs", "pce": "pcs",
    "kgs": "kg", "kilogram": "kg", "kilograms": "kg", "gm": "g", "gram": "g", "grams": "g", "gms": "g",
    "mtr": "m", "mtrs": "m", "meter": "m", "meters": "m", "metre": "m", "metres": "m",
    "yds": "yd", "yard": "yd", "yards": "yd",
    "dz": "dozen", "doz": "dozen", "dozens": "dozen",
    "ltr": "l", "litre": "l", "liter": "l", "litres": "l", "liters": "l",
    "lbs": "lb", "pound": "lb", "pounds": "lb",
    "ton": "t", "tonne": "t", "tonnes": "t",
    "set": "sets", "roll": "rolls", "box": "boxes", "carton": "cartons", "ctn": "cartons", "ctns": "cartons",
}
# 1 a = factor b
BUILTIN_CONVERSIONS = {
    ("kg", "g"): 1000.0,
    ("t", "kg"): 1000.0,
    ("lb", "kg"): 0.45359237,
    ("m", "cm"): 100.0,
    ("m", "mm"): 1000.0,
    ("yd", "m"): 0.9144,
    ("l", "ml"): 1000.0,
    ("dozen", "pcs"): 12.0,
    ("gross", "pcs"): 144.0,
}
# Units that cannot be split: requirements are rounded up.
DISCRETE_UOMS = {"pcs", "sets", "pairs", "rolls", "boxes", "cartons", "dozen", "gross", "cones", "spools"}


def norm_uom(uom: str | None) -> str:
    u = re.sub(r"[.\s]+", "", (uom or "").strip().lower())
    return UOM_ALIASES.get(u, u)


def norm_code(code: str) -> str:
    return code.strip().upper()


def _round(x: float) -> float:
    return round(x + 0.0, 4)


def _ceil(x: float) -> float:
    return float(math.ceil(round(x, 6)))


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------


@dataclass
class _Demand:
    need_by: date
    wo_id: str
    qty: float
    wastage: float
    seq: int
    # filled in by netting
    late_qty: float = 0.0
    unallocated: float = 0.0
    ready: date | None = None


@dataclass
class _Lot:
    when: date
    qty: float
    po_number: str | None = None
    left: float = field(init=False)

    def __post_init__(self) -> None:
        self.left = self.qty


class _Mrp:
    def __init__(self, inp: MrpInput):
        self.inp = inp
        self.start = inp.planning_start
        self.issues: list[str] = []
        self.names: dict[str, str] = {}  # normalized code -> display code
        self.uom: dict[str, str] = {}  # component -> canonical unit
        self.bom: dict[str, list[BomLine]] = defaultdict(list)
        for line in inp.bom:
            self.bom[norm_code(line.parent_item)].append(line)
            self._name(line.parent_item)
            c = self._name(line.component_item)
            self.uom.setdefault(c, norm_uom(line.uom))
        self.demands: dict[str, list[_Demand]] = defaultdict(list)
        self._seq = 0
        self._warned: set[str] = set()

    # -- helpers -------------------------------------------------------------

    def _name(self, code: str) -> str:
        key = norm_code(code)
        self.names.setdefault(key, code.strip())
        return key

    def issue(self, msg: str) -> None:
        if msg not in self._warned:
            self._warned.add(msg)
            self.issues.append(msg)

    def factor(self, frm: str, to: str, item: str) -> float | None:
        frm, to = norm_uom(frm), norm_uom(to)
        if frm == to:
            return 1.0
        for conv in self.inp.uom_conversions:
            if conv.item_code is not None and norm_code(conv.item_code) != item:
                continue
            a, b = norm_uom(conv.from_uom), norm_uom(conv.to_uom)
            if (a, b) == (frm, to):
                return conv.factor
            if (a, b) == (to, frm):
                return 1.0 / conv.factor
        if (frm, to) in BUILTIN_CONVERSIONS:
            return BUILTIN_CONVERSIONS[(frm, to)]
        if (to, frm) in BUILTIN_CONVERSIONS:
            return 1.0 / BUILTIN_CONVERSIONS[(to, frm)]
        return None

    def convert(self, qty: float, frm: str, item: str, what: str) -> float | None:
        to = self.uom[item]
        f = self.factor(frm, to, item)
        if f is None:
            self.issue(
                f"{self.names[item]}: cannot convert {what} from '{frm}' to '{to}'; it was ignored. "
                "Pass a uom_conversions entry for this item."
            )
            return None
        return qty * f

    def lines_for(self, item: str, version: str | None, wo_id: str | None = None) -> list[BomLine]:
        lines = self.bom.get(item, [])
        if not lines:
            return []
        versions = sorted({l.bom_version for l in lines if l.bom_version}, key=_natural_key)
        if version is not None:
            chosen = [l for l in lines if l.bom_version == version]
            if chosen:
                return chosen
            if versions:
                self.issue(
                    f"{self.names[item]}: BOM version '{version}' not found (have {', '.join(versions)}); "
                    f"used {versions[-1]}."
                )
                return [l for l in lines if l.bom_version == versions[-1]]
            return lines
        if len(versions) > 1:
            self.issue(
                f"{self.names[item]}: several BOM versions ({', '.join(versions)}) and none given"
                f"{f' for {wo_id}' if wo_id else ''}; used {versions[-1]}. Pass bom_version to choose."
            )
        if versions:
            latest = versions[-1]
            return [l for l in lines if l.bom_version in (latest, None)]
        return lines

    # -- explosion -----------------------------------------------------------

    def add_children(self, parent: str, qty: float, need_by: date, wo_id: str, version: str | None) -> None:
        for line in self.lines_for(parent, version, wo_id):
            child = norm_code(line.component_item)
            base = qty * line.qty_per
            f = self.factor(line.uom, self.uom[child], child)
            if f is None:
                self.issue(f"{self.names[child]}: BOM unit '{line.uom}' does not convert to '{self.uom[child]}'; line ignored.")
                continue
            base *= f
            gross = base * (1 + line.wastage_pct / 100.0)
            if self.uom[child] in DISCRETE_UOMS:
                gross = _ceil(gross)
            self._seq += 1
            self.demands[child].append(_Demand(need_by, wo_id, gross, max(gross - base, 0.0), self._seq))

    def levels(self) -> dict[str, int]:
        """Low-level code of each item reachable from the work orders (longest path)."""
        levels: dict[str, int] = {}

        def visit(item: str, depth: int, path: tuple[str, ...], version: str | None) -> None:
            if item in path:
                cycle = " -> ".join(self.names[i] for i in (*path[path.index(item):], item))
                raise ToolException(f"BOM has a cycle: {cycle}")
            if levels.get(item, -1) >= depth and depth > 0:
                return
            levels[item] = max(levels.get(item, -1), depth)
            for line in self.lines_for(item, version):
                visit(norm_code(line.component_item), depth + 1, (*path, item), None)

        for wo in self.inp.work_orders:
            visit(self._name(wo.item_code), 0, (), wo.bom_version)
        return levels

    # -- netting -------------------------------------------------------------

    def supply(self, item: str) -> tuple[dict[str, float], list[_Lot]]:
        on_hand = reserved = hold = 0.0
        for s in self.inp.stock:
            if norm_code(s.item_code) != item:
                continue
            f_q = self.convert(1.0, s.uom, item, f"stock unit")
            if f_q is None:
                continue
            on_hand += s.on_hand_qty * f_q
            reserved += s.reserved_qty * f_q
            hold += s.quality_hold_qty * f_q
        safety = self.inp.safety_stock.get(self.names[item], self.inp.safety_stock.get(item, 0.0))
        avail = on_hand - reserved - hold - safety
        if avail < -EPS:
            self.issue(
                f"{self.names[item]}: reserved + quality hold + safety stock ({_round(reserved + hold + safety)}) "
                f"exceed on hand ({_round(on_hand)}); usable stock taken as 0."
            )
        lots = [_Lot(self.start, max(avail, 0.0))]
        for po in sorted(self.inp.open_pos, key=lambda p: (p.expected_date, p.po_number)):
            if norm_code(po.item_code) != item or po.open_qty <= EPS:
                continue
            qty = self.convert(po.open_qty, po.uom, item, f"PO {po.po_number}")
            if qty is None:
                continue
            when = po.expected_date
            if when < self.start:
                self.issue(
                    f"PO {po.po_number} ({self.names[item]}) was due {when} and is still open; "
                    f"assumed to arrive on {self.start}."
                )
                when = self.start
            lots.append(_Lot(when, qty, po.po_number))
        totals = {"on_hand": on_hand, "reserved": reserved, "hold": hold, "safety": safety, "available": max(avail, 0.0)}
        return totals, lots

    @staticmethod
    def allocate(demands: list[_Demand], lots: list[_Lot]) -> set[str]:
        """FIFO: earliest need first, earliest supply first. Returns PO numbers used."""
        used: set[str] = set()
        i = 0
        for d in sorted(demands, key=lambda d: (d.need_by, d.seq)):
            remaining, ready = d.qty, None
            while remaining > EPS and i < len(lots):
                lot = lots[i]
                if lot.left <= EPS:
                    i += 1
                    continue
                take = min(remaining, lot.left)
                lot.left -= take
                remaining -= take
                ready = lot.when if ready is None else max(ready, lot.when)
                if lot.when > d.need_by:
                    d.late_qty += take
                if lot.po_number:
                    used.add(lot.po_number)
            d.unallocated = remaining if remaining > EPS else 0.0
            d.ready = None if d.unallocated else (ready or lots[0].when)
        return used

    # -- main ----------------------------------------------------------------

    def run(self) -> MrpResult:
        levels = self.levels()
        no_bom: set[str] = set()
        for wo in self.inp.work_orders:
            item = norm_code(wo.item_code)
            if not self.bom.get(item):
                self.issue(f"{wo.wo_id}: no BOM for {wo.item_code}; no materials planned for it.")
                no_bom.add(wo.wo_id)
                continue
            self.add_children(item, wo.qty, wo.need_by or self.start, wo.wo_id, wo.bom_version)

        reqs: list[MaterialRequirement] = []
        shortages: list[Shortage] = []
        pos: list[SuggestedPO] = []
        planned: list[PlannedProduction] = []
        details: list[MrpItemDetail] = []
        wo_ready: dict[str, list[date | None]] = defaultdict(list)
        wo_short: dict[str, list[str]] = defaultdict(list)

        for item in sorted((i for i, lv in levels.items() if lv > 0), key=lambda i: (levels[i], self.names[i])):
            demands = self.demands.get(item, [])
            if not demands:
                continue
            name, uom = self.names[item], self.uom[item]
            is_make = bool(self.bom.get(item))
            totals, lots = self.supply(item)
            used = self.allocate(demands, lots)

            gross = sum(d.qty for d in demands)
            latest_need = max(d.need_by for d in demands)
            earliest_need = min(d.need_by for d in demands)
            on_order_by_need = sum(l.qty for l in lots[1:] if l.when <= latest_need)
            on_order_late = sum(l.qty for l in lots[1:] if l.when > latest_need)
            late = sum(d.late_qty for d in demands)
            uncovered = sum(d.unallocated for d in demands)
            shortfall = late + uncovered
            wos = list(dict.fromkeys(d.wo_id for d in sorted(demands, key=lambda d: (d.need_by, d.seq))))

            if is_make:
                # Unfilled demand becomes production, exploded one level further.
                to_make: dict[str, list[_Demand]] = defaultdict(list)
                for d in demands:
                    if d.unallocated:
                        self.add_children(item, d.unallocated, d.need_by, d.wo_id, None)
                        to_make[d.wo_id].append(d)
                    if d.qty - d.unallocated > EPS:
                        wo_ready[d.wo_id].append(d.ready or self.start)
                if to_make:
                    need = [d for ds in to_make.values() for d in ds]
                    planned.append(PlannedProduction(
                        item_code=name, qty=_round(uncovered), uom=uom,
                        need_by=min(d.need_by for d in need), for_work_orders=list(to_make),
                    ))
                    self.issue(f"{name} is made in-house: {_round(uncovered)} {uom} must be produced for {', '.join(to_make)}.")
                net_shortage = 0.0
            else:
                for d in demands:
                    wo_ready[d.wo_id].append(d.ready)
                    if d.late_qty + d.unallocated > EPS:
                        wo_short[d.wo_id].append(name)
                net_shortage = shortfall
                if shortfall > EPS:
                    short_demands = [d for d in demands if d.late_qty + d.unallocated > EPS]
                    if late > EPS:
                        cause = "supplier_delay"
                    elif totals["hold"] > EPS and uncovered <= totals["hold"] + EPS:
                        cause = "quality_reject"
                    else:
                        cause = "insufficient_stock"
                    shortages.append(Shortage(
                        item_code=name, uom=uom, shortfall_qty=_round(shortfall),
                        need_by=min(d.need_by for d in short_demands),
                        affected_work_orders=list(dict.fromkeys(d.wo_id for d in sorted(short_demands, key=lambda d: (d.need_by, d.seq)))),
                        cause=cause,
                    ))
                    for lot in lots[1:]:
                        late_for = [d for d in demands if lot.when > d.need_by and d.late_qty > EPS]
                        if lot.po_number and lot.po_number in used and late_for:
                            self.issue(
                                f"PO {lot.po_number} ({name}) arrives {lot.when}, after the need date "
                                f"{min(d.need_by for d in late_for)}."
                            )
                if uncovered > EPS:
                    need_by = min(d.need_by for d in demands if d.unallocated)
                    pos.append(self.suggest_po(item, uncovered, need_by))

            reqs.append(MaterialRequirement(
                item_code=name, uom=uom, required_qty=_round(gross), on_hand_qty=_round(totals["on_hand"]),
                reserved_qty=_round(totals["reserved"]), on_order_qty=_round(on_order_by_need),
                net_shortage_qty=_round(net_shortage), for_work_orders=wos,
            ))
            details.append(MrpItemDetail(
                item_code=name, uom=uom, level=levels[item], make_or_buy="make" if is_make else "buy",
                gross_required_qty=_round(gross), wastage_qty=_round(sum(d.wastage for d in demands)),
                on_hand_qty=_round(totals["on_hand"]), reserved_qty=_round(totals["reserved"]),
                quality_hold_qty=_round(totals["hold"]), safety_stock_qty=_round(totals["safety"]),
                available_now_qty=_round(totals["available"]), on_order_by_need_qty=_round(on_order_by_need),
                on_order_late_qty=_round(on_order_late), open_pos_used=sorted(used),
                shortfall_qty=_round(shortfall if not is_make else 0.0), uncovered_qty=_round(uncovered),
                earliest_need_by=earliest_need,
                covered_by_date=None if uncovered > EPS else max((d.ready for d in demands if d.ready), default=self.start),
            ))

        statuses = []
        for wo in self.inp.work_orders:
            dates = wo_ready.get(wo.wo_id, [])
            if wo.wo_id in no_bom or any(d is None for d in dates):
                ready = None
            else:
                ready = max((d for d in dates if d), default=self.start)
            statuses.append(WoMaterialStatus(
                wo_id=wo.wo_id, need_by=wo.need_by or self.start, material_ready_date=ready,
                short_items=list(dict.fromkeys(wo_short.get(wo.wo_id, []))),
            ))
        return MrpResult(
            material_requirements=reqs, shortages=shortages, suggested_pos=pos, planned_production=planned,
            work_orders=statuses, details=details, issues=self.issues,
        )

    def suggest_po(self, item: str, qty: float, need_by: date) -> SuggestedPO:
        name, uom = self.names[item], self.uom[item]
        options = [s for s in self.inp.suppliers if norm_code(s.item_code) == item]
        if not options:
            order = _ceil(qty) if uom in DISCRETE_UOMS else _round(qty)
            return SuggestedPO(
                item_code=name, qty=order, uom=uom, supplier_id=None, need_by=need_by, expedite=False,
                reason=f"Covers {_round(qty)} {uom} that no stock or open PO covers. No supplier data given.",
            )

        def order_qty(s) -> float:
            q = max(qty, s.moq)
            if s.pack_size:
                q = math.ceil(round(q / s.pack_size, 6)) * s.pack_size
            elif uom in DISCRETE_UOMS:
                q = _ceil(q)
            return _round(q)

        def key(s):
            return (s.lead_time_days, (s.unit_price or 0) * order_qty(s), s.supplier_id)

        best = min(options, key=key)
        q = order_qty(best)
        arrival = self.start + timedelta(days=best.lead_time_days)
        expedite = arrival > need_by
        reason = f"Covers {_round(qty)} {uom} that no stock or open PO covers"
        if q > _round(qty) + EPS:
            reason += f" (rounded up to {q} for MOQ {best.moq:g}" + (f", pack {best.pack_size:g}" if best.pack_size else "") + ")"
        reason += f"; {best.supplier_id} lead time {best.lead_time_days} days, arrives {arrival}"
        reason += f", {(arrival - need_by).days} days after the need date {need_by}." if expedite else f", before the need date {need_by}."
        return SuggestedPO(item_code=name, qty=q, uom=uom, supplier_id=best.supplier_id,
                           need_by=need_by, expedite=expedite, reason=reason)


def _natural_key(s: str) -> list[Any]:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def run_mrp(inp: MrpInput | dict[str, Any]) -> MrpResult:
    """Run MRP on validated input (a dict is validated first)."""
    if not isinstance(inp, MrpInput):
        inp = MrpInput.model_validate(inp)
    return _Mrp(inp).run()


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------

MRP_TOOL_DESCRIPTION = (
    "Material requirements planning. Give it the work orders (item, quantity, the date materials are "
    "needed), the BOM lines, stock records, open purchase orders and, if known, supplier options, unit "
    "conversions and safety stock, exactly as read from the documents. It explodes the BOM (with "
    "wastage), nets each component against usable stock (on hand minus reserved, quality hold and safety "
    "stock) and open POs in date order, and returns: material_requirements, shortages and suggested_pos "
    "in the WorkOrderPlan format; planned_production for made sub-assemblies; per work order the date "
    "all its materials are ready (use it as the earliest start when scheduling); per-item details; and "
    "issues (anything it could not resolve or had to assume). Re-run it after changing need dates."
)


def make_mrp_tool() -> StructuredTool:
    def mrp_tool(**kwargs: Any) -> str:
        return run_mrp(MrpInput.model_validate(kwargs)).model_dump_json()

    return StructuredTool.from_function(
        func=mrp_tool,
        name="mrp_tool",
        description=MRP_TOOL_DESCRIPTION,
        args_schema=MrpInput,
        handle_tool_error=True,
        handle_validation_error=validation_error_message,
    )
