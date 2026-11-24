"""schedule_tool: production scheduling with OR-Tools CP-SAT.

Model:

* **Working-time axis.** The calendar (work days, shift hours, holidays, and
  overtime hours when ``allow_overtime``) is turned into one axis of working
  minutes. Nights, weekends and holidays are not on it, so a long operation
  can pause overnight (e.g. sewing from Monday 08:00 to next Tuesday 17:00).
* **Machines.** Each operation runs on exactly one eligible machine (the solver
  chooses when several can do it). A machine runs one operation at a time.
* **Downtime.** An operation never overlaps a downtime window on its machine,
  not even one that falls entirely outside working hours: its start and end
  must both be on the same side of the window. So a check that looks only at
  start/end times never sees work during downtime.
* **Order.** Operations of a work order run in ``seq`` order, each starting
  after the previous one ends; the first starts no earlier than
  ``earliest_start`` (pass the material-ready date from mrp_tool).
* **Objective.** Minimise priority-weighted lateness against due dates
  (priority 1 weighs most), then the sum of finish times.
* **Limit.** CP-SAT stops after ``time_limit_s`` (max 10 s). A plan found but
  not proven best is returned with ``status="feasible"``.

Before returning, every schedule is re-checked in calendar time by
:func:`check_schedule` (double-booking, downtime, working hours, order, earliest
start). Any finding goes to ``violations``; it is empty unless there is a bug.
"""

from __future__ import annotations

import bisect
import math
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from langchain_core.tools import StructuredTool, ToolException

from project.core.schemas import (
    DowntimeWindow,
    MachineLoad,
    MachineSpec,
    Operation,
    RoutingStep,
    ScheduleConflict,
    ScheduledWorkOrder,
    ScheduleInput,
    ScheduleResult,
    ScheduleWorkOrder,
)
from project.core.tracing import SL_TZ
from project.tools.common import validation_error_message

NUM_WORKERS = 8
RANDOM_SEED = 0


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=SL_TZ) if dt.tzinfo is None else dt.astimezone(SL_TZ)


def _key(s: str) -> str:
    return s.strip().casefold()


# ---------------------------------------------------------------------------
# Calendar <-> working minutes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Segment:
    start: datetime
    regular_end: datetime  # end of the normal shift
    end: datetime  # end including overtime (== regular_end without overtime)

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)

    @property
    def regular_minutes(self) -> int:
        return int((self.regular_end - self.start).total_seconds() // 60)


class WorkCalendar:
    """Maps calendar datetimes to minutes of working time since the planning start, and back."""

    def __init__(self, inp: ScheduleInput):
        cal = inp.calendar
        holidays = set(cal.holidays)
        use_ot = inp.allow_overtime and cal.overtime_end is not None and cal.overtime_end > cal.shift_end
        self.segments: list[_Segment] = []
        for i in range(inp.horizon_days):
            day = inp.planning_start + timedelta(days=i)
            if day.weekday() not in cal.work_days or day in holidays:
                continue
            start = datetime.combine(day, cal.shift_start, SL_TZ)
            regular_end = datetime.combine(day, cal.shift_end, SL_TZ)
            end = datetime.combine(day, cal.overtime_end, SL_TZ) if use_ot else regular_end
            if end > start:
                self.segments.append(_Segment(start, min(regular_end, end), end))
        self.offsets: list[int] = []
        total = 0
        for seg in self.segments:
            self.offsets.append(total)
            total += seg.minutes
        self.total = total
        self._starts = [s.start for s in self.segments]
        self.work_days = set(cal.work_days)
        self.holidays = holidays

    def minutes_before(self, dt: datetime) -> float:
        """Working minutes between the planning start and ``dt``."""
        dt = _aware(dt)
        i = bisect.bisect_right(self._starts, dt) - 1
        if i < 0:
            return 0.0
        seg = self.segments[i]
        if dt >= seg.end:
            return float(self.offsets[i] + seg.minutes)
        return self.offsets[i] + (dt - seg.start).total_seconds() / 60

    def start_at(self, t: int) -> datetime:
        """Calendar time to START work at working minute ``t`` (a boundary means the next shift)."""
        i = bisect.bisect_right(self.offsets, t) - 1
        i = max(0, min(i, len(self.segments) - 1))
        seg = self.segments[i]
        return seg.start + timedelta(minutes=min(t - self.offsets[i], seg.minutes))

    def end_at(self, t: int) -> datetime:
        """Calendar time work ENDS at working minute ``t`` (a boundary means the end of the earlier shift)."""
        i = bisect.bisect_left(self.offsets, t) - 1
        i = max(0, min(i, len(self.segments) - 1))
        seg = self.segments[i]
        return seg.start + timedelta(minutes=max(0, min(t - self.offsets[i], seg.minutes)))

    def overtime_minutes(self, a: int, b: int) -> int:
        """Working minutes in [a, b) that fall in overtime hours."""
        total = 0
        for off, seg in zip(self.offsets, self.segments):
            if off >= b:
                break
            ot_a, ot_b = off + seg.regular_minutes, off + seg.minutes
            total += max(0, min(b, ot_b) - max(a, ot_a))
        return total

    def is_working_day(self, d: date) -> bool:
        return d.weekday() in self.work_days and d not in self.holidays

    def working_days_between(self, after: date, until: date) -> int:
        """Working days d with after < d <= until."""
        n, d = 0, after + timedelta(days=1)
        while d <= until:
            n += self.is_working_day(d)
            d += timedelta(days=1)
        return n

    def within_hours(self, dt: datetime, *, as_end: bool) -> bool:
        dt = _aware(dt)
        for seg in self.segments:
            if (seg.start < dt <= seg.end) if as_end else (seg.start <= dt < seg.end):
                return True
        return False


# ---------------------------------------------------------------------------
# Problem preparation
# ---------------------------------------------------------------------------


@dataclass
class _Option:
    machine: str
    duration: int  # working minutes


@dataclass
class _Task:
    wo: ScheduleWorkOrder
    step: RoutingStep
    options: list[_Option]


class _Problem:
    def __init__(self, inp: ScheduleInput):
        self.inp = inp
        self.cal = WorkCalendar(inp)
        self.issues: list[str] = []
        self.reasons: list[str] = []
        self.machines: dict[str, MachineSpec] = {}
        for m in inp.machines:
            if not m.available:
                self.issues.append(f"Machine {m.machine_id} is marked unavailable; nothing is scheduled on it.")
                continue
            self.machines[_key(m.machine_id)] = m
        self.downtime: dict[str, list[tuple[int, int, DowntimeWindow]]] = {k: [] for k in self.machines}
        known = {_key(m.machine_id) for m in inp.machines}
        for w in inp.downtime:
            k = _key(w.machine_id)
            if _aware(w.end) <= _aware(w.start):
                self.issues.append(f"Downtime on {w.machine_id} ending {w.end} does not end after it starts; ignored.")
            elif k not in known:
                self.issues.append(f"Downtime for unknown machine {w.machine_id}; ignored.")
            elif k in self.downtime:
                a = math.floor(self.cal.minutes_before(w.start))
                b = math.ceil(self.cal.minutes_before(w.end))
                self.downtime[k].append((a, b, w))
        self.max_free = {k: self._max_free(k) for k in self.machines}
        self.tasks: dict[str, list[_Task]] = {}
        self.earliest: dict[str, int] = {}
        self.deadline: dict[str, int] = {}
        if self.cal.total == 0:
            self.reasons.append("The calendar has no working time in the horizon (check work_days, shift hours, holidays).")
            return
        seen: set[str] = set()
        for wo in inp.work_orders:
            if wo.wo_id in seen:
                self.reasons.append(f"Work order id {wo.wo_id} appears more than once.")
                continue
            seen.add(wo.wo_id)
            self._prepare(wo)

    def _max_free(self, k: str) -> int:
        """Longest stretch of working time on machine k with no downtime."""
        best, cur = 0, 0
        for a, b, _ in sorted(self.downtime[k], key=lambda d: (d[0], d[1])):
            best = max(best, a - cur)
            cur = max(cur, b)
        return max(best, self.cal.total - cur)

    def _prepare(self, wo: ScheduleWorkOrder) -> None:
        if wo.earliest_start is None:
            est = 0
        elif isinstance(wo.earliest_start, datetime):
            est = math.ceil(self.cal.minutes_before(wo.earliest_start))
        else:
            est = math.ceil(self.cal.minutes_before(datetime.combine(wo.earliest_start, time(0), SL_TZ)))
        if est >= self.cal.total:
            self.reasons.append(f"{wo.wo_id}: earliest start {wo.earliest_start} is after the {self.inp.horizon_days}-day horizon.")
            return
        self.earliest[wo.wo_id] = est
        self.deadline[wo.wo_id] = math.floor(self.cal.minutes_before(datetime.combine(wo.due_date, time(23, 59, 59), SL_TZ)))
        tasks = []
        for step in sorted(wo.routing, key=lambda s: s.seq):
            options, why = self._options(wo, step)
            if not options:
                self.reasons.append(f"{wo.wo_id} seq {step.seq} ({step.operation}): {why}")
            tasks.append(_Task(wo, step, options))
        self.tasks[wo.wo_id] = tasks

    def _options(self, wo: ScheduleWorkOrder, step: RoutingStep) -> tuple[list[_Option], str]:
        if step.machine_ids:
            ids = [_key(m) for m in step.machine_ids]
            missing = [m for m in step.machine_ids if _key(m) not in self.machines]
            if missing:
                self.issues.append(f"{wo.wo_id} seq {step.seq}: machine(s) {', '.join(missing)} unknown or unavailable.")
            eligible = [self.machines[i] for i in ids if i in self.machines]
        else:
            eligible = [m for m in self.machines.values() if _key(step.operation) in {_key(o) for o in m.operations}]
        if not eligible:
            return [], f"no available machine can do '{step.operation}'."
        options, too_long, no_rate = [], [], []
        for m in eligible:
            minutes = self._duration(wo, step, m)
            if minutes is None:
                no_rate.append(m.machine_id)
            elif minutes > self.max_free[_key(m.machine_id)]:
                too_long.append(f"{m.machine_id} ({minutes / 60:.1f} h)")
                if self.max_free[_key(m.machine_id)] >= self.cal.total:
                    too_long[-1] += f" which exceeds all {self.cal.total / 60:.0f} working hours in the horizon"
            else:
                options.append(_Option(_key(m.machine_id), minutes))
        if options:
            return options, ""
        parts = []
        if no_rate:
            parts.append(f"no run rate or hours for {', '.join(no_rate)} (give hours or rate_per_hour)")
        if too_long:
            parts.append(
                f"longer than any downtime-free working window on {', '.join(too_long)}; "
                "split the order, increase horizon_days or allow overtime"
            )
        return [], "; ".join(parts) + "."

    @staticmethod
    def _duration(wo: ScheduleWorkOrder, step: RoutingStep, m: MachineSpec) -> int | None:
        if step.hours is not None:
            run = step.hours * 60
        else:
            rates = {_key(k): v for k, v in m.rates.items()}
            rate = step.rate_per_hour or rates.get(_key(step.operation)) or m.rate_per_hour
            if not rate:
                return None
            run = wo.qty / rate * 60
        setup = step.setup_minutes if step.setup_minutes is not None else m.setup_minutes
        return max(1, math.ceil(run + setup - 1e-9))


# ---------------------------------------------------------------------------
# Solve
# ---------------------------------------------------------------------------


def run_schedule(inp: ScheduleInput | dict[str, Any]) -> ScheduleResult:
    """Build and solve the schedule (a dict is validated first)."""
    from ortools.sat.python import cp_model

    if not isinstance(inp, ScheduleInput):
        inp = ScheduleInput.model_validate(inp)
    prob = _Problem(inp)
    if prob.reasons:
        return _empty(prob, "infeasible", {"status": "NOT_SOLVED", "reason": "input check failed"})

    H = prob.cal.total
    model = cp_model.CpModel()
    starts: dict[tuple[str, int], Any] = {}
    ends: dict[tuple[str, int], Any] = {}
    chosen: dict[tuple[str, int], list[tuple[_Option, Any]]] = {}
    intervals: dict[str, list[Any]] = {k: [] for k in prob.machines}
    finish: dict[str, Any] = {}
    tard_terms, finish_terms = [], []

    for wo_id, tasks in prob.tasks.items():
        prev_end = None
        for k, task in enumerate(tasks):
            s = model.new_int_var(prob.earliest[wo_id] if k == 0 else 0, H, f"s_{wo_id}_{k}")
            e = model.new_int_var(0, H, f"e_{wo_id}_{k}")
            lits = []
            for opt in task.options:
                p = model.new_constant(1) if len(task.options) == 1 else model.new_bool_var(f"p_{wo_id}_{k}_{opt.machine}")
                iv = model.new_optional_interval_var(s, opt.duration, e, p, f"iv_{wo_id}_{k}_{opt.machine}")
                intervals[opt.machine].append(iv)
                for j, (a, b, _w) in enumerate(prob.downtime[opt.machine]):
                    before = model.new_bool_var(f"dt_{wo_id}_{k}_{opt.machine}_{j}")
                    model.add(e <= a).only_enforce_if([p, before])
                    model.add(s >= b).only_enforce_if([p, ~before])
                lits.append((opt, p))
            model.add_exactly_one([p for _o, p in lits])
            if prev_end is not None:
                model.add(s >= prev_end)
            starts[(wo_id, k)], ends[(wo_id, k)], chosen[(wo_id, k)] = s, e, lits
            prev_end = e
        finish[wo_id] = prev_end
        tard = model.new_int_var(0, H, f"tard_{wo_id}")
        model.add(tard >= prev_end - prob.deadline[wo_id])
        weight = max(1, 100 // tasks[0].wo.priority)
        tard_terms.append(weight * tard)
        finish_terms.append(prev_end)

    for k, ivs in intervals.items():
        if ivs:
            model.add_no_overlap(ivs)
    big = (H + 1) * max(1, len(finish_terms))
    model.minimize(sum(tard_terms) * big + sum(finish_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = inp.time_limit_s
    solver.parameters.num_workers = NUM_WORKERS
    solver.parameters.random_seed = RANDOM_SEED
    t0 = _time.perf_counter()
    status = solver.solve(model)
    info = {"status": solver.status_name(status), "wall_time_s": round(_time.perf_counter() - t0, 3),
            "time_limit_s": inp.time_limit_s}
    if status == cp_model.MODEL_INVALID:
        raise ToolException(f"Internal error: invalid scheduling model ({model.validate()}).")
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        if status == cp_model.INFEASIBLE:
            prob.reasons.append(
                f"No schedule fits in the {inp.horizon_days}-day horizon with this capacity and downtime. "
                "Increase horizon_days, allow overtime, add machines or split orders."
            )
        else:
            prob.reasons.append(f"No feasible schedule found within {inp.time_limit_s:g} s.")
        return _empty(prob, "infeasible", info)
    info["objective"] = solver.objective_value

    # -- read the solution --------------------------------------------------
    result_wos: list[ScheduledWorkOrder] = []
    conflicts: list[ScheduleConflict] = []
    busy: dict[str, int] = {k: 0 for k in prob.machines}
    for wo_id, tasks in prob.tasks.items():
        wo = tasks[0].wo
        ops = []
        placed = []
        for k, task in enumerate(tasks):
            opt = next(o for o, p in chosen[(wo_id, k)] if solver.boolean_value(p))
            a, b = solver.value(starts[(wo_id, k)]), solver.value(ends[(wo_id, k)])
            busy[opt.machine] += b - a
            placed.append((task, opt, a, b))
            ops.append(Operation(
                seq=task.step.seq, operation=task.step.operation, machine_id=prob.machines[opt.machine].machine_id,
                start=prob.cal.start_at(a), end=prob.cal.end_at(b), qty=wo.qty,
                uses_overtime=prob.cal.overtime_minutes(a, b) > 0,
            ))
        planned_finish = ops[-1].end
        is_late = planned_finish.date() > wo.due_date
        days_late = prob.cal.working_days_between(wo.due_date, planned_finish.date()) if is_late else 0
        result_wos.append(ScheduledWorkOrder(
            wo_id=wo_id, item_code=wo.item_code, qty=wo.qty, priority=wo.priority, due_date=wo.due_date,
            planned_start=ops[0].start, planned_finish=planned_finish, is_late=is_late,
            days_late=days_late, operations=ops,
        ))
        if is_late:
            conflicts.extend(_explain_late(prob, wo, placed, days_late))

    loads = []
    for k, m in prob.machines.items():
        down = sum(min(b, H) - max(a, 0) for a, b, _ in prob.downtime[k] if b > a)
        avail = max(H - down, 0)
        loads.append(MachineLoad(
            machine_id=m.machine_id, busy_hours=round(busy[k] / 60, 2), available_hours=round(avail / 60, 2),
            downtime_hours=round(down / 60, 2), utilization_pct=round(100 * busy[k] / avail, 1) if avail else 0.0,
        ))
    result = ScheduleResult(
        status="optimal" if status == cp_model.OPTIMAL else "feasible",
        work_orders=result_wos, conflicts=conflicts, machine_load=loads, infeasible_reasons=[],
        violations=[], issues=prob.issues, solver=info,
    )
    result.violations = check_schedule(result, inp)
    return result


def _empty(prob: _Problem, status: str, info: dict[str, Any]) -> ScheduleResult:
    return ScheduleResult(status=status, work_orders=[], conflicts=[], machine_load=[],
                          infeasible_reasons=prob.reasons, violations=[], issues=prob.issues, solver=info)


def _explain_late(prob: _Problem, wo: ScheduleWorkOrder, placed, days_late: int) -> list[ScheduleConflict]:
    """Why a work order is late: its own lead time, downtime, or competing orders."""
    out = []
    est = prob.earliest[wo.wo_id]
    lower_bound = est + sum(min(o.duration for o in task.options) for task, *_ in placed)
    late = f"{wo.wo_id} finishes {days_late} working day(s) after its due date {wo.due_date}"
    if lower_bound > prob.deadline[wo.wo_id]:
        start_note = f"cannot start before {prob.cal.start_at(est):%Y-%m-%d %H:%M}" if est else "starts at the planning start"
        out.append(ScheduleConflict(
            type="due_date_risk", wo_id=wo.wo_id,
            message=f"{late}: it {start_note} and needs {(lower_bound - est) / 60:.1f} working hours even with every machine free.",
        ))
    else:
        machines = sorted({prob.machines[o.machine].machine_id for _t, o, _a, _b in placed})
        out.append(ScheduleConflict(
            type="capacity_overload", wo_id=wo.wo_id,
            message=f"{late}: {', '.join(machines)} are busy with other orders (priority {wo.priority}).",
        ))
    prev = est
    for task, opt, a, _b in placed:
        for da, db, w in prob.downtime[opt.machine]:
            if prev < db and da < a:  # the operation waited across this window
                out.append(ScheduleConflict(
                    type="machine_down", wo_id=wo.wo_id, machine_id=w.machine_id,
                    message=f"{task.step.operation} for {wo.wo_id} waits for {w.machine_id} {w.reason} "
                            f"({_aware(w.start):%Y-%m-%d %H:%M} to {_aware(w.end):%Y-%m-%d %H:%M}).",
                ))
        prev = _b
    return out


# ---------------------------------------------------------------------------
# Independent check (calendar time, no solver variables)
# ---------------------------------------------------------------------------


def check_schedule(result: ScheduleResult, inp: ScheduleInput) -> list[str]:
    """Hard-constraint violations in a schedule, checked directly on the datetimes."""
    cal = WorkCalendar(inp)
    violations: list[str] = []
    by_machine: dict[str, list[tuple[datetime, datetime, str]]] = {}
    wos = {w.wo_id: w for w in inp.work_orders}
    for swo in result.work_orders:
        wo = wos.get(swo.wo_id)
        prev_end = None
        for op in swo.operations:
            label = f"{swo.wo_id} seq {op.seq}"
            s, e = _aware(op.start), _aware(op.end)
            if e <= s:
                violations.append(f"{label}: ends before it starts.")
            if not cal.within_hours(s, as_end=False) or not cal.within_hours(e, as_end=True):
                violations.append(f"{label}: starts or ends outside working hours.")
            if prev_end is not None and s < prev_end:
                violations.append(f"{label}: starts before the previous operation ends.")
            prev_end = e
            by_machine.setdefault(_key(op.machine_id), []).append((s, e, label))
            for w in inp.downtime:
                if _key(w.machine_id) == _key(op.machine_id) and s < _aware(w.end) and _aware(w.start) < e:
                    violations.append(f"{label}: overlaps {w.machine_id} downtime {w.start} to {w.end}.")
        if wo is not None and wo.earliest_start is not None and swo.operations:
            est = wo.earliest_start if isinstance(wo.earliest_start, datetime) else datetime.combine(wo.earliest_start, time(0), SL_TZ)
            if _aware(swo.operations[0].start) < _aware(est):
                violations.append(f"{swo.wo_id}: starts before its earliest start {wo.earliest_start}.")
    for machine, ops in by_machine.items():
        ops.sort()
        for (s1, e1, l1), (s2, e2, l2) in zip(ops, ops[1:]):
            if s2 < e1:
                violations.append(f"Machine {machine} double-booked: {l1} and {l2}.")
    return violations


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------

SCHEDULE_TOOL_DESCRIPTION = (
    "Production scheduling with a constraint solver (OR-Tools CP-SAT, at most 10 s). Give it the work "
    "orders (quantity, due date, priority, earliest start such as the material-ready date from mrp_tool, "
    "and the routing: operations in sequence with allowed machines and run time or rate), the machines "
    "(what they can do, units per hour, setup minutes), maintenance and other downtime windows, and the "
    "working calendar (work days, shift hours, holidays, overtime end). It returns a schedule that never "
    "double-books a machine, never overlaps downtime and keeps operation order, minimising priority-"
    "weighted lateness; per work order the operations with machine and times (WorkOrderPlan format), "
    "planned start and finish, is_late and days_late; conflicts explaining late orders; machine load; "
    "or, if no schedule is possible, infeasible_reasons. Overtime is used only if allow_overtime is true."
)


def make_schedule_tool() -> StructuredTool:
    def schedule_tool(**kwargs: Any) -> str:
        return run_schedule(ScheduleInput.model_validate(kwargs)).model_dump_json()

    return StructuredTool.from_function(
        func=schedule_tool,
        name="schedule_tool",
        description=SCHEDULE_TOOL_DESCRIPTION,
        args_schema=ScheduleInput,
        handle_tool_error=True,
        handle_validation_error=validation_error_message,
    )
