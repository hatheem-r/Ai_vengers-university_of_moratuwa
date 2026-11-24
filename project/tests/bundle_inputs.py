"""Test helper: build tool inputs for a bundle the way a planner would, from the
extraction key (the correct Intake output) plus the work-order list and need-by dates
from data/generator/solver.py. Tests only: the tools never read hidden bundle files."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from core.schemas import (BomLine, CapacityLine, DowntimeWindow, FactoryPolicyFields, MrpDemand, MrpToolInput,
                          OpenSupply, PlanningCalendar, ScheduleJob, ScheduleOperation, ScheduleToolInput,
                          StockPosition, SupplierOption, WipLoad)

BUNDLES = Path(__file__).resolve().parents[1] / "data" / "bundles"
HOLIDAYS = [date(2026, 11, 24), date(2026, 12, 23), date(2026, 12, 25)]
CAL = PlanningCalendar(holidays=HOLIDAYS)


def load(bundle_id: str):
    from data.generator.masters import MASTERS
    from data.generator.solver import solve
    root = BUNDLES / bundle_id
    state = json.loads((root / "state.json").read_text())
    key = json.loads((root / "extraction_key.json").read_text())
    answer = json.loads((root / "answer_key.json").read_text())
    master = MASTERS[state["profile"]]
    return state, key, answer, master, solve(state, master)


def _of(key: dict, t: str) -> list[dict]:
    return [d for d in key["documents"] if d["doc_type"] == t]


def mrp_input(key: dict, master: dict, plan: dict, planning_start: date) -> MrpToolInput:
    bom = [BomLine.model_validate(ln) for d in _of(key, "bill_of_materials") for ln in d["lines"]]
    stock: dict[str, StockPosition] = {}
    for d in _of(key, "stock_report"):
        for ln in d["lines"]:
            on_hand = next((ln[k] for k in ("on_hand", "in_stock", "qty") if ln.get(k) is not None), 0)
            reserved = next((ln[k] for k in ("reserved", "committed") if ln.get(k) is not None), 0)
            hold = on_hand if (ln.get("status") or "OK").upper() != "OK" else 0
            p = stock.setdefault(ln["item_code"], StockPosition(item_code=ln["item_code"], uom=ln["uom"], on_hand_qty=0))
            p.on_hand_qty += on_hand
            p.reserved_qty += reserved or 0
            p.on_hold_qty += hold
    new_eta = {d["fields"]["po_number"]: d["fields"]["new_eta"] for d in _of(key, "supplier_email")}
    supply = []
    for d in _of(key, "supplier_purchase_order"):
        f = d["fields"]
        eta = new_eta.get(f["po_number"]) or f.get("expected_arrival_printed")
        if eta:
            supply.append(OpenSupply(po_number=f["po_number"], item_code=f["item_code"], qty=f["qty"], uom=f["uom"],
                                     eta=eta, supplier_id=f.get("supplier_id")))
    suppliers = []
    for code, it in master["items"].items():
        for sid in (it.get("supplier"), it.get("alt")):
            if sid:
                suppliers.append(SupplierOption(supplier_id=sid, item_code=code,
                                                lead_time_working_days=master["suppliers"][sid]["lead_days"]))
    demands = [MrpDemand(wo_id=w["wo_id"], item_code=w["item_code"], qty=w["qty"], uom=w["uom"],
                         need_by=plan["need_by"][w["wo_id"]]) for w in plan["work_orders"]]
    return MrpToolInput(planning_start=planning_start, demands=demands, bom=bom, stock=list(stock.values()),
                        open_supply=supply, suppliers=suppliers, calendar=CAL)


def _routing(master: dict, item: str, key: dict) -> list[dict]:
    rows = [ln for d in _of(key, "routing") for ln in d["lines"]]
    style = master["items"][item].get("style")
    return [r for r in rows if (r.get("item") == item) or (style and r.get("style") == style)]


def schedule_input(key: dict, master: dict, plan: dict, planning_start: date, ready: dict | None = None,
                   **kw) -> ScheduleToolInput:
    ready = ready if ready is not None else plan["ready"]
    jobs = []
    for w in plan["work_orders"]:
        ops = [ScheduleOperation(seq=r["seq"], operation=r["operation"], work_centre=r["work_centre"],
                                 std_min_per_unit=r["std_min_per_unit"], setup_min=r.get("setup_min", 0))
               for r in _routing(master, w["item_code"], key)]
        jobs.append(ScheduleJob(wo_id=w["wo_id"], item_code=w["item_code"], qty=w["qty"], due_date=w["due_date"],
                                priority=w["priority"], earliest_start=ready.get(w["wo_id"]), operations=ops))
    wcs = [CapacityLine.model_validate(ln) for d in _of(key, "capacity_shift_plan") for ln in d["lines"]]
    downtime = []
    for d in _of(key, "maintenance_log"):
        for ln in d["lines"]:
            if ln["status"] != "Open":
                continue
            end = ln.get("unavailable_until_incl") or (
                date.fromisoformat(ln["expected_return"]) - timedelta(days=1) if ln.get("expected_return") else ln["start"])
            downtime.append(DowntimeWindow(work_centre=ln["work_centre"], start=ln["start"], end_incl=end))
    wip = [WipLoad(work_centre=ln["work_centre"], load_minutes=ln["load_minutes"], order=ln.get("order"))
           for d in _of(key, "production_wip_report") for ln in d["lines"]]
    return ScheduleToolInput(planning_start=planning_start, jobs=jobs, work_centres=wcs, downtime=downtime, wip=wip,
                             calendar=CAL, **kw)


def policy(key: dict) -> FactoryPolicyFields:
    docs = _of(key, "factory_policy")
    return FactoryPolicyFields.model_validate(docs[0]["fields"]) if docs else FactoryPolicyFields()
