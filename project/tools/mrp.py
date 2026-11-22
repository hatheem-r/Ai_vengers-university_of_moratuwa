"""mrp_tool: material requirements planning for a set of work orders.

    from tools.mrp import mrp_tool
    out = mrp_tool(MrpToolInput(...))        # -> MrpToolOutput (core/schemas.py)

What it does
------------
1. Explodes each demand through the BoM, all levels, with wastage at every level:
   need = parent qty x qty_per x (1 + wastage_pct / 100). A component that is itself a
   parent in the BoM is a sub-assembly and is exploded further.
2. Allocates material to work orders one at a time, in need-by order (ties keep the
   input order, so pass demands in priority order). Each work order takes, per component:
     a. free stock = on hand - on hold - reserved (shared pool, first come first served);
     b. open supplier POs, earliest usable first. A PO received on day D is usable from
        D + receipt_lag_working_days. The part used before need_by counts as on order;
        a part that is only usable after need_by is a `supplier_delay` shortage;
     c. anything still missing is an `insufficient_stock` shortage.
3. One Shortage and one SuggestedPO per short component. The PO goes to the fastest
   supplier in `suppliers` (if any is given). `expedite` is true when that supplier
   cannot make the need-by date.
4. material_ready[wo_id] = the day all of a work order's material is usable: the latest of
   planning_start, the in-time POs it uses, and the date its shortage can be covered (the
   new PO or the delayed PO, whichever is earlier). Feed it to schedule_tool as
   ScheduleJob.earliest_start.

Reported numbers: required_qty is the total over all work orders (rounded up: whole units
for countable UoMs, else 0.01); on_hand_qty is usable on hand (on hand - on hold);
on_order_qty is the PO quantity allocated in time; net_shortage_qty is the shortfall.

Written independently of data/generator/solver.py on purpose: tests/test_tools.py
compares the two on the bundles, so a disagreement points at a bug in one of them.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from core.schemas import (BomLine, MaterialRequirement, MrpToolInput, MrpToolOutput, Shortage, ShortageCause,
                          SuggestedPO)

from .workcal import add_working_days, first_working_day, round_requirement

EPS = 1e-6
MAX_BOM_DEPTH = 12


@dataclass
class _Supply:
    po_number: str
    qty: float
    usable: date
    supplier_id: Optional[str]


@dataclass
class _Short:
    wo_id: str
    qty: float
    need_by: date
    cause: ShortageCause
    po_number: Optional[str] = None
    po_usable: Optional[date] = None


@dataclass
class _Item:
    uom: str
    required: float = 0.0
    on_order: float = 0.0
    wos: list[str] = field(default_factory=list)
    shorts: list[_Short] = field(default_factory=list)


def explode_bom(bom: list[BomLine], item: str, qty: float, warnings: Optional[list[str]] = None) -> dict[str, tuple[float, str]]:
    """{component: (gross qty incl. wastage, uom)} for `qty` of `item`, down to purchased items."""
    by_parent: dict[str, list[BomLine]] = defaultdict(list)
    for line in bom:
        by_parent[line.parent].append(line)
    out: dict[str, tuple[float, str]] = {}
    warnings = warnings if warnings is not None else []

    def walk(parent: str, q: float, path: tuple[str, ...]) -> None:
        if len(path) > MAX_BOM_DEPTH or parent in path[:-1]:
            warnings.append(f"BoM loop or depth > {MAX_BOM_DEPTH} at {' > '.join(path)}; stopped there")
            return
        for line in by_parent[parent]:
            need = q * line.qty_per * (1 + line.wastage_pct / 100)
            if line.component in by_parent:
                walk(line.component, need, path + (line.component,))
            else:
                prev, uom = out.get(line.component, (0.0, line.uom))
                if uom != line.uom:
                    warnings.append(f"{line.component}: BoM lines use both {uom} and {line.uom}; added as {uom}")
                out[line.component] = (prev + need, uom)

    walk(item, qty, (item,))
    return out


def mrp_tool(inp: MrpToolInput) -> MrpToolOutput:
    cal = inp.calendar
    warnings: list[str] = []
    start = inp.planning_start
    parents = {line.parent for line in inp.bom}

    # Stock: one free pool per item.
    free: dict[str, float] = defaultdict(float)
    usable_on_hand: dict[str, float] = defaultdict(float)
    reserved: dict[str, float] = defaultdict(float)
    stock_uom: dict[str, str] = {}
    for s in inp.stock:
        usable_on_hand[s.item_code] += max(0.0, s.on_hand_qty - s.on_hold_qty)
        reserved[s.item_code] += s.reserved_qty
        stock_uom.setdefault(s.item_code, s.uom)
    for code in usable_on_hand:
        free[code] = max(0.0, usable_on_hand[code] - reserved[code])

    # Open supply, earliest usable first.
    supply: dict[str, list[_Supply]] = defaultdict(list)
    for po in inp.open_supply:
        usable = add_working_days(po.eta, inp.receipt_lag_working_days, cal)
        supply[po.item_code].append(_Supply(po.po_number, po.qty, usable, po.supplier_id))
    for rows in supply.values():
        rows.sort(key=lambda r: (r.usable, r.po_number))

    items: dict[str, _Item] = {}
    ready: dict[str, date] = {}
    order = sorted(range(len(inp.demands)), key=lambda i: (inp.demands[i].need_by, i))
    for i in order:
        dem = inp.demands[i]
        ready[dem.wo_id] = max(ready.get(dem.wo_id, start), start)
        if dem.item_code not in parents:
            warnings.append(f"{dem.wo_id}: no BoM for {dem.item_code}; no material planned for it")
            continue
        for code, (qty, uom) in explode_bom(inp.bom, dem.item_code, dem.qty, warnings).items():
            it = items.setdefault(code, _Item(uom=uom))
            it.required += qty
            if dem.wo_id not in it.wos:
                it.wos.append(dem.wo_id)
            if code in stock_uom and stock_uom[code] != uom:
                warnings.append(f"{code}: stock is in {stock_uom[code]} but the BoM uses {uom}; not converted")
            take = min(free[code], qty)
            free[code] -= take
            left = qty - take
            for po in supply.get(code, []):
                if left <= EPS:
                    break
                t = min(po.qty, left)
                if t <= EPS:
                    continue
                po.qty -= t
                left -= t
                if po.usable <= dem.need_by:
                    it.on_order += t
                    ready[dem.wo_id] = max(ready[dem.wo_id], po.usable)
                else:
                    it.shorts.append(_Short(dem.wo_id, t, dem.need_by, "supplier_delay", po.po_number, po.usable))
            if left > EPS:
                it.shorts.append(_Short(dem.wo_id, left, dem.need_by, "insufficient_stock"))

    requirements: list[MaterialRequirement] = []
    shortages: list[Shortage] = []
    suggested: list[SuggestedPO] = []
    for code in sorted(items):
        it = items[code]
        short_total = sum(s.qty for s in it.shorts)
        net = round_requirement(short_total, it.uom) if short_total > EPS else 0.0
        requirements.append(MaterialRequirement(
            item_code=code, uom=it.uom, required_qty=round_requirement(it.required, it.uom),
            on_hand_qty=round(usable_on_hand.get(code, 0.0), 2), reserved_qty=round(reserved.get(code, 0.0), 2),
            on_order_qty=round(it.on_order, 2), net_shortage_qty=net, for_work_orders=sorted(it.wos)))
        if not net:
            continue

        need_by = min(s.need_by for s in it.shorts)
        delayed = [s for s in it.shorts if s.cause == "supplier_delay"]
        cause: ShortageCause = "supplier_delay" if delayed else "insufficient_stock"
        affected = sorted({s.wo_id for s in it.shorts})
        shortages.append(Shortage(item_code=code, uom=it.uom, shortfall_qty=net, need_by=need_by,
                                  affected_work_orders=affected, cause=cause))

        options = [o for o in inp.suppliers if o.item_code == code]
        best = min(options, key=lambda o: (o.lead_time_working_days, o.supplier_id)) if options else None
        new_ready = None
        if best is not None:
            new_ready = add_working_days(add_working_days(first_working_day(start, cal), best.lead_time_working_days, cal),
                                         inp.receipt_lag_working_days, cal)
        for s in it.shorts:
            dates = [d for d in (new_ready, s.po_usable) if d is not None]
            if dates:
                ready[s.wo_id] = max(ready[s.wo_id], min(dates))
            else:
                warnings.append(f"{code}: no supplier lead time, so the date {s.wo_id} can start is unknown")

        if delayed:
            late = max(s.po_usable for s in delayed if s.po_usable)
            reason = (f"Open PO {delayed[0].po_number} is only usable on {late.isoformat()}, after the need-by date "
                      f"{need_by.isoformat()}; order {net:g} {it.uom} elsewhere or accept the delay. Needs approval.")
        else:
            reason = f"Covers the {net:g} {it.uom} shortfall for {', '.join(affected)}."
        if best is not None:
            reason += f" {best.supplier_id} lead time is {best.lead_time_working_days} working days."
        suggested.append(SuggestedPO(item_code=code, qty=net, uom=it.uom,
                                     supplier_id=best.supplier_id if best else None, need_by=need_by,
                                     expedite=new_ready is None or new_ready > need_by, reason=reason))

    return MrpToolOutput(material_requirements=requirements, shortages=shortages, suggested_pos=suggested,
                         material_ready=ready, warnings=list(dict.fromkeys(warnings)))
