"""schedule_tool tests. Shift is 08:00-17:00 (540 min) unless stated; 24 Nov 2026 is a Tuesday."""

import json
import random
from datetime import date, datetime, timedelta

import pytest

from project.core.schemas import ScheduleInput
from project.core.tracing import SL_TZ
from project.tools.schedule import WorkCalendar, check_schedule, make_schedule_tool, run_schedule

START = date(2026, 11, 24)  # Tuesday


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=SL_TZ)


def wo(wo_id, minutes, due="2026-12-31", machine="M1", priority=1, op="Cut", **extra):
    """A one-step work order taking `minutes` (qty = minutes at 60 units/hour)."""
    return {"wo_id": wo_id, "item_code": "X", "qty": minutes, "due_date": due, "priority": priority,
            "routing": [{"seq": 10, "operation": op, "machine_ids": [machine] if machine else []}], **extra}


def problem(work_orders, machines=None, **extra):
    return {"planning_start": START.isoformat(), "work_orders": work_orders,
            "machines": machines or [{"machine_id": "M1", "operations": ["Cut"], "rate_per_hour": 60}], **extra}


def ops_of(result, wo_id):
    return next(w for w in result.work_orders if w.wo_id == wo_id).operations


def test_long_operation_pauses_overnight():
    r = run_schedule(problem([wo("W1", 900)]))  # 15 h = Tue 9 h + Wed 6 h
    assert r.status == "optimal" and r.violations == []
    (op,) = ops_of(r, "W1")
    assert (op.start, op.end) == (dt("2026-11-24T08:00"), dt("2026-11-25T14:00"))
    assert op.start.utcoffset() == timedelta(hours=5, minutes=30)


def test_weekends_and_holidays_are_skipped():
    # 3 working days of work from Thu 26 Nov; Fri 27 is a holiday -> Thu, Mon 30, Tue 1 Dec
    r = run_schedule(problem([wo("W1", 3 * 540, earliest_start="2026-11-26")],
                             calendar={"holidays": ["2026-11-27"]}))
    (op,) = ops_of(r, "W1")
    assert (op.start, op.end) == (dt("2026-11-26T08:00"), dt("2026-12-01T17:00"))


def test_no_double_booking_and_priority_wins():
    # Both need 2 days on M1 by Wed 25 Nov; only one fits -> priority 1 first, priority 2 late
    r = run_schedule(problem([wo("LOW", 1080, due="2026-11-25", priority=2), wo("HIGH", 1080, due="2026-11-25", priority=1)]))
    high, low = ops_of(r, "HIGH")[0], ops_of(r, "LOW")[0]
    assert high.end == dt("2026-11-25T17:00") and low.start == dt("2026-11-26T08:00")
    late = {w.wo_id: (w.is_late, w.days_late) for w in r.work_orders}
    assert late == {"HIGH": (False, 0), "LOW": (True, 2)}  # finishes Fri 27 Nov: Thu + Fri late
    (c,) = r.conflicts
    assert c.type == "capacity_overload" and c.wo_id == "LOW"
    assert r.violations == []


def test_downtime_during_shift_is_never_overlapped():
    # Maintenance Tue 12:00-14:00; a 5 h job cannot start at 08:00 (would end 13:00)
    r = run_schedule(problem([wo("W1", 300)],
                             downtime=[{"machine_id": "M1", "start": "2026-11-24T12:00", "end": "2026-11-24T14:00"}]))
    (op,) = ops_of(r, "W1")
    # Earliest legal: start 14:00 Tue, 3 h Tue + 2 h Wed -> Wed 10:00 (finishing earlier is impossible)
    assert (op.start, op.end) == (dt("2026-11-24T14:00"), dt("2026-11-25T10:00"))
    assert r.violations == []


def test_overnight_downtime_is_not_spanned():
    # Downtime Tue 20:00 - Wed 06:00 (outside shift). A 10 h job must not span it.
    r = run_schedule(problem([wo("W1", 600)],
                             downtime=[{"machine_id": "M1", "start": "2026-11-24T20:00", "end": "2026-11-25T06:00"}]))
    (op,) = ops_of(r, "W1")
    assert (op.start, op.end) == (dt("2026-11-25T08:00"), dt("2026-11-26T09:00"))


def test_alternative_machines_run_in_parallel():
    machines = [{"machine_id": "LINE-1", "operations": ["Sewing"], "rate_per_hour": 60},
                {"machine_id": "LINE-2", "operations": ["sewing"], "rate_per_hour": 30}]
    r = run_schedule(problem([wo("A", 540, machine=None, op="Sewing"), wo("B", 540, machine=None, op="Sewing")], machines))
    used = sorted(ops_of(r, w)[0].machine_id for w in ("A", "B"))
    assert used == ["LINE-1", "LINE-2"] and r.violations == []
    load = {m.machine_id: m.busy_hours for m in r.machine_load}
    assert load == {"LINE-1": 9.0, "LINE-2": 18.0}  # same qty, half the rate


def test_routing_order_setup_and_earliest_start():
    order = {"wo_id": "W1", "item_code": "POLO", "qty": 120, "due_date": "2026-12-31",
             "earliest_start": "2026-11-26T10:30",
             "routing": [{"seq": 20, "operation": "Sewing", "machine_ids": ["SEW"]},
                         {"seq": 10, "operation": "Cutting", "machine_ids": ["CUT"], "hours": 2, "setup_minutes": 30}]}
    machines = [{"machine_id": "CUT", "operations": ["Cutting"]},
                {"machine_id": "SEW", "operations": ["Sewing"], "rates": {"SEWING": 40}, "setup_minutes": 15}]
    r = run_schedule(problem([order], machines))
    cut, sew = ops_of(r, "W1")
    assert (cut.seq, cut.start, cut.end) == (10, dt("2026-11-26T10:30"), dt("2026-11-26T13:00"))  # 2 h + 30 min setup
    assert (sew.seq, sew.start, sew.end) == (20, dt("2026-11-26T13:00"), dt("2026-11-26T16:15"))  # 3 h + 15 min


def test_overtime_only_when_allowed():
    cal = {"overtime_end": "19:00"}
    no_ot = run_schedule(problem([wo("W1", 660)], calendar=cal))
    assert ops_of(no_ot, "W1")[0].end == dt("2026-11-25T10:00") and not ops_of(no_ot, "W1")[0].uses_overtime
    ot = run_schedule(problem([wo("W1", 660)], calendar=cal, allow_overtime=True))
    op = ops_of(ot, "W1")[0]
    assert op.end == dt("2026-11-24T19:00") and op.uses_overtime


def test_late_from_own_lead_time_is_due_date_risk():
    r = run_schedule(problem([wo("W1", 1620, due="2026-11-24")]))  # 3 days of work, due day 1
    w = r.work_orders[0]
    assert (w.is_late, w.days_late) == (True, 2)
    assert r.conflicts[0].type == "due_date_risk" and "27.0 working hours" in r.conflicts[0].message


def test_late_order_waiting_for_downtime_gets_machine_down_conflict():
    r = run_schedule(problem([wo("W1", 540, due="2026-11-24")],
                             downtime=[{"machine_id": "M1", "start": "2026-11-24T08:00", "end": "2026-11-24T17:00",
                                        "reason": "gearbox service"}]))
    types = {c.type for c in r.conflicts}
    assert "machine_down" in types
    assert any("gearbox service" in c.message for c in r.conflicts)


@pytest.mark.parametrize("case,expected", [
    (problem([wo("W1", 60, op="Paint", machine=None)]), "no available machine can do 'Paint'"),
    (problem([wo("W1", 60)], machines=[{"machine_id": "M1", "operations": ["Cut"]}]), "no run rate"),
    (problem([wo("W1", 1200)], downtime=[{"machine_id": "M1", "start": f"2026-11-{d}T12:00", "end": f"2026-11-{d}T12:30"} for d in range(24, 31)],
             horizon_days=7), "longer than any downtime-free working window"),
    (problem([wo("W1", 60)], calendar={"work_days": []}), "no working time"),
    (problem([wo("W1", 60), wo("W1", 60)]), "appears more than once"),
    (problem([wo("W1", 540 * 6)], horizon_days=7), "exceeds all 45 working hours in the horizon"),
    # Each 3-day job fits alone; together they need 6 of the 5 working days -> solver proves infeasible
    (problem([wo("W1", 540 * 3), wo("W2", 540 * 3)], horizon_days=7), "No schedule fits in the 7-day horizon"),
])
def test_infeasible_inputs_explain_why(case, expected):
    r = run_schedule(case)
    assert r.status == "infeasible" and r.work_orders == []
    assert any(expected in reason for reason in r.infeasible_reasons), r.infeasible_reasons


def test_issues_for_bad_downtime_and_unavailable_machine():
    machines = [{"machine_id": "M1", "operations": ["Cut"], "rate_per_hour": 60},
                {"machine_id": "M2", "operations": ["Cut"], "rate_per_hour": 60, "available": False}]
    r = run_schedule(problem([wo("W1", 60, machine=None)], machines,
                             downtime=[{"machine_id": "GHOST", "start": "2026-11-24T08:00", "end": "2026-11-24T09:00"}]))
    assert ops_of(r, "W1")[0].machine_id == "M1"
    assert any("M2 is marked unavailable" in i for i in r.issues)
    assert any("unknown machine GHOST" in i for i in r.issues)


def test_check_schedule_catches_violations():
    inp = ScheduleInput.model_validate(problem(
        [wo("A", 120), wo("B", 120)],
        downtime=[{"machine_id": "M1", "start": "2026-11-26T08:00", "end": "2026-11-26T12:00"}]))
    r = run_schedule(inp)
    assert check_schedule(r, inp) == []
    a, b = ops_of(r, "A")[0], ops_of(r, "B")[0]
    b.start, b.end = a.start, a.end  # double-book
    a.start, a.end = dt("2026-11-26T09:00"), dt("2026-11-26T11:00")  # into downtime
    found = " ".join(check_schedule(r, inp))
    assert "overlaps M1 downtime" in found
    a.start, a.end = dt("2026-11-24T06:00"), dt("2026-11-24T07:00")
    assert any("outside working hours" in v for v in check_schedule(r, inp))
    a.start, a.end = b.start, b.end
    assert any("double-booked" in v for v in check_schedule(r, inp))


@pytest.mark.parametrize("seed", range(30))
def test_random_instances_have_no_violations(seed):
    """Stress test: random factories never produce a double-booking, downtime overlap or bad hours."""
    rng = random.Random(seed)
    ops = ["Cut", "Sew", "Pack"]
    machines = [{"machine_id": f"M{i}", "operations": rng.sample(ops, rng.randint(1, 3)),
                 "rate_per_hour": rng.choice([30, 60, 120]), "setup_minutes": rng.choice([0, 15, 30])}
                for i in range(rng.randint(2, 5))]
    for op in ops:  # every operation has at least one machine
        if not any(op in m["operations"] for m in machines):
            machines[0]["operations"].append(op)
    downtime = []
    for m in machines:
        for _ in range(rng.randint(0, 3)):
            start = datetime.combine(START + timedelta(days=rng.randint(0, 20)), datetime.min.time()) + timedelta(hours=rng.randint(0, 23))
            downtime.append({"machine_id": m["machine_id"], "start": start.isoformat(),
                             "end": (start + timedelta(hours=rng.randint(1, 10))).isoformat()})
    work_orders = []
    for i in range(rng.randint(3, 12)):
        steps = rng.sample(ops, rng.randint(1, 3))
        work_orders.append({
            "wo_id": f"WO-{i}", "item_code": "X", "qty": rng.randint(50, 600), "priority": rng.randint(1, 3),
            "due_date": (START + timedelta(days=rng.randint(2, 25))).isoformat(),
            "earliest_start": (START + timedelta(days=rng.randint(0, 5))).isoformat(),
            "routing": [{"seq": 10 * (k + 1), "operation": s} for k, s in enumerate(steps)],
        })
    inp = ScheduleInput.model_validate({
        "planning_start": START.isoformat(), "work_orders": work_orders, "machines": machines, "downtime": downtime,
        "calendar": {"work_days": [0, 1, 2, 3, 4, 5], "overtime_end": "20:00"},
        "allow_overtime": rng.random() < 0.3, "horizon_days": 120,
    })
    r = run_schedule(inp)
    assert r.status in ("optimal", "feasible"), r.infeasible_reasons
    assert r.violations == []
    assert check_schedule(r, inp) == []
    assert r.solver["wall_time_s"] <= 11
    assert len(r.work_orders) == len(work_orders)


def test_calendar_round_trip():
    inp = ScheduleInput.model_validate(problem([wo("W1", 60)]))
    cal = WorkCalendar(inp)
    assert cal.minutes_before(dt("2026-11-24T08:00")) == 0
    assert cal.minutes_before(dt("2026-11-24T20:00")) == 540  # evening = end of day
    assert cal.start_at(540) == dt("2026-11-25T08:00") and cal.end_at(540) == dt("2026-11-24T17:00")
    assert cal.minutes_before(dt("2026-11-28T10:00")) == 4 * 540  # Saturday -> after Friday


def test_tool_json_and_validation():
    tool = make_schedule_tool()
    out = json.loads(tool.invoke(problem([wo("W1", 60)])))
    assert out["status"] == "optimal" and out["work_orders"][0]["operations"][0]["machine_id"] == "M1"
    msg = tool.invoke(problem([wo("W1", 60)], time_limit_s=60))
    assert "time_limit_s: Input should be less than or equal to 10" in msg
