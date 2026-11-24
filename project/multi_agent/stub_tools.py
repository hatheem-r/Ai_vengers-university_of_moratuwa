"""STUB tools, used until Shazan's project/tools/ (mrp.py, schedule.py, rules.py) land.

They take and return the real tool models from core/schemas.py, so swapping them for the
real tools changes nothing else. They are deliberately simple and deterministic:

- stub_mrp_tool:      one-pass BoM explosion with wastage, netting against usable stock
                      (on hand - reserved - on hold) and open POs that arrive in time.
- stub_schedule_tool: forward, finite-capacity schedule in priority / due-date order:
                      one shift a day from calendar.shift_start, no overtime, skips rest
                      days, holidays and downtime. Not optimal; CP-SAT replaces it.
- stub_rule_check:    a few structural checks (unknown work centre, operation order,
                      rest days, downtime, start before material is ready).
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from core.schemas import (FACTORY_TZ, MaterialRequirement, MrpToolInput, MrpToolOutput, Operation,
                          PlanningCalendar, RuleCheckInput, RuleCheckOutput, RuleViolation,
                          ScheduleConflict, ScheduledWorkOrder, ScheduleToolInput, ScheduleToolOutput,
                          Shortage, SuggestedPO)

COUNTABLE = {"pcs", "pc", "cone", "cones", "carton", "ctn", "set", "pair", "nos", "no", "ea", "each", "roll"}


# --- calendar helpers ------------------------------------------------------------

def is_working_day(d: date, cal: PlanningCalendar) -> bool:
    return d.weekday() in cal.working_weekdays and d not in cal.holidays


def next_working_day(d: date, cal: PlanningCalendar, lag: int = 1) -> date:
    for _ in range(lag):
        d += timedelta(days=1)
        while not is_working_day(d, cal):
            d += timedelta(days=1)
    return d


def working_days_between(a: date, b: date, cal: PlanningCalendar) -> int:
    """Working days after `a` up to and including `b` (0 if b <= a)."""
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        n += is_working_day(d, cal)
    return n


def _round_qty(qty: float, uom: str) -> float:
    return float(math.ceil(qty - 1e-9)) if uom.lower() in COUNTABLE else round(qty, 2)


# --- mrp_tool ----------------------------------------------------------------------

def stub_mrp_tool(inp: MrpToolInput) -> MrpToolOutput:
    bom_by_parent = defaultdict(list)
    for line in inp.bom:
        bom_by_parent[line.parent].append(line)

    req: dict[str, float] = defaultdict(float)
    uom: dict[str, str] = {}
    users: dict[str, list[str]] = defaultdict(list)
    need_by: dict[str, date] = {}
    warnings: list[str] = []

    def explode(item: str, qty: float, wo_id: str, nb: date, depth: int = 0) -> None:
        if depth > 10:
            warnings.append(f"BoM for {item} is deeper than 10 levels; stopped")
            return
        for line in bom_by_parent.get(item, []):
            q = qty * line.qty_per * (1 + line.wastage_pct / 100)
            if line.component in bom_by_parent:  # sub-assembly: explode further
                explode(line.component, q, wo_id, nb, depth + 1)
                continue
            req[line.component] += q
            uom[line.component] = line.uom
            if wo_id not in users[line.component]:
                users[line.component].append(wo_id)
            need_by[line.component] = min(nb, need_by.get(line.component, nb))

    for dem in inp.demands:
        if dem.item_code not in bom_by_parent:
            warnings.append(f"no BoM for {dem.item_code} ({dem.wo_id})")
        explode(dem.item_code, dem.qty, dem.wo_id, dem.need_by)

    stock = defaultdict(lambda: [0.0, 0.0, 0.0])  # on_hand, reserved, on_hold
    for s in inp.stock:
        acc = stock[s.item_code]
        acc[0] += s.on_hand_qty
        acc[1] += s.reserved_qty
        acc[2] += s.on_hold_qty

    reqs, shorts, pos = [], [], []
    material_ready = {d.wo_id: inp.planning_start for d in inp.demands}
    for item in sorted(req):
        u = uom[item]
        required = _round_qty(req[item], u)
        on_hand, reserved, on_hold = stock.get(item, [0.0, 0.0, 0.0])
        available = max(0.0, on_hand - reserved - on_hold)
        nb = need_by[item]
        in_time = late = 0.0
        latest_eta = None
        for sup in inp.open_supply:
            if sup.item_code != item:
                continue
            usable = next_working_day(sup.eta, inp.calendar, inp.receipt_lag_working_days)
            if usable <= nb:
                in_time += sup.qty
            else:
                late += sup.qty
                latest_eta = max(latest_eta or usable, usable)
        net = max(0.0, required - available - in_time)
        net = _round_qty(net, u) if net > 1e-6 else 0.0
        reqs.append(MaterialRequirement(item_code=item, uom=u, required_qty=required, on_hand_qty=round(on_hand, 2),
                                        reserved_qty=round(reserved, 2), on_order_qty=round(in_time, 2),
                                        net_shortage_qty=net, for_work_orders=users[item]))
        if net <= 0:
            continue
        covered_late = late >= net
        shorts.append(Shortage(item_code=item, uom=u, shortfall_qty=net, need_by=nb,
                               affected_work_orders=users[item],
                               cause="supplier_delay" if covered_late else "insufficient_stock"))
        if covered_late and latest_eta:
            for wo in users[item]:
                material_ready[wo] = max(material_ready[wo], latest_eta)
        else:
            supplier = next((o for o in inp.suppliers if o.item_code == item), None)
            pos.append(SuggestedPO(item_code=item, qty=net, uom=u, need_by=nb,
                                   supplier_id=supplier.supplier_id if supplier else None,
                                   expedite=True, reason=f"Net shortage of {net} {u} for {', '.join(users[item])}"))
            if supplier:
                ready = next_working_day(inp.planning_start, inp.calendar,
                                         supplier.lead_time_working_days + inp.receipt_lag_working_days)
                for wo in users[item]:
                    material_ready[wo] = max(material_ready[wo], ready)
            else:
                warnings.append(f"{item}: no supplier lead time; material_ready for {users[item]} is unknown")

    return MrpToolOutput(material_requirements=reqs, shortages=shorts, suggested_pos=pos,
                         material_ready=material_ready, warnings=warnings)


# --- schedule_tool -------------------------------------------------------------------

def stub_schedule_tool(inp: ScheduleToolInput) -> ScheduleToolOutput:
    cal = inp.calendar
    wcs = {w.work_centre: w for w in inp.work_centres}
    down = defaultdict(list)
    for dt in inp.downtime:
        down[dt.work_centre].append((dt.start, dt.end_incl))

    def available(wc: str, d: date) -> bool:
        return is_working_day(d, cal) and wcs[wc].status.upper() != "DOWN" \
            and not any(s <= d <= e for s, e in down[wc])

    def day_bounds(wc: str, d: date) -> tuple[datetime, datetime]:
        start = datetime.combine(d, cal.shift_start, tzinfo=FACTORY_TZ)
        return start, start + timedelta(minutes=wcs[wc].regular_min_per_day)

    def first_slot(wc: str, at: datetime) -> datetime:
        d = at.date()
        for _ in range(400):
            if available(wc, d):
                s, e = day_bounds(wc, d)
                if at < e:
                    return max(at, s)
            d += timedelta(days=1)
            at = datetime.combine(d, time(0), tzinfo=FACTORY_TZ)
        raise RuntimeError(f"no working time on {wc} within 400 days")

    def add_minutes(wc: str, start: datetime, minutes: float) -> datetime:
        cur, left = first_slot(wc, start), minutes
        while True:
            _, e = day_bounds(wc, cur.date())
            room = (e - cur).total_seconds() / 60
            if left <= room + 1e-9:
                return cur + timedelta(minutes=left)
            left -= room
            cur = first_slot(wc, e + timedelta(minutes=1))

    origin = datetime.combine(inp.planning_start, cal.shift_start, tzinfo=FACTORY_TZ)
    free_at = {wc: origin for wc in wcs}
    for w in inp.wip:
        if w.work_centre in wcs:
            c = wcs[w.work_centre]
            free_at[w.work_centre] = add_minutes(w.work_centre, free_at[w.work_centre],
                                                 round(w.load_minutes / (c.units * c.efficiency)))

    scheduled, conflicts = [], []
    order = sorted(inp.jobs, key=lambda j: (j.priority or 99, j.due_date, j.wo_id))
    for job in order:
        unknown = [op.work_centre for op in job.operations if op.work_centre not in wcs]
        if unknown:
            conflicts.append(ScheduleConflict(type="infeasible", wo_id=job.wo_id, work_centre=unknown[0],
                                              message=f"{job.wo_id}: work centre(s) {unknown} not in the capacity plan"))
            continue
        ready = origin
        if job.earliest_start and job.earliest_start > inp.planning_start:
            ready = datetime.combine(job.earliest_start, cal.shift_start, tzinfo=FACTORY_TZ)
            conflicts.append(ScheduleConflict(type="material_late", wo_id=job.wo_id,
                                              message=f"{job.wo_id}: material usable from {job.earliest_start}"))
        ops = []
        for op in sorted(job.operations, key=lambda o: o.seq):
            c = wcs[op.work_centre]
            minutes = round(op.setup_min + job.qty * op.std_min_per_unit / (c.units * c.efficiency))
            start = first_slot(op.work_centre, max(ready, free_at[op.work_centre]))
            end = add_minutes(op.work_centre, start, minutes)
            free_at[op.work_centre] = ready = end
            ops.append(Operation(seq=op.seq, operation=op.operation, machine_id=op.work_centre,
                                 start=start, end=end, qty=job.qty))
        if not ops:
            conflicts.append(ScheduleConflict(type="infeasible", wo_id=job.wo_id, message=f"{job.wo_id}: no operations"))
            continue
        finish = ops[-1].end
        late = working_days_between(job.due_date, finish.date(), cal)
        if late:
            conflicts.append(ScheduleConflict(type="due_date_risk", wo_id=job.wo_id,
                                              message=f"{job.wo_id} finishes {finish.date()}, {late} working day(s) after due {job.due_date}"))
        scheduled.append(ScheduledWorkOrder(wo_id=job.wo_id, planned_start=ops[0].start, planned_finish=finish,
                                            is_late=late > 0, days_late=late, operations=ops))

    status = "feasible" if len(scheduled) == len(inp.jobs) else "infeasible"
    return ScheduleToolOutput(status=status, scheduled=scheduled, conflicts=conflicts, solve_time_s=0.0,
                              infeasibility_reason=None if status == "feasible"
                              else "; ".join(c.message for c in conflicts if c.type == "infeasible"))


# --- rule_check ------------------------------------------------------------------------

def stub_rule_check(inp: RuleCheckInput) -> RuleCheckOutput:
    cal, out = inp.calendar, []
    known = {w.work_centre for w in inp.work_centres}
    down_wc = {w.work_centre for w in inp.work_centres if w.status.upper() == "DOWN"}
    for wo in inp.plan.work_orders:
        prev_end = None
        ready = inp.material_ready.get(wo.wo_id)
        for op in wo.operations:
            if op.machine_id not in known:
                out.append(RuleViolation(rule="unknown_work_centre", severity="high", wo_id=wo.wo_id, seq=op.seq,
                                         work_centre=op.machine_id, message=f"{op.machine_id} is not in the capacity plan"))
            if prev_end and op.start < prev_end:
                out.append(RuleViolation(rule="operation_order", severity="high", wo_id=wo.wo_id, seq=op.seq,
                                         message=f"op {op.seq} starts before the previous operation ends"))
            prev_end = op.end
            for d in {op.start.date(), op.end.date()}:  # pausing over a rest day is fine; working on one is not
                if not is_working_day(d, cal):
                    out.append(RuleViolation(rule="non_working_day", severity="medium", wo_id=wo.wo_id, seq=op.seq,
                                             message=f"op {op.seq} runs on rest day/holiday {d}"))
                    break
            if op.machine_id in down_wc or any(dt.work_centre == op.machine_id and dt.start <= op.end.date()
                                               and op.start.date() <= dt.end_incl for dt in inp.downtime):
                out.append(RuleViolation(rule="downtime", severity="high", wo_id=wo.wo_id, seq=op.seq,
                                         work_centre=op.machine_id, message=f"op {op.seq} overlaps downtime on {op.machine_id}"))
        if ready and wo.operations and wo.operations[0].start.date() < ready:
            out.append(RuleViolation(rule="material_not_ready", severity="high", wo_id=wo.wo_id, seq=wo.operations[0].seq,
                                     message=f"{wo.wo_id} starts before its material is usable ({ready})"))
    return RuleCheckOutput(violations=out)
