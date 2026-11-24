"""Tests for project/tools/: read_document, mrp_tool, schedule_tool, rule_check.

Bundle tests build the tool inputs from extraction_key.json (the correct Intake output)
and compare with data/generator/solver.py, which was written separately on purpose
(TASKS.md section 2): if they disagree, one of them has a bug.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, time as dtime, timedelta

import pytest

from bundle_inputs import BUNDLES, CAL, load, mrp_input, policy, schedule_input
from core.schemas import (FACTORY_TZ, AnswerKey, BomLine, CapacityLine, CustomerPOLine, DowntimeWindow, Flag,
                          MrpDemand, MrpToolInput, OpenSupply, PlanningCalendar, RuleCheckInput, ScheduleJob,
                          ScheduleOperation, ScheduleToolInput, SourceRef, StockPosition, SupplierOption, WipLoad,
                          WorkOrder, WorkOrderPlan)
from tools import schedule as schedule_mod
from tools.loaders import list_documents, read_document, read_input
from tools.mrp import explode_bom, mrp_tool
from tools.rules import rule_check
from tools.schedule import schedule_tool

ALL_BUNDLES = sorted(p.name for p in BUNDLES.iterdir() if (p / "state.json").is_file()) if BUNDLES.is_dir() else []
DEV = [b for b in ALL_BUNDLES if b.startswith("dev_")]
needs_bundles = pytest.mark.skipif(not ALL_BUNDLES, reason="bundles not generated")
STRUCTURAL = {"double_booking", "downtime", "material_not_ready", "operation_order", "non_working_day",
              "overtime_day_limit", "overtime_week_limit", "unknown_work_centre"}
MON = date(2026, 12, 7)  # a Monday, no holidays that week


def at(d: date, h: int, m: int = 0) -> datetime:
    return datetime.combine(d, dtime(h, m), tzinfo=FACTORY_TZ)


# --- read_document ---------------------------------------------------------------------------

@needs_bundles
def test_read_document_xlsx_csv_pdf_and_scan():
    root = BUNDLES / "dev_A_S1_clean" / "inputs"
    cap = read_document(root / "capacity_shift_plan.xlsx")
    assert cap.kind == "table" and "Work centres" in cap.sheets
    assert "CUT-01" in cap.sheets["Work centres"].to_string() and "r1\tWork centre ID" in cap.text
    po = read_document(next(root.glob("customer_po_*.pdf")))
    assert po.kind == "pdf" and "NWO-PO-2408" in po.text and not po.looks_scanned
    assert po.pages[0].image_jpeg is None and len(po.content_blocks()) == 1
    assert read_document(next(root.glob("customer_po_*.pdf")), images="all").pages[0].image_jpeg[:2] == b"\xff\xd8"

    scan = read_document(next((BUNDLES / "A_S4_messy" / "inputs").glob("customer_po_*.pdf")))
    assert scan.looks_scanned and scan.pages[0].image_jpeg
    blocks = scan.content_blocks()
    assert blocks[0]["type"] == "image" and blocks[0]["source"]["media_type"] == "image/jpeg"
    assert blocks[-1]["type"] == "text" and "page images" in blocks[-1]["text"]

    c_inputs = BUNDLES / "dev_C_S5_messy" / "inputs"
    csv = read_document(c_inputs / "maintenance_log.csv")
    assert list(csv.sheets) == ["csv"] and "BLD-01" in csv.text
    assert read_document(next(c_inputs.glob("*.jpg"))).images()[0][0] == "image/jpeg"
    assert "Kandy Print" in read_document(next(c_inputs.glob("supplier_email_*.txt"))).text


@needs_bundles
def test_read_input_stays_inside_inputs():
    inputs = BUNDLES / "dev_A_S1_clean" / "inputs"
    assert read_input(inputs, "routing_odoo_operations.xlsx").kind == "table"
    for bad in ("../answer_key.json", "../state.json", "/etc/passwd", "nope.pdf"):
        with pytest.raises(FileNotFoundError):
            read_input(inputs, bad)
    assert all(p.parent == inputs for p in list_documents(inputs))


# --- mrp_tool: against solver.py ---------------------------------------------------------------

def _mrp_vs_answer(bundle: str):
    state, key, answer, master, plan = load(bundle)
    out = mrp_tool(mrp_input(key, master, plan, date.fromisoformat(state["planning_start"])))
    conflict_items = {c["item"] for c in state.get("conflicts", []) if c["kind"] == "receipt_mismatch"}
    exp_short = sorted((s["item_code"], s["shortfall_qty"], s["cause"], s["need_by"], tuple(s["affected_work_orders"]))
                       for s in answer["shortages"])
    got_short = sorted((s.item_code, s.shortfall_qty, s.cause, s.need_by.isoformat(), tuple(s.affected_work_orders))
                       for s in out.shortages)
    exp_mat = {m["item_code"]: m for m in answer["material_requirements"]}
    got_mat = {m.item_code: m.model_dump(mode="json") for m in out.material_requirements}
    return state, answer, plan, out, exp_short, got_short, exp_mat, got_mat, conflict_items


@needs_bundles
@pytest.mark.parametrize("bundle", ALL_BUNDLES)
def test_mrp_matches_solver(bundle):
    state, answer, plan, out, exp_short, got_short, exp_mat, got_mat, conflict_items = _mrp_vs_answer(bundle)
    assert got_short == exp_short
    assert set(got_mat) == set(exp_mat)
    for code, exp in exp_mat.items():
        got = got_mat[code]
        fields = ["required_qty", "reserved_qty", "on_order_qty", "net_shortage_qty", "for_work_orders", "uom"]
        if code not in conflict_items:  # C_S6: stock report and delivery note disagree on purpose
            fields.append("on_hand_qty")
        assert {f: got[f] for f in fields} == {f: exp[f] for f in fields}, code
    assert {k: v.isoformat() for k, v in out.material_ready.items()} == plan["ready"]
    exp_po = sorted((p["item_code"], p["supplier_id"], p["expedite"]) for p in answer["suggested_pos"])
    assert sorted((p.item_code, p.supplier_id, p.expedite) for p in out.suggested_pos) == exp_po
    for po in out.suggested_pos:  # the solver rounds up to pack size; we order the exact shortfall
        assert po.qty <= next(p["qty"] for p in answer["suggested_pos"] if p["item_code"] == po.item_code)


@needs_bundles
def test_mrp_sample_bundle_has_no_shortage():
    """TASKS.md acceptance: mrp_tool shortages match solver.py on dev_A_S1_clean."""
    *_, exp_short, got_short, _, _, _ = _mrp_vs_answer("dev_A_S1_clean")
    assert got_short == exp_short == []


# --- mrp_tool: unit cases -----------------------------------------------------------------------

def _mrp(**kw) -> MrpToolInput:
    base = dict(planning_start=MON, calendar=PlanningCalendar(),
                bom=[BomLine(parent="SHIRT", component="BODY", qty_per=1, uom="pcs", wastage_pct=0),
                     BomLine(parent="BODY", component="FAB", qty_per=0.5, uom="kg", wastage_pct=10),
                     BomLine(parent="SHIRT", component="BTN", qty_per=6, uom="pcs", wastage_pct=2)],
                demands=[MrpDemand(wo_id="WO-001", item_code="SHIRT", qty=100, uom="pcs", need_by=MON)],
                stock=[])
    base.update(kw)
    return MrpToolInput(**base)


def test_explode_multilevel_with_wastage():
    got = explode_bom(_mrp().bom, "SHIRT", 100)
    assert got["FAB"][0] == pytest.approx(55.0) and got["BTN"][0] == pytest.approx(612.0)
    assert "BODY" not in got


def test_mrp_netting_hold_reserved_and_rounding():
    out = mrp_tool(_mrp(stock=[StockPosition(item_code="FAB", uom="kg", on_hand_qty=60, on_hold_qty=10, reserved_qty=5),
                               StockPosition(item_code="BTN", uom="pcs", on_hand_qty=1000)]))
    fab = next(m for m in out.material_requirements if m.item_code == "FAB")
    assert (fab.required_qty, fab.on_hand_qty, fab.reserved_qty, fab.net_shortage_qty) == (55.0, 50.0, 5.0, 10.0)
    assert [(s.item_code, s.shortfall_qty, s.cause) for s in out.shortages] == [("FAB", 10.0, "insufficient_stock")]
    assert out.suggested_pos[0].supplier_id is None and out.suggested_pos[0].expedite
    assert any("no supplier lead time" in w for w in out.warnings)


def test_mrp_in_time_po_late_po_and_supplier_choice():
    supply = [OpenSupply(po_number="P1", item_code="FAB", qty=20, uom="kg", eta=date(2026, 12, 3)),   # usable Fri 4
              OpenSupply(po_number="P2", item_code="FAB", qty=100, uom="kg", eta=date(2026, 12, 14))]  # usable Tue 15
    suppliers = [SupplierOption(supplier_id="SLOW", item_code="FAB", lead_time_working_days=10),
                 SupplierOption(supplier_id="FAST", item_code="FAB", lead_time_working_days=3)]
    out = mrp_tool(_mrp(stock=[StockPosition(item_code="FAB", uom="kg", on_hand_qty=10),
                               StockPosition(item_code="BTN", uom="pcs", on_hand_qty=1000)],
                        open_supply=supply, suppliers=suppliers))
    fab = next(m for m in out.material_requirements if m.item_code == "FAB")
    assert fab.on_order_qty == 20 and fab.net_shortage_qty == 25.0
    (short,) = out.shortages
    assert short.cause == "supplier_delay" and short.affected_work_orders == ["WO-001"]
    (po,) = out.suggested_pos
    assert po.supplier_id == "FAST" and po.expedite and "P2" in po.reason
    # FAST: 3 working days after Mon 7 Dec + 1 receiving day = Fri 11 Dec (earlier than P2's Tue 15)
    assert out.material_ready["WO-001"] == date(2026, 12, 11)


def test_mrp_allocates_by_need_by_and_warns_on_missing_bom():
    demands = [MrpDemand(wo_id="WO-002", item_code="SHIRT", qty=100, uom="pcs", need_by=date(2026, 12, 10)),
               MrpDemand(wo_id="WO-001", item_code="SHIRT", qty=100, uom="pcs", need_by=MON),
               MrpDemand(wo_id="WO-003", item_code="PANTS", qty=5, uom="pcs", need_by=MON)]
    out = mrp_tool(_mrp(demands=demands, stock=[StockPosition(item_code="FAB", uom="kg", on_hand_qty=55),
                                                 StockPosition(item_code="BTN", uom="pcs", on_hand_qty=5000)]))
    (short,) = out.shortages
    assert short.affected_work_orders == ["WO-002"] and short.need_by == date(2026, 12, 10)
    assert any("PANTS" in w for w in out.warnings)


# --- schedule_tool: bundles ---------------------------------------------------------------------

def _check(inp: ScheduleToolInput, out, ready=None) -> list:
    plan = WorkOrderPlan(
        bundle_id="t", system_id="human", created_at=at(MON, 8), planning_start=inp.planning_start,
        work_orders=[WorkOrder(wo_id=s.wo_id, item_code="X", qty=1, uom="pcs", customer_po="PO", priority=1,
                               due_date=inp.planning_start, planned_start=s.planned_start,
                               planned_finish=s.planned_finish, is_late=s.is_late, days_late=s.days_late,
                               operations=s.operations) for s in out.scheduled],
        material_requirements=[], rationale="test")
    res = rule_check(RuleCheckInput(plan=plan, policy=policy({"documents": []}), work_centres=inp.work_centres,
                                    downtime=inp.downtime, material_ready=ready or {}, calendar=inp.calendar))
    return [v for v in res.violations if v.rule in STRUCTURAL]


@needs_bundles
@pytest.mark.parametrize("bundle", ALL_BUNDLES)
def test_schedule_bundles_feasible_and_no_worse_than_solver(bundle):
    state, key, answer, master, plan = load(bundle)
    inp = schedule_input(key, master, plan, date.fromisoformat(state["planning_start"]))
    t0 = time.monotonic()
    out = schedule_tool(inp)
    assert time.monotonic() - t0 < 12
    assert out.status in ("optimal", "feasible"), out.infeasibility_reason
    assert {s.wo_id for s in out.scheduled} == {j.wo_id for j in inp.jobs}
    assert _check(inp, out, {k: date.fromisoformat(v) for k, v in plan["ready"].items()}) == []
    assert sum(s.days_late for s in out.scheduled) <= sum(w["days_late"] for w in answer["work_orders"])
    late = {s.wo_id for s in out.scheduled if s.is_late}
    assert late == {c.wo_id for c in out.conflicts if c.type == "due_date_risk"}


# --- schedule_tool: unit cases --------------------------------------------------------------------

def _wc(name, **kw) -> CapacityLine:
    return CapacityLine(work_centre=name, units=kw.pop("units", 1), efficiency=kw.pop("efficiency", 1.0), **kw)


def _job(wo, minutes_per_unit=1.0, qty=240, due=date(2026, 12, 31), wcs=("A",), **kw) -> ScheduleJob:
    return ScheduleJob(wo_id=wo, item_code="X", qty=qty, due_date=due, operations=[
        ScheduleOperation(seq=10 * (i + 1), operation=f"op{i}", work_centre=w, std_min_per_unit=minutes_per_unit)
        for i, w in enumerate(wcs)], **kw)


def _sched(jobs, wcs=None, **kw) -> tuple[ScheduleToolInput, object]:
    inp = ScheduleToolInput(planning_start=MON, jobs=jobs, work_centres=wcs or [_wc("A"), _wc("B")], **kw)
    return inp, schedule_tool(inp)


def test_schedule_no_double_booking_and_operation_order():
    inp, out = _sched([_job("WO-1", wcs=("A", "B")), _job("WO-2", wcs=("A", "B"))])
    assert out.status == "optimal" and _check(inp, out) == []
    ops_a = sorted((op.start, op.end) for s in out.scheduled for op in s.operations if op.machine_id == "A")
    assert ops_a[0][1] <= ops_a[1][0]
    first = next(s for s in out.scheduled if s.operations[0].start == at(MON, 8))
    assert first.operations[0].end == at(MON, 12) and first.operations[1].start >= first.operations[0].end


def test_schedule_spans_overnight_and_skips_sunday():
    # 3 days of work starting Friday: Fri, Sat, (Sun off), Mon
    inp = ScheduleToolInput(planning_start=date(2026, 12, 11), jobs=[_job("WO-1", qty=1440)], work_centres=[_wc("A")])
    out = schedule_tool(inp)
    (wo,) = out.scheduled
    assert wo.planned_start == at(date(2026, 12, 11), 8) and wo.planned_finish == at(date(2026, 12, 14), 16)
    assert _check(inp, out) == []


def test_schedule_respects_downtime_units_efficiency_and_alternates():
    down = [DowntimeWindow(work_centre="A", start=MON, end_incl=date(2026, 12, 8))]
    inp, out = _sched([_job("WO-1", qty=480)], wcs=[_wc("A", units=2, efficiency=0.5), _wc("B")], downtime=down)
    op = out.scheduled[0].operations[0]
    assert op.machine_id == "A" and op.start == at(date(2026, 12, 9), 8)   # A down Mon-Tue, no alternate
    inp, out = _sched([_job("WO-1", qty=480)], wcs=[_wc("A", alternates=["B"]), _wc("B")], downtime=down)
    op = out.scheduled[0].operations[0]
    assert op.machine_id == "B" and op.start == at(MON, 8) and op.end == at(MON, 16)
    assert any(c.type == "machine_down" and c.work_centre == "A" for c in out.conflicts)
    assert _check(inp, out) == []


def test_schedule_op_does_not_run_through_downtime():
    down = [DowntimeWindow(work_centre="A", start=date(2026, 12, 8), end_incl=date(2026, 12, 8))]
    inp, out = _sched([_job("WO-1", qty=720)], wcs=[_wc("A")], downtime=down, allow_overtime=False)
    op = out.scheduled[0].operations[0]
    assert op.start.date() == date(2026, 12, 9) and _check(inp, out) == []


def test_schedule_down_status_without_window_uses_alternate():
    inp, out = _sched([_job("WO-1")], wcs=[_wc("A", status="DOWN - see maintenance log", alternates=["B"]), _wc("B")])
    assert out.scheduled[0].operations[0].machine_id == "B"


def test_schedule_earliest_start_and_wip_first():
    wip = [WipLoad(work_centre="A", load_minutes=600, order="OLD")]
    inp, out = _sched([_job("WO-1", earliest_start=date(2026, 12, 8))], wip=wip, allow_overtime=False)
    op = out.scheduled[0].operations[0]
    assert op.start == at(date(2026, 12, 8), 10)   # Tue: WIP uses Mon + 2 h of Tue
    assert any(c.type == "material_late" for c in out.conflicts)


def test_schedule_overtime_only_when_it_reduces_lateness():
    tight = _job("WO-1", qty=1200, due=date(2026, 12, 8))          # 20 h of work, 16 regular hours to Tue
    _, no_ot = _sched([tight], wcs=[_wc("A")], allow_overtime=False)
    assert no_ot.scheduled[0].days_late == 1
    inp, with_ot = _sched([tight], wcs=[_wc("A", ot_max_min_per_day=120)])
    wo = with_ot.scheduled[0]
    assert wo.days_late == 0 and wo.operations[0].uses_overtime
    assert any(c.type == "capacity_overload" for c in with_ot.conflicts) and _check(inp, with_ot) == []
    _, relaxed = _sched([_job("WO-1", qty=1200)], wcs=[_wc("A", ot_max_min_per_day=120)])
    assert not relaxed.scheduled[0].operations[0].uses_overtime


def test_schedule_lateness_counts_working_days():
    _, out = _sched([_job("WO-1", qty=480 * 3, due=MON)], wcs=[_wc("A")], allow_overtime=False)
    wo = out.scheduled[0]
    assert wo.planned_finish.date() == date(2026, 12, 9) and wo.days_late == 2 and wo.is_late
    assert any(c.type == "due_date_risk" and c.wo_id == "WO-1" for c in out.conflicts)


def test_schedule_reports_infeasible_jobs_and_schedules_the_rest():
    _, out = _sched([_job("WO-1"), _job("WO-2", wcs=("NOPE",)),
                     ScheduleJob(wo_id="WO-3", item_code="X", qty=1, due_date=MON, operations=[])])
    assert out.status == "infeasible" and [s.wo_id for s in out.scheduled] == ["WO-1"]
    assert "NOPE" in out.infeasibility_reason and "WO-3 has no operations" in out.infeasibility_reason
    _, out = _sched([_job("WO-1")], wcs=[_wc("A", status="DOWN")])
    assert out.status == "infeasible" and "down for the whole horizon" in out.infeasibility_reason


def test_schedule_timeout_falls_back_to_greedy(monkeypatch):
    monkeypatch.setattr(schedule_mod, "_solve_cp", lambda pb, hint, limit: (None, "UNKNOWN", set()))
    inp, out = _sched([_job("WO-1", wcs=("A", "B")), _job("WO-2", wcs=("A", "B"))])
    assert out.status == "timeout" and len(out.scheduled) == 2 and _check(inp, out) == []


def test_schedule_respects_holidays_and_time_limit():
    cal = PlanningCalendar(holidays=[date(2026, 12, 8)])
    inp, out = _sched([_job("WO-1", qty=720)], wcs=[_wc("A")], calendar=cal, allow_overtime=False, time_limit_s=2)
    assert out.scheduled[0].planned_finish == at(date(2026, 12, 9), 12) and out.solve_time_s < 3


# --- rule_check -----------------------------------------------------------------------------------

def _answer_plan(bundle: str):
    state, key, answer, master, plan = load(bundle)
    ak = AnswerKey.model_validate(answer)
    p = WorkOrderPlan.model_validate({
        **ak.model_dump(mode="json", exclude={"scoring_notes", "ot_work_centres"}), "system_id": "human",
        "suggested_pos": [{k: v for k, v in s.items() if k != "expected_usable_date"} for s in answer["suggested_pos"]],
        "flags": [{k: v for k, v in f.items() if k != "required"} for f in answer["flags"]]})
    sched = schedule_input(key, master, plan, date.fromisoformat(state["planning_start"]))
    lines = [CustomerPOLine.model_validate(ln) for d in key["documents"] if d["doc_type"] == "customer_po"
             for ln in (d.get("lines") or d["fields"].get("lines"))]
    rin = RuleCheckInput(plan=p, policy=policy(key), work_centres=sched.work_centres, downtime=sched.downtime,
                         material_ready={k: date.fromisoformat(v) for k, v in plan["ready"].items()},
                         order_lines=lines, calendar=CAL)
    return rin


@needs_bundles
@pytest.mark.parametrize("bundle", ALL_BUNDLES)
def test_rule_check_passes_answer_keys(bundle):
    assert rule_check(_answer_plan(bundle)).violations == []


def _rules(rin: RuleCheckInput, **plan_changes) -> set[str]:
    rin = rin.model_copy(update={"plan": rin.plan.model_copy(update=plan_changes)})
    return {v.rule for v in rule_check(rin).violations}


def _shift_op(wo: WorkOrder, i: int, start: datetime, end: datetime, **kw) -> WorkOrder:
    ops = list(wo.operations)
    ops[i] = ops[i].model_copy(update={"start": start, "end": end, **kw})
    return wo.model_copy(update={"operations": ops})


@needs_bundles
def test_rule_check_catches_seeded_violations():
    rin = _answer_plan("A_S7_clean")  # three orders on shared lines, with late orders and flags
    wos = rin.plan.work_orders
    a, b = wos[0], wos[1]
    # b's first op on a's first machine at the same time -> double booking
    op_a = a.operations[0]
    clash = _shift_op(b, 0, op_a.start, op_a.end, machine_id=op_a.machine_id)
    assert "double_booking" in _rules(rin, work_orders=[a, clash, *wos[2:]])
    # an operation that starts before the previous one ends
    bad = _shift_op(a, 1, a.operations[0].start, a.operations[1].end)
    assert "operation_order" in _rules(rin, work_orders=[bad, *wos[1:]])
    # Sunday work, unknown work centre
    sunday = date(2026, 12, 13)
    sun = _shift_op(a, len(a.operations) - 1, at(sunday, 8), at(sunday, 10))
    assert "non_working_day" in _rules(rin, work_orders=[sun, *wos[1:]])
    unk = _shift_op(a, 0, a.operations[0].start, a.operations[0].end, machine_id="LINE-99")
    assert "unknown_work_centre" in _rules(rin, work_orders=[unk, *wos[1:]])
    # 4 h overtime on one day; 7 x 10 h days in one week
    d = a.operations[0].start.date()
    ot = _shift_op(a, 0, at(d, 8), at(d, 21))
    assert "overtime_day_limit" in _rules(rin, work_orders=[ot, *wos[1:]])
    # material not ready
    early = rin.model_copy(update={"material_ready": {a.wo_id: a.operations[0].start.date() + timedelta(days=3)}})
    assert "material_not_ready" in {v.rule for v in rule_check(early).violations}
    # qty mismatch, missing flags
    assert "qty_mismatch" in _rules(rin, work_orders=[a.model_copy(update={"qty": a.qty + 10}), *wos[1:]])
    assert "missing_flag" in _rules(rin, flags=[])
    # the untouched plan passes
    assert rule_check(rin).violations == []


def test_rule_check_downtime_and_week_limit():
    wcs = [_wc("A")]
    week = [at(MON + timedelta(days=i), 8) for i in range(6)]
    ops = [dict(seq=i, operation="sew", machine_id="A", start=s, end=s + timedelta(hours=12), qty=1, uses_overtime=True)
           for i, s in enumerate(week)]
    plan = WorkOrderPlan(bundle_id="t", system_id="human", created_at=at(MON, 8), planning_start=MON,
                         work_orders=[WorkOrder(wo_id="WO-1", item_code="X", qty=1, uom="pcs", customer_po="P",
                                                priority=1, due_date=date(2026, 12, 31), planned_start=week[0],
                                                planned_finish=ops[-1]["end"], is_late=False, days_late=0,
                                                operations=ops)],
                         material_requirements=[], rationale="t",
                         flags=[Flag(flag_id="F1", type="machine_down", severity="high", message="A down",
                                     sources=[SourceRef(document="maintenance_log.xlsx", field="status")],
                                     needs_human=False)])
    down = [DowntimeWindow(work_centre="A", start=date(2026, 12, 9), end_incl=date(2026, 12, 9))]
    pol = policy({"documents": []})
    rules = {v.rule for v in rule_check(RuleCheckInput(plan=plan, policy=pol, work_centres=wcs, downtime=down)).violations}
    assert rules == {"downtime", "overtime_day_limit", "overtime_week_limit"}


# --- the multi-agent graph with the real tools ---------------------------------------------------

@needs_bundles
@pytest.mark.parametrize("bundle", DEV)
def test_graph_end_to_end_with_real_tools(bundle, monkeypatch):
    """Fake LLMs (multi_agent/fakes.py), real tools: the graph picks tools.* up by itself."""
    from core import runner
    from core.schemas import ApprovalDecision, RunConfig
    from multi_agent import fakes, tools as registry

    monkeypatch.delenv("WOP_STUB_TOOLS", raising=False)
    for name in ("mrp_tool", "schedule_tool", "rule_check"):
        assert registry.resolve(name)[1] is False, f"{name} still resolves to the stub"
    fakes.install(extraction_key=json.loads((BUNDLES / bundle / "extraction_key.json").read_text()))
    try:
        events: list = []
        rid = f"run_tools_{bundle}"
        r = runner.start_run(rid, BUNDLES / bundle, RunConfig(mode="multi_agent"), events.append)
        assert r.status == "awaiting_approval", r.error
        calls = [e for e in events if e.type == "tool_call"]
        assert {e.tool for e in calls} == {"mrp_tool", "schedule_tool", "rule_check"}
        assert all(e.payload["stub"] is False for e in calls)
        assert r.plan.work_orders and all(w.operations for w in r.plan.work_orders)
        r = runner.resume_run(rid, ApprovalDecision(decision="approve"), events.append)
        assert r.status == "completed"
    finally:
        fakes.uninstall()
