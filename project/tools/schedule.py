"""schedule_tool: finite-capacity scheduling with OR-Tools CP-SAT.

    from tools.schedule import schedule_tool
    out = schedule_tool(ScheduleToolInput(...))   # -> ScheduleToolOutput (core/schemas.py)

Model
-----
- Time runs on working days only (rest days and holidays from `calendar` are skipped).
  Every working day is a slot of L minutes starting at `calendar.shift_start`, where L is
  the longest day any work centre can work (regular + overtime). Work centre w can work
  minutes [0, c_w) of each day: c_w = regular_min_per_day, plus ot_max_min_per_day when
  the solver turns overtime on for w (overtime is a yes/no decision per work centre for
  the whole horizon, as in the reference solver).
- A work centre is one resource: the `units` parallel machines/operators are folded into
  the duration, duration = setup + qty x std_min_per_unit / (units x efficiency), rounded
  to whole minutes. Two operations never overlap on a work centre (NoOverlap).
- An operation runs without a break except overnight: it may start late on one day and
  continue the next working day. It may not run through downtime: it finishes before the
  machine goes down or starts after it is back.
- An operation can run on its work centre or on one of that centre's `alternates`
  (with the alternate's units and efficiency).
- Operations of a job run in `seq` order; the first starts no earlier than the job's
  `earliest_start` (material ready) and planning_start.
- Downtime: each DowntimeWindow blocks its work centre for whole days. A work centre whose
  capacity-plan status starts with "DOWN" and that has no window is down for the whole
  horizon. WIP load runs first on its work centre (from the first free minute; it speeds
  up if that centre goes on overtime), and new jobs on that centre start after it.
- Objective (`min_lateness`): working days late (summed over jobs) first, then the number
  of work centres on overtime, then the sum of finish times. `min_makespan` puts the last
  finish time first.

Output
------
status: "optimal" / "feasible" (CP-SAT result); "timeout" when CP-SAT found nothing
within the limit, in which case `scheduled` holds the greedy schedule that is also used as
the solver's starting hint (it is feasible, just not optimised); "infeasible" when some
job cannot be scheduled at all (unknown work centre, no operations, every option down),
with `infeasibility_reason` saying which and why; the other jobs are still scheduled.

Never double-books a work centre and never schedules into downtime: both are hard
constraints, and tests/test_tools.py checks every schedule with rule_check.
Days late = working days after the due date up to the finish date.
"""
from __future__ import annotations

import math
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from core.schemas import (FACTORY_TZ, CapacityLine, Operation, ScheduleConflict, ScheduledWorkOrder, ScheduleJob,
                          ScheduleToolInput, ScheduleToolOutput)

from .workcal import first_working_day, working_days_after, working_days_list

MAX_HORIZON_DAYS = 1500
OBJ_LATE = 1_000_000_000      # per working day late
OBJ_OT = 10_000_000           # per work centre on overtime
NUM_WORKERS = 8


@dataclass
class _Option:
    wc: str
    minutes: int


@dataclass
class _Op:
    job: ScheduleJob
    seq: int
    name: str
    options: list[_Option]


@dataclass
class _Placed:
    start: int
    end: int
    wc: str


@dataclass
class _Problem:
    inp: ScheduleToolInput
    L: int
    days: list[date]
    wcs: dict[str, CapacityLine]
    reg: dict[str, int]
    ot_min: dict[str, int]                       # 0 when overtime is not allowed there
    blocked: dict[str, list[tuple[int, int]]]    # fixed busy intervals: downtime
    wip: dict[str, list[tuple[int, int]]]        # wc -> [(start, load minutes)], in load order
    down_days: dict[str, list[tuple[date, date]]]
    jobs: list[ScheduleJob]                      # schedulable jobs, in priority order
    ops: dict[str, list[_Op]]
    release: dict[str, int]
    due_idx: dict[str, int]
    conflicts: list[ScheduleConflict] = field(default_factory=list)


# --- time helpers ---------------------------------------------------------------------------

def _fit(t: int, p: int, c: int, L: int) -> tuple[int, int]:
    """Earliest (start, end) at or after t for p working minutes on a centre that can work
    minutes [0, c) of each L-minute day."""
    d, m = divmod(t, L)
    if m >= c:
        d, m = d + 1, 0
    s = d * L + m
    if p <= c - m:
        return s, s + p
    rest = p - (c - m)
    k = math.ceil(rest / c)
    return s, (d + k) * L + (rest - (k - 1) * c)


def _place(busy: list[tuple[int, int]], t: int, p: int, c: int, L: int) -> tuple[int, int]:
    """Earliest slot at or after t that overlaps nothing in `busy`."""
    for _ in range(100_000):
        s, e = _fit(t, p, c, L)
        clash = [b for b in busy if b[0] < e and s < b[1]]
        if not clash:
            return s, e
        t = max(b[1] for b in clash)
    raise RuntimeError("no free slot found")


def _due_index(due: date, day0: date, cal) -> int:
    """Index of the last working day on or before `due` (negative if before day0)."""
    if due >= day0:
        return working_days_after(day0, due, cal)
    return -working_days_after(due, day0, cal)


# --- setup ---------------------------------------------------------------------------------

def _build(inp: ScheduleToolInput) -> _Problem:
    cal = inp.calendar
    day0 = first_working_day(inp.planning_start, cal)
    wcs = {w.work_centre: w for w in inp.work_centres}
    reg = {k: w.regular_min_per_day for k, w in wcs.items()}
    ot_min = {}
    for k, w in wcs.items():
        allowed = inp.allow_overtime and (inp.ot_work_centres is None or k in inp.ot_work_centres)
        ot_min[k] = w.ot_max_min_per_day if allowed else 0
    L = max([reg[k] + ot_min[k] for k in wcs] + [1])
    conflicts: list[ScheduleConflict] = []

    down_days: dict[str, list[tuple[date, date]]] = {k: [] for k in wcs}
    for dt in inp.downtime:
        if dt.work_centre in wcs:
            down_days[dt.work_centre].append((dt.start, dt.end_incl))
    always_down = {k for k, w in wcs.items()
                   if w.status.strip().upper().startswith("DOWN") and not down_days[k]}
    for k in sorted(always_down):
        conflicts.append(ScheduleConflict(type="machine_down", work_centre=k,
                                          message=f"{k} is DOWN in the capacity plan with no return date; not used"))
    for k in wcs:
        if reg[k] <= 0 and k not in always_down:
            always_down.add(k)

    # Jobs, operations and their work-centre options.
    jobs, ops, bad = [], {}, []
    for job in sorted(inp.jobs, key=lambda j: (j.priority or 10**6, j.due_date, j.wo_id)):
        if not job.operations:
            bad.append((job, None, f"{job.wo_id} has no operations"))
            continue
        job_ops, problem = [], None
        for op in sorted(job.operations, key=lambda o: o.seq):
            if op.work_centre not in wcs:
                problem = (op.work_centre, f"{job.wo_id} op {op.seq} ({op.operation}): work centre "
                                           f"{op.work_centre} is not in the capacity plan")
                break
            cands = [op.work_centre] + [a for a in wcs[op.work_centre].alternates if a in wcs and a != op.work_centre]
            options = []
            for wc in cands:
                if wc in always_down:
                    continue
                w = wcs[wc]
                minutes = op.setup_min + job.qty * op.std_min_per_unit / (w.units * w.efficiency)
                options.append(_Option(wc, max(1, int(round(minutes)))))
            if not options:
                problem = (op.work_centre, f"{job.wo_id} op {op.seq} ({op.operation}): {op.work_centre} "
                                           f"and its alternates are all down for the whole horizon")
                break
            job_ops.append(_Op(job, op.seq, op.operation, options))
        if problem:
            bad.append((job, problem[0], problem[1]))
            continue
        jobs.append(job)
        ops[job.wo_id] = job_ops
    for job, wc, msg in bad:
        conflicts.append(ScheduleConflict(type="infeasible", wo_id=job.wo_id, work_centre=wc, message=msg))

    # Horizon: enough working days for everything run back to back, plus slack.
    total = sum(max(o.minutes for o in op.options) for j in jobs for op in ops[j.wo_id])
    total += sum(w.load_minutes for w in inp.wip)
    min_cap = min([reg[k] for k in wcs if reg[k] > 0] + [480])
    latest = max([(j.earliest_start or day0) for j in jobs] + [j.due_date for j in jobs] + [day0]
                 + [e for v in down_days.values() for _, e in v])
    n_days = working_days_after(day0, latest, cal) + math.ceil(total / min_cap) + 30
    n_days = min(MAX_HORIZON_DAYS, n_days)
    days = working_days_list(day0, n_days, cal)
    index = {d: i for i, d in enumerate(days)}

    blocked: dict[str, list[tuple[int, int]]] = {k: [] for k in wcs}
    for k, windows in down_days.items():
        for a, b in windows:
            d = a
            while d <= b:
                if d in index:
                    i = index[d]
                    blocked[k].append((i * L, (i + 1) * L))
                d += timedelta(days=1)
    wip: dict[str, list[tuple[int, int]]] = {}
    busy = {k: list(v) for k, v in blocked.items()}
    for w in inp.wip:
        if w.work_centre not in wcs or w.work_centre in always_down:
            if w.work_centre not in wcs:
                conflicts.append(ScheduleConflict(type="capacity_overload", work_centre=w.work_centre,
                                                  message=f"WIP {w.order or ''} is on unknown work centre "
                                                          f"{w.work_centre}; ignored"))
            continue
        p = int(round(w.load_minutes))
        if p > 0:
            k = w.work_centre
            s0, e0 = _place(busy[k], busy_end(busy[k], wip.get(k)), p, reg[k], L)
            busy[k].append((s0, e0))
            wip.setdefault(k, []).append((s0, p))

    release = {}
    for j in jobs:
        es = max(j.earliest_start or day0, day0)
        es = first_working_day(es, cal)
        release[j.wo_id] = working_days_after(day0, es, cal) * L
        if j.earliest_start and j.earliest_start > inp.planning_start:
            conflicts.append(ScheduleConflict(type="material_late", wo_id=j.wo_id,
                                              message=f"{j.wo_id} cannot start before {es.isoformat()} (material ready)"))
    due_idx = {j.wo_id: _due_index(j.due_date, day0, cal) for j in jobs}
    return _Problem(inp, L, days, wcs, reg, ot_min, blocked, wip, down_days, jobs, ops, release, due_idx, conflicts)


def busy_end(busy: list[tuple[int, int]], wip: Optional[list[tuple[int, int]]]) -> int:
    """Where the next WIP load may start: after the previous WIP load on that centre."""
    if not wip:
        return 0
    s_last = wip[-1][0]
    return max(e for s, e in busy if s == s_last)


# --- greedy schedule (solver hint and fallback) ---------------------------------------------

def _greedy(pb: _Problem) -> dict[tuple[str, int], _Placed]:
    busy = {k: list(v) for k, v in pb.blocked.items()}
    wip_end = {}
    for k, loads in pb.wip.items():
        t = 0
        for s0, p in loads:
            s, e = _place(busy[k], max(t, s0), p, pb.reg[k], pb.L)
            busy[k].append((s, e))
            t = e
        wip_end[k] = t
    out = {}
    for job in pb.jobs:
        t = pb.release[job.wo_id]
        for op in pb.ops[job.wo_id]:
            best = None
            for o in op.options:
                s, e = _place(busy[o.wc], max(t, wip_end.get(o.wc, 0)), o.minutes, pb.reg[o.wc], pb.L)
                if best is None or e < best.end:
                    best = _Placed(s, e, o.wc)
            busy[best.wc].append((best.start, best.end))
            out[(job.wo_id, op.seq)] = best
            t = best.end
    return out


# --- CP-SAT ----------------------------------------------------------------------------------

def _solve_cp(pb: _Problem, hint: dict[tuple[str, int], _Placed], time_limit: float):
    from ortools.sat.python import cp_model

    L = pb.L
    hint_end = max([p.end for p in hint.values()] + [0])
    due_max = max([max(0, d) for d in pb.due_idx.values()] + [0])
    n_days = min(len(pb.days), max(hint_end // L + 1, due_max + 1) + 10)
    H = n_days * L
    m = cp_model.CpModel()
    ot = {k: m.NewBoolVar(f"ot_{k}") for k in pb.wcs if pb.ot_min[k] > 0}
    intervals: dict[str, list] = {k: [m.NewFixedSizeIntervalVar(a, b - a, f"blk_{k}_{a}") for a, b in v if a < H]
                                  for k, v in pb.blocked.items()}

    def day_of(t, tag: str):
        d = m.NewIntVar(0, n_days, f"d_{tag}")
        m.AddDivisionEquality(d, t, L)
        return d

    def task(tag: str, options: list[_Option]):
        """Start/end vars and one optional interval per work-centre option. The elapsed
        time e - s is the working minutes plus the overnight gaps it spans."""
        s = m.NewIntVar(0, H, f"s_{tag}")
        e = m.NewIntVar(1, H, f"e_{tag}")
        size = m.NewIntVar(1, H, f"z_{tag}")
        m.Add(size == e - s)
        em1 = m.NewIntVar(0, H, f"em1_{tag}")
        m.Add(em1 == e - 1)
        sd, ed = day_of(s, f"s{tag}"), day_of(em1, f"e{tag}")
        pres = []
        for o in options:
            x = m.NewBoolVar(f"x_{tag}_{o.wc}") if len(options) > 1 else m.NewConstant(1)
            pres.append((x, o))
            intervals[o.wc].append(m.NewOptionalIntervalVar(s, size, e, x, f"i_{tag}_{o.wc}"))
            caps = [(pb.reg[o.wc], [ot[o.wc].Not()] if o.wc in ot else [])]
            if o.wc in ot:
                caps.append((pb.reg[o.wc] + pb.ot_min[o.wc], [ot[o.wc]]))
            for c, lits in caps:
                enf = [x] + lits
                m.Add(e - s == o.minutes + (ed - sd) * (L - c)).OnlyEnforceIf(enf)
                m.Add(s - L * sd <= c - 1).OnlyEnforceIf(enf)
                m.Add(e - L * ed <= c).OnlyEnforceIf(enf)
        m.AddExactlyOne([x for x, _ in pres])
        return s, e, ed, pres

    # WIP first on its work centre; new work on that centre starts after it.
    wip_end: dict[str, object] = {}
    for k, loads in pb.wip.items():
        prev = None
        for i, (s0, p) in enumerate(loads):
            s, e, _, _ = task(f"wip_{k}_{i}", [_Option(k, p)])
            m.Add(s >= (s0 if prev is None else prev))
            prev = e
        wip_end[k] = prev

    var, late, finish = {}, {}, {}
    for job in pb.jobs:
        prev_end, ed = None, None
        for op in pb.ops[job.wo_id]:
            key = (job.wo_id, op.seq)
            s, e, ed, pres = task(f"{job.wo_id}_{op.seq}", op.options)
            m.Add(s >= (pb.release[job.wo_id] if prev_end is None else prev_end))
            for x, o in pres:
                if o.wc in wip_end:
                    m.Add(s >= wip_end[o.wc]).OnlyEnforceIf(x)
            prev_end = e
            var[key] = (s, e, pres)
            h = hint.get(key)
            if h is not None:
                m.AddHint(s, h.start)
                m.AddHint(e, h.end)
                for x, o in pres:
                    if len(pres) > 1:
                        m.AddHint(x, o.wc == h.wc)
        lt = m.NewIntVar(0, n_days + abs(min(0, pb.due_idx[job.wo_id])), f"late_{job.wo_id}")
        m.Add(lt >= ed - pb.due_idx[job.wo_id])
        late[job.wo_id] = lt
        finish[job.wo_id] = prev_end
    for ivs in intervals.values():
        if len(ivs) > 1:
            m.AddNoOverlap(ivs)
    for v in ot.values():
        m.AddHint(v, False)

    n_ot = sum(ot.values()) if ot else 0
    if pb.inp.objective == "min_makespan":
        mk = m.NewIntVar(0, H, "makespan")
        m.AddMaxEquality(mk, list(finish.values()))
        m.Minimize(OBJ_LATE * mk + OBJ_OT * n_ot + sum(late.values()))
    else:
        m.Minimize(OBJ_LATE * sum(late.values()) + OBJ_OT * n_ot + sum(finish.values()))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_workers = NUM_WORKERS
    solver.parameters.random_seed = 0
    status = solver.Solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, solver.StatusName(status), set()
    placed = {}
    for key, (s, e, pres) in var.items():
        wc = next(o.wc for x, o in pres if solver.Value(x))
        placed[key] = _Placed(solver.Value(s), solver.Value(e), wc)
    ot_on = {k for k, v in ot.items() if solver.Value(v)}
    return placed, "optimal" if status == cp_model.OPTIMAL else "feasible", ot_on


# --- output ----------------------------------------------------------------------------------

def _clock(pb: _Problem, t: int, end: bool) -> datetime:
    d, mnt = divmod(t - 1 if end else t, pb.L)
    if end:
        mnt += 1
    return datetime.combine(pb.days[d], pb.inp.calendar.shift_start, tzinfo=FACTORY_TZ) + timedelta(minutes=mnt)


def _output(pb: _Problem, placed: dict[tuple[str, int], _Placed], ot_on: set[str]) -> list[ScheduledWorkOrder]:
    cal, out = pb.inp.calendar, []
    for job in pb.jobs:
        ops = []
        for op in pb.ops[job.wo_id]:
            p = placed[(job.wo_id, op.seq)]
            sd, ed = p.start // pb.L, (p.end - 1) // pb.L
            uses_ot = p.wc in ot_on and (ed > sd or p.end - ed * pb.L > pb.reg[p.wc])
            ops.append(Operation(seq=op.seq, operation=op.name, machine_id=p.wc, start=_clock(pb, p.start, False),
                                 end=_clock(pb, p.end, True), qty=job.qty, uses_overtime=uses_ot))
        finish = ops[-1].end
        days_late = working_days_after(job.due_date, finish.date(), cal)
        out.append(ScheduledWorkOrder(wo_id=job.wo_id, planned_start=ops[0].start, planned_finish=finish,
                                      is_late=days_late > 0, days_late=days_late, operations=ops))
    return out


def _report(pb: _Problem, scheduled: list[ScheduledWorkOrder], ot_on: set[str]) -> list[ScheduleConflict]:
    conflicts = list(pb.conflicts)
    used: dict[str, set[str]] = {}
    for w in scheduled:
        for op in w.operations:
            used.setdefault(op.machine_id, set()).add(w.wo_id)
    for k, windows in pb.down_days.items():
        for a, b in windows:
            affected = sorted({j.wo_id for j in pb.jobs for op in pb.ops[j.wo_id]
                               if any(o.wc == k for o in op.options)})
            if affected:
                conflicts.append(ScheduleConflict(
                    type="machine_down", work_centre=k,
                    message=f"{k} is down {a.isoformat()} to {b.isoformat()}; work for {', '.join(affected)} "
                            f"was scheduled around it"))
    for k in sorted(ot_on):
        conflicts.append(ScheduleConflict(
            type="capacity_overload", work_centre=k,
            message=f"{k} needs overtime (up to {pb.ot_min[k]} min/day) to reduce lateness; needs approval"))
    for w in scheduled:
        if w.is_late:
            job = next(j for j in pb.jobs if j.wo_id == w.wo_id)
            conflicts.append(ScheduleConflict(
                type="due_date_risk", wo_id=w.wo_id, work_centre=w.operations[-1].machine_id,
                message=f"{w.wo_id} finishes {w.planned_finish.date().isoformat()}, {w.days_late} working day(s) "
                        f"after its due date {job.due_date.isoformat()}"))
    return conflicts


def schedule_tool(inp: ScheduleToolInput) -> ScheduleToolOutput:
    t0 = _time.monotonic()
    try:
        pb = _build(inp)
        hint = _greedy(pb) if pb.jobs else {}
        placed, status, ot_on = (_solve_cp(pb, hint, inp.time_limit_s) if pb.jobs else ({}, "optimal", set()))
        if placed is None:
            placed, status, ot_on = hint, "timeout", set()
        scheduled = _output(pb, placed, ot_on)
        conflicts = _report(pb, scheduled, ot_on)
    except Exception as exc:  # noqa: BLE001 - the Planner gets a readable error, not a stack trace
        return ScheduleToolOutput(status="error", infeasibility_reason=f"{type(exc).__name__}: {exc}",
                                  solve_time_s=round(_time.monotonic() - t0, 3))
    infeasible = [c.message for c in conflicts if c.type == "infeasible"]
    if infeasible:
        status = "infeasible"
    return ScheduleToolOutput(status=status, scheduled=scheduled, conflicts=conflicts,
                              infeasibility_reason="; ".join(infeasible) or None,
                              solve_time_s=round(_time.monotonic() - t0, 3))
