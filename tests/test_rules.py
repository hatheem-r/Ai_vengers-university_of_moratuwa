"""rule_check tests: a clean plan passes; each rule catches its own breakage."""

import copy
import json

import pytest

from project.tools.rules import RULES, make_rule_check_tool, run_rules

PO_DOC = {"document": "customer_po_4471.pdf", "field": "qty", "value": 2400, "page": 1}


def base_plan():
    # 24 Nov 2026 is a Tuesday; shift 08:00-17:00 Mon-Fri.
    return {
        "bundle_id": "dev_A_S1_clean", "system_id": "B", "created_at": "2026-11-24T09:00:00+05:30",
        "planning_start": "2026-11-24",
        "work_orders": [{
            "wo_id": "WO-001", "item_code": "POLO-PIQ-NVY", "qty": 2400, "uom": "pcs", "customer_po": "PO-4471",
            "priority": 1, "due_date": "2026-12-08",
            "planned_start": "2026-11-24T08:00:00+05:30", "planned_finish": "2026-12-04T17:00:00+05:30",
            "is_late": False, "days_late": 0,
            "operations": [
                {"seq": 10, "operation": "Cutting", "machine_id": "CUT-01", "start": "2026-11-24T08:00:00+05:30",
                 "end": "2026-11-25T12:00:00+05:30", "qty": 2400},
                {"seq": 20, "operation": "Sewing", "machine_id": "LINE-2", "start": "2026-11-25T13:00:00+05:30",
                 "end": "2026-12-03T17:00:00+05:30", "qty": 2400},
                {"seq": 30, "operation": "Packing", "machine_id": "PACK-01", "start": "2026-12-04T08:00:00+05:30",
                 "end": "2026-12-04T17:00:00+05:30", "qty": 2400},
            ],
        }],
        "material_requirements": [{"item_code": "FAB-PIQ-NVY", "uom": "kg", "required_qty": 612, "on_hand_qty": 700,
                                   "reserved_qty": 0, "on_order_qty": 0, "net_shortage_qty": 0, "for_work_orders": ["WO-001"]}],
        "shortages": [], "suggested_pos": [], "flags": [], "assumptions": [], "rationale": "Single order, on time.",
    }


def base_input(**overrides):
    data = {
        "plan": base_plan(),
        "customer_orders": [{"customer_po": "PO-4471", "item_code": "POLO-PIQ-NVY", "qty": 2400, "uom": "pcs",
                             "due_date": "2026-12-08", "source": PO_DOC}],
        "machines": ["CUT-01", "LINE-2", "PACK-01"],
        "calendar": {},
    }
    data.update(overrides)
    return data


def found(result, rule=None, flag_type=None, text=""):
    return [v for v in result.violations
            if (rule is None or v.rule == rule) and (flag_type is None or v.flag_type == flag_type) and text in v.message]


def test_clean_plan_passes():
    r = run_rules(base_input())
    assert r.passed and r.violations == []
    assert set(r.rules_run) >= {"order_coverage", "schedule_feasibility", "plan_consistency", "missing_fields"}
    assert any(s.startswith("invoice_po_match: no invoices") for s in r.rules_skipped)
    assert set(r.rules_run) | {s.split(":")[0] for s in r.rules_skipped} == set(RULES)


def test_document_conflict_needs_human_and_cites_both_documents():
    other = {"customer_po": "PO-4471", "item_code": "POLO-PIQ-NVY", "qty": 2600, "due_date": "2026-12-08",
             "source": {"document": "buyer_email_amendment.txt", "field": "qty", "value": 2600, "row": 4}}
    r = run_rules(base_input(customer_orders=base_input()["customer_orders"] + [other]))
    (v,) = found(r, "document_conflicts")
    assert v.flag_type == "conflict" and v.needs_human and v.severity == "high"
    assert "qty" in v.message and {s.document for s in v.sources} == {"customer_po_4471.pdf", "buyer_email_amendment.txt"}
    assert not r.passed


def test_same_document_repeats_are_not_conflicts():
    dup = dict(base_input()["customer_orders"][0], qty=2600)
    r = run_rules(base_input(customer_orders=base_input()["customer_orders"] + [dup]))
    assert found(r, "document_conflicts") == []


def invoice_case(**policy):
    src = lambda doc: {"document": doc, "field": "qty"}  # noqa: E731
    return base_input(
        purchase_orders=[{"po_number": "PO-77", "item_code": "FAB-PIQ-NVY", "qty": 600, "unit_price": 4.0,
                          "supplier_id": "SUP-03", "source": src("po_77.pdf")}],
        invoices=[
            {"invoice_number": "INV-1", "po_number": "PO-77", "item_code": "FAB-PIQ-NVY", "qty": 620, "unit_price": 4.2,
             "supplier_id": "SUP-03", "source": src("invoice_1.pdf")},
            {"invoice_number": "INV-2", "po_number": None, "item_code": "BTN", "qty": 10, "source": src("invoice_2.pdf")},
            {"invoice_number": "INV-3", "po_number": "PO-99", "item_code": "BTN", "qty": 10, "source": src("invoice_3.pdf")},
            {"invoice_number": "INV-4", "po_number": "po-77 ", "item_code": "THREAD", "qty": 10, "source": src("invoice_4.pdf")},
        ],
        receipts=[{"po_number": "PO-77", "item_code": "FAB-PIQ-NVY", "qty": 590, "source": src("grn_12.xlsx")}],
        policy=policy,
    )


def test_invoice_po_receipt_checks():
    r = run_rules(invoice_case())
    msgs = " | ".join(v.message for v in found(r, "invoice_po_match"))
    assert "unit price 4.2" in msgs and "differs from PO PO-77 price 4.0" in msgs
    assert "bill 620 of FAB-PIQ-NVY but PO PO-77 ordered 600" in msgs
    assert "only 590 were received" in msgs
    assert "INV-2 (BTN) has no PO number" in msgs
    assert "PO PO-99, which is not in the documents" in msgs
    assert "bills THREAD, which is not on PO po-77" in msgs  # PO numbers matched case/space-insensitively
    received = found(r, "invoice_po_match", text="received")[0]
    assert {s.document for s in received.sources} == {"invoice_1.pdf", "grn_12.xlsx"}
    assert all(v.flag_type in ("invoice_po_mismatch", "missing_field") for v in found(r, "invoice_po_match"))


def test_invoice_tolerances():
    r = run_rules(invoice_case(price_tolerance_pct=10, qty_tolerance_pct=5))
    msgs = " | ".join(v.message for v in found(r, "invoice_po_match"))
    assert "unit price" not in msgs and "ordered 600" not in msgs  # 620 is within 5% of 600 and of 590


def test_missing_fields():
    orders = [{"customer_po": "PO-4471", "item_code": "POLO-PIQ-NVY", "qty": 2400, "source": PO_DOC}]
    (v,) = found(run_rules(base_input(customer_orders=orders)), "missing_fields")
    assert v.flag_type == "missing_field" and "has no due_date" in v.message and v.sources[0].document == "customer_po_4471.pdf"


def test_order_coverage():
    orders = [
        {"customer_po": "PO-4471", "item_code": "POLO-PIQ-NVY", "qty": 2500, "due_date": "2026-12-07", "source": PO_DOC},
        {"customer_po": "PO-4480", "item_code": "TEE-WHT", "qty": 100, "due_date": "2026-12-20"},
    ]
    r = run_rules(base_input(customer_orders=orders))
    assert found(r, "order_coverage", "due_date_risk", "PO-4480 / TEE-WHT has no work order")
    assert found(r, "order_coverage", "conflict", "is for 2500 but work orders WO-001 plan 2400")
    assert found(r, "order_coverage", "conflict", "due date 2026-12-08 differs from customer order PO-4471 due date 2026-12-07")

    plan = base_plan()
    plan["work_orders"][0]["customer_po"] = "PO-0000"
    r = run_rules(base_input(plan=plan))
    assert found(r, "order_coverage", text="refers to customer order PO-0000, which is not in the documents")


def test_split_orders_are_summed():
    plan = base_plan()
    a = plan["work_orders"][0]
    b = copy.deepcopy(a)
    a["qty"], b["qty"], b["wo_id"] = 1400, 1000, "WO-002"
    for op in b["operations"]:
        op["machine_id"] += "-B"
    r = run_rules(base_input(plan={**plan, "work_orders": [a, b]}, machines=[]))
    assert found(r, "order_coverage") == []


def schedule_plan():
    plan = base_plan()
    second = copy.deepcopy(plan["work_orders"][0])
    second.update(wo_id="WO-002", customer_po="PO-4471")
    # WO-002 cutting overlaps WO-001 cutting on CUT-01
    second["operations"] = [{"seq": 10, "operation": "Cutting", "machine_id": "CUT-01",
                             "start": "2026-11-25T08:00:00+05:30", "end": "2026-11-25T16:00:00+05:30", "qty": 1}]
    second["planned_start"], second["planned_finish"] = "2026-11-25T08:00:00+05:30", "2026-11-25T16:00:00+05:30"
    plan["work_orders"].append(second)
    return plan


def test_schedule_double_booking_downtime_and_materials():
    r = run_rules(base_input(
        plan=schedule_plan(), customer_orders=[],
        downtime=[{"machine_id": "LINE-2", "start": "2026-11-30T08:00", "end": "2026-11-30T12:00", "reason": "needle bar service"}],
        material_ready={"WO-001": "2026-11-26"},
    ))
    assert found(r, "schedule_feasibility", "capacity_overload", "Machine CUT-01 is double-booked")
    (down,) = found(r, "schedule_feasibility", "machine_down")
    assert "needle bar service" in down.message and down.wo_id == "WO-001"
    assert found(r, "schedule_feasibility", "shortage", "WO-001 starts 2026-11-24 but its materials are only available on 2026-11-26")
    assert not r.passed


def test_schedule_order_hours_unknown_machine_and_start():
    plan = base_plan()
    ops = plan["work_orders"][0]["operations"]
    ops[1]["machine_id"] = "LINE-9"
    ops[1]["start"] = "2026-11-25T11:00:00+05:30"  # before cutting ends at 12:00
    ops[0]["start"] = plan["work_orders"][0]["planned_start"] = "2026-11-23T07:00:00+05:30"  # Monday 07:00
    ops[2]["start"] = "2026-12-05T09:00:00+05:30"  # Saturday
    r = run_rules(base_input(plan=plan))
    msgs = " | ".join(v.message for v in found(r, "schedule_feasibility"))
    assert "uses machine LINE-9, which is not in the machine list" in msgs
    assert "starts before the previous operation of WO-001 ends" in msgs
    assert "before the planning start 2026-11-24" in msgs
    assert "starts 2026-11-23 07:00, outside working hours" in msgs
    assert "starts 2026-12-05 09:00, outside working hours" in msgs


def overtime_plan():
    plan = base_plan()
    ops = plan["work_orders"][0]["operations"]
    # Spans Tue evening (full 17:00-20:00 overtime window = 3 h, as schedule_tool would use it) and Wed to 19:30 (2.5 h)
    ops[0].update(end="2026-11-25T19:30:00+05:30", uses_overtime=True)
    ops[1]["start"] = "2026-11-26T08:00:00+05:30"
    return plan


def test_overtime_limits():
    cal = {"overtime_end": "20:00"}
    r = run_rules(base_input(plan=overtime_plan(), calendar=cal, policy={"max_overtime_hours_per_day": 2}))
    day = found(r, "overtime_policy", "compliance_breach", "Machine CUT-01 has")
    assert [v.message.split(" (")[0] for v in day] == [
        "Machine CUT-01 has 3.0 h overtime on 2026-11-24", "Machine CUT-01 has 2.5 h overtime on 2026-11-25"]
    assert all(v.needs_human for v in day)
    assert found(r, "schedule_feasibility") == []  # inside the overtime window, so not outside hours
    assert not found(run_rules(base_input(plan=overtime_plan(), calendar=cal, policy={"max_overtime_hours_per_day": 3})),
                     "overtime_policy")

    r = run_rules(base_input(plan=overtime_plan(), calendar=cal, policy={"max_overtime_hours_per_week": 4}))
    assert found(r, "overtime_policy", text="5.5 h overtime in the week of 2026-11-23 (limit 4 h/week)")

    # Without overtime_end: 19:30 is outside working hours, and only the last day's 2.5 h is measurable
    r = run_rules(base_input(plan=overtime_plan(), policy={"max_overtime_hours_per_day": 2}))
    assert found(r, "schedule_feasibility", text="outside working hours")
    assert [v.message.split(" (")[0] for v in found(r, "overtime_policy")] == ["Machine CUT-01 has 2.5 h overtime on 2026-11-25"]


def test_non_working_day_breach():
    plan = base_plan()
    plan["work_orders"][0]["operations"][2].update(start="2026-12-05T08:00:00+05:30", end="2026-12-05T17:00:00+05:30")
    plan["work_orders"][0]["planned_finish"] = "2026-12-05T17:00:00+05:30"
    r = run_rules(base_input(plan=plan))
    assert found(r, "overtime_policy", "compliance_breach", "planned on Saturday 2026-12-05, a non-working day")
    r = run_rules(base_input(plan=plan, policy={"allow_non_working_days": True}))
    assert not found(r, "overtime_policy") and not found(r, "schedule_feasibility")


def test_compliance_certificates():
    req = {"certificate_type": "OEKO-TEX", "item_code": "FAB-PIQ-NVY",
           "source": {"document": "buyer_requirements.pdf", "field": "certification"}}
    r = run_rules(base_input(certificate_requirements=[req]))
    (v,) = found(r, "compliance")
    assert v.flag_type == "compliance_breach" and v.needs_human and v.wo_id == "WO-001"
    assert "No OEKO-TEX certificate for FAB-PIQ-NVY" in v.message and v.sources[0].document == "buyer_requirements.pdf"

    cert = {"certificate_type": "oeko-tex", "item_code": "FAB-PIQ-NVY", "valid_until": "2026-11-30",
            "source": {"document": "oekotex_cert.pdf", "field": "valid_until"}}
    r = run_rules(base_input(certificate_requirements=[req], certificates=[cert]))
    assert found(r, "compliance", text="expires 2026-11-30, before production finishes on 2026-12-04")

    cert["valid_until"] = "2027-06-30"
    assert found(run_rules(base_input(certificate_requirements=[req], certificates=[cert])), "compliance") == []

    by_po = {"certificate_type": "BSCI", "customer_po": "PO-4471"}
    assert found(run_rules(base_input(certificate_requirements=[by_po])), "compliance", text="No BSCI certificate for POLO-PIQ-NVY")


def test_late_order_must_be_flagged():
    plan = base_plan()
    wo = plan["work_orders"][0]
    wo["due_date"] = "2026-12-03"
    plan_unflagged_input = base_input(plan=plan, customer_orders=[])
    r = run_rules(plan_unflagged_input)
    assert found(r, "plan_consistency", "due_date_risk", "is_late is False")
    assert found(r, "plan_consistency", "due_date_risk", "has no due_date_risk flag")
    assert not r.passed

    wo.update(is_late=True, days_late=1)
    plan["flags"] = [{"flag_id": "F1", "type": "due_date_risk", "severity": "medium",
                      "message": "WO-001 finishes 1 working day late.", "sources": [PO_DOC], "needs_human": True}]
    r = run_rules(base_input(plan=plan, customer_orders=[]))
    assert found(r, "plan_consistency") == [] and r.passed


def test_shortage_and_flag_hygiene():
    plan = base_plan()
    plan["material_requirements"][0]["net_shortage_qty"] = 10
    plan["shortages"] = [{"item_code": "BTN-4H-15L", "uom": "pcs", "shortfall_qty": 50, "need_by": "2026-11-24",
                          "affected_work_orders": ["WO-001"], "cause": "insufficient_stock"}]
    plan["flags"] = [
        {"flag_id": "F1", "type": "conflict", "severity": "high", "message": "Qty differs.", "sources": [], "needs_human": False},
        {"flag_id": "F1", "type": "missing_field", "severity": "low", "message": "x", "sources": [PO_DOC], "needs_human": False},
    ]
    r = run_rules(base_input(plan=plan))
    assert found(r, "plan_consistency", "shortage", "FAB-PIQ-NVY has a net shortage of 10 kg but no shortage entry")
    assert found(r, "plan_consistency", "shortage", "BTN-4H-15L has neither a suggested PO nor a flag")
    assert found(r, "plan_consistency", "conflict", "Flag F1 (conflict) is unresolved but not marked needs_human")
    assert found(r, "plan_consistency", text="Flag F1 cites no source document")
    assert found(r, "plan_consistency", text="Flag id F1 is used twice")

    plan["suggested_pos"] = [{"item_code": "BTN-4H-15L", "qty": 50, "uom": "pcs", "need_by": "2026-11-24",
                              "expedite": True, "reason": "cover"}]
    assert not found(run_rules(base_input(plan=plan)), text="neither a suggested PO")


def test_already_flagged_conflict_does_not_fail_the_plan():
    other = {"customer_po": "PO-4471", "item_code": "POLO-PIQ-NVY", "qty": 2600, "due_date": "2026-12-08",
             "source": {"document": "buyer_email_amendment.txt", "field": "qty"}}
    plan = base_plan()
    plan["flags"] = [{"flag_id": "F1", "type": "conflict", "severity": "high", "needs_human": True,
                      "message": "Order quantity differs between the PO and the buyer's email.",
                      "sources": [{"document": "buyer_email_amendment.txt", "field": "qty", "value": 2600}]}]
    r = run_rules(base_input(plan=plan, customer_orders=base_input()["customer_orders"] + [other]))
    (v,) = found(r, "document_conflicts")
    assert v.already_flagged and r.passed


def test_tool_json_and_validation_errors():
    tool = make_rule_check_tool()
    out = json.loads(tool.invoke(base_input()))
    assert out["passed"] is True and out["violations"] == []
    bad = base_input()
    del bad["plan"]["work_orders"][0]["due_date"]
    msg = tool.invoke(bad)
    assert "plan.work_orders.0.due_date: Field required" in msg


@pytest.mark.parametrize("rule", sorted(RULES))
def test_every_rule_is_documented_in_the_module(rule):
    import project.tools.rules as mod
    assert rule in mod.__doc__
