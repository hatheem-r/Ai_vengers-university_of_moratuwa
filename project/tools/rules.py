"""rule_check: checks a WorkOrderPlan against hard planning rules.

    from tools.rules import rule_check
    out = rule_check(RuleCheckInput(plan=..., policy=..., work_centres=..., ...))
    out.passed, out.violations                     # RuleCheckOutput (core/schemas.py)

Rules (RuleId in core/schemas.py)            severity
--------------------------------------------------------
double_booking       two operations overlap on one work centre             high
downtime             an operation runs on a day its work centre is down     high
material_not_ready   a work order starts before material_ready[wo_id]       high
operation_order      an operation starts before the previous one ends       high
non_working_day      an operation starts or ends on a rest day / holiday    medium
overtime_day_limit   more overtime on a work centre in a day than policy    high
overtime_week_limit  more total or overtime hours in a week than policy     high
unknown_work_centre  machine_id is not in the capacity plan                 high
qty_mismatch         planned qty per item differs from the customer POs     medium
missing_flag         a late order, shortage or downtime has no flag         medium

How time is read
- An operation may pause overnight and over rest days; it only works on working days.
- A work centre is one resource (its units are already in the operation's duration),
  so any overlap on one machine_id is double booking.
- Overtime on a day = work after shift_start + regular_min_per_day + BREAK_ALLOWANCE_MIN
  (a meal break; the reference plans put lunch 12:30-13:30) plus work before shift_start.
  For a day in the middle of a multi-day operation the work is assumed to end at the
  regular end, or at the overtime limit if the operation says uses_overtime.
- Weekly hours = regular hours for every day with work + that week's overtime.
- missing_flag accepts a flag of the right type that names the work order, item or work
  centre (in its message or source values); failing that, enough flags of that type.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from core.schemas import (FACTORY_TZ, Flag, RuleCheckInput, RuleCheckOutput, RuleViolation)

from .workcal import is_working_day

BREAK_ALLOWANCE_MIN = 60
TOLERANCE_MIN = 1
QTY_TOLERANCE = 1e-6


def _local(dt: datetime) -> datetime:
    return dt.astimezone(FACTORY_TZ)


def _days(a: date, b: date):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def _mentions(flag: Flag, *needles: str) -> bool:
    text = " ".join([flag.message] + [str(s.value) for s in flag.sources if s.value is not None]
                    + [s.field for s in flag.sources])
    return any(n and n in text for n in needles)


def rule_check(inp: RuleCheckInput) -> RuleCheckOutput:
    cal, pol, plan = inp.calendar, inp.policy, inp.plan
    wcs = {w.work_centre: w for w in inp.work_centres}
    down: dict[str, list[tuple[date, date]]] = defaultdict(list)
    for dt in inp.downtime:
        down[dt.work_centre].append((dt.start, dt.end_incl))
    always_down = {k for k, w in wcs.items() if w.status.strip().upper().startswith("DOWN") and not down[k]}
    out: list[RuleViolation] = []

    def add(rule, severity, message, **kw):
        out.append(RuleViolation(rule=rule, severity=severity, message=message, **kw))

    ot_day_limit = pol.max_ot_hours_day * 60
    # (wc, date) -> [earliest start, latest end]
    span: dict[tuple[str, date], list[datetime]] = {}
    by_wc: dict[str, list[tuple[datetime, datetime, str, int]]] = defaultdict(list)

    for wo in plan.work_orders:
        prev_end = None
        ready = inp.material_ready.get(wo.wo_id)
        if ready and wo.operations and _local(wo.operations[0].start).date() < ready:
            add("material_not_ready", "high", f"{wo.wo_id} starts {_local(wo.operations[0].start).date()} but its "
                f"material is only usable from {ready}", wo_id=wo.wo_id, seq=wo.operations[0].seq)
        for op in wo.operations:
            s, e = _local(op.start), _local(op.end)
            wc = op.machine_id
            tag = dict(wo_id=wo.wo_id, seq=op.seq, work_centre=wc)
            if wc not in wcs:
                add("unknown_work_centre", "high", f"{wo.wo_id} op {op.seq}: {wc} is not in the capacity plan", **tag)
            if prev_end and s < prev_end:
                add("operation_order", "high", f"{wo.wo_id} op {op.seq} starts {s:%Y-%m-%d %H:%M} before the "
                    f"previous operation ends ({prev_end:%Y-%m-%d %H:%M})", wo_id=wo.wo_id, seq=op.seq)
            prev_end = e
            for d in sorted({s.date(), e.date()}):
                if not is_working_day(d, cal):
                    add("non_working_day", "medium", f"{wo.wo_id} op {op.seq} works on rest day/holiday {d}", **tag)
                    break
            if wc in always_down:
                add("downtime", "high", f"{wo.wo_id} op {op.seq} uses {wc}, which is DOWN in the capacity plan", **tag)
            else:
                hit = [d for a, b in down.get(wc, []) for d in _days(max(a, s.date()), min(b, e.date()))
                       if is_working_day(d, cal)]
                if hit:
                    add("downtime", "high", f"{wo.wo_id} op {op.seq} runs on {wc} on {hit[0]}, when it is down", **tag)
            if e > s:
                by_wc[wc].append((s, e, wo.wo_id, op.seq))

            # Daily spans for the overtime rules.
            reg = wcs[wc].regular_min_per_day if wc in wcs else 480
            for d in _days(s.date(), e.date()):
                if not is_working_day(d, cal):
                    continue
                day_start = datetime.combine(d, cal.shift_start, tzinfo=FACTORY_TZ)
                reg_end = day_start + timedelta(minutes=reg + BREAK_ALLOWANCE_MIN)
                a = s if d == s.date() else day_start
                b = e if d == e.date() else reg_end + timedelta(minutes=ot_day_limit if op.uses_overtime else 0)
                if b <= a:
                    continue
                cur = span.setdefault((wc, d), [a, b])
                cur[0], cur[1] = min(cur[0], a), max(cur[1], b)

    for wc, ops in by_wc.items():
        ops.sort()
        for i, (s1, e1, wo1, seq1) in enumerate(ops):
            for s2, e2, wo2, seq2 in ops[i + 1:]:
                if s2 >= e1:
                    break
                add("double_booking", "high", f"{wc}: {wo1} op {seq1} and {wo2} op {seq2} overlap "
                    f"({s2:%Y-%m-%d %H:%M} to {min(e1, e2):%Y-%m-%d %H:%M})", wo_id=wo2, seq=seq2, work_centre=wc)

    # Overtime per day and per week.
    week_ot: dict[tuple[str, int, int], float] = defaultdict(float)
    week_total: dict[tuple[str, int, int], float] = defaultdict(float)
    for (wc, d), (a, b) in sorted(span.items()):
        reg = wcs[wc].regular_min_per_day if wc in wcs else 480
        day_start = datetime.combine(d, cal.shift_start, tzinfo=FACTORY_TZ)
        reg_end = day_start + timedelta(minutes=reg + BREAK_ALLOWANCE_MIN)
        ot = max(0.0, (b - reg_end).total_seconds() / 60) + max(0.0, (day_start - a).total_seconds() / 60)
        if ot > ot_day_limit + TOLERANCE_MIN:
            add("overtime_day_limit", "high", f"{wc} works {ot / 60:.1f} h overtime on {d}; the policy allows "
                f"{pol.max_ot_hours_day:g} h a day", work_centre=wc)
        wk = (wc, *d.isocalendar()[:2])
        week_ot[wk] += ot
        week_total[wk] += reg + ot
    for (wc, y, w), total in sorted(week_total.items()):
        if total > pol.max_total_hours_week * 60 + TOLERANCE_MIN:
            add("overtime_week_limit", "high", f"{wc} works {total / 60:.1f} h in week {y}-W{w:02d}; the policy "
                f"allows {pol.max_total_hours_week:g} h", work_centre=wc)
        elif week_ot[(wc, y, w)] > pol.max_ot_hours_week * 60 + TOLERANCE_MIN:
            add("overtime_week_limit", "high", f"{wc} works {week_ot[(wc, y, w)] / 60:.1f} h overtime in week "
                f"{y}-W{w:02d}; the policy allows {pol.max_ot_hours_week:g} h", work_centre=wc)

    # Quantities against the customer PO lines.
    if inp.order_lines:
        ordered: dict[str, float] = defaultdict(float)
        for ln in inp.order_lines:
            if ln.item_code:
                ordered[ln.item_code] += ln.qty
        planned: dict[str, float] = defaultdict(float)
        for wo in plan.work_orders:
            planned[wo.item_code] += wo.qty
        for item in sorted(set(ordered) | set(planned)):
            if abs(ordered.get(item, 0) - planned.get(item, 0)) > QTY_TOLERANCE:
                wo_ids = [w.wo_id for w in plan.work_orders if w.item_code == item]
                add("qty_mismatch", "medium", f"{item}: work orders plan {planned.get(item, 0):g} but the customer "
                    f"POs order {ordered.get(item, 0):g}", wo_id=wo_ids[0] if wo_ids else None)

    # Known problems that need a flag.
    def need_flag(types: set[str], needles: list[str], count_needed: int, what: str, **kw) -> None:
        typed = [f for f in plan.flags if f.type in types]
        if any(_mentions(f, *needles) for f in typed) or len(typed) >= count_needed:
            return
        add("missing_flag", "medium", f"{what} has no {'/'.join(sorted(types))} flag", **kw)

    late = [w for w in plan.work_orders if w.is_late]
    for w in late:
        need_flag({"due_date_risk"}, [w.wo_id, w.customer_po], len(late), f"{w.wo_id} is {w.days_late} working day(s) late",
                  wo_id=w.wo_id)
    for s in plan.shortages:
        need_flag({"shortage", "supplier_delay"}, [s.item_code, *s.affected_work_orders], len(plan.shortages),
                  f"Shortage of {s.shortfall_qty:g} {s.uom} {s.item_code}")
    horizon_end = max([_local(op.end).date() for w in plan.work_orders for op in w.operations] + [plan.planning_start])
    down_wcs = sorted({k for k, v in down.items() for a, b in v if b >= plan.planning_start and a <= horizon_end}
                      | always_down)
    for k in down_wcs:
        need_flag({"machine_down"}, [k], len(down_wcs), f"{k} is down", work_centre=k)
    return RuleCheckOutput(violations=out)
