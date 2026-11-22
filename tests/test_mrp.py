"""mrp_tool tests. Every expected number is worked out by hand in the comments."""

import json
from datetime import date

import pytest
from langchain_core.tools import ToolException

from project.core.schemas import MaterialRequirement, Shortage, SuggestedPO
from project.tools.mrp import make_mrp_tool, run_mrp

START = "2026-11-24"


def polo_input(**overrides):
    """The API.md §5.3 example: 2400 navy polos, fabric 0.25 kg/pc, 3 buttons/pc, 2% wastage."""
    data = {
        "planning_start": START,
        "work_orders": [{"wo_id": "WO-001", "item_code": "POLO-PIQ-NVY", "qty": 2400, "uom": "pcs", "need_by": "2026-11-26"}],
        "bom": [
            {"parent_item": "POLO-PIQ-NVY", "component_item": "FAB-PIQ-NVY", "qty_per": 0.25, "uom": "kg", "wastage_pct": 2},
            {"parent_item": "POLO-PIQ-NVY", "component_item": "BTN-4H-15L", "qty_per": 3, "uom": "pcs", "wastage_pct": 2},
        ],
        "stock": [
            {"item_code": "FAB-PIQ-NVY", "on_hand_qty": 367.2, "uom": "kg"},
            {"item_code": "BTN-4H-15L", "on_hand_qty": 12000, "uom": "pcs", "reserved_qty": 2000},
        ],
        "suppliers": [
            {"item_code": "FAB-PIQ-NVY", "supplier_id": "SUP-03", "lead_time_days": 2, "pack_size": 50},
            {"item_code": "FAB-PIQ-NVY", "supplier_id": "SUP-07", "lead_time_days": 9, "unit_price": 1.0},
        ],
    }
    data.update(overrides)
    return data


def by_item(rows):
    return {r.item_code: r for r in rows}


def test_api_example_matches_by_hand():
    r = run_mrp(polo_input())
    reqs = by_item(r.material_requirements)
    # 2400 * 0.25 * 1.02 = 612 kg; 2400 * 3 * 1.02 = 7344 pcs (no float noise -> not 7345)
    assert reqs["FAB-PIQ-NVY"] == MaterialRequirement(
        item_code="FAB-PIQ-NVY", uom="kg", required_qty=612.0, on_hand_qty=367.2, reserved_qty=0,
        on_order_qty=0, net_shortage_qty=244.8, for_work_orders=["WO-001"])
    assert reqs["BTN-4H-15L"].required_qty == 7344 and reqs["BTN-4H-15L"].net_shortage_qty == 0
    assert r.shortages == [Shortage(item_code="FAB-PIQ-NVY", uom="kg", shortfall_qty=244.8, need_by=date(2026, 11, 26),
                                    affected_work_orders=["WO-001"], cause="insufficient_stock")]
    # SUP-03 is fastest (2 days -> 26 Nov); 244.8 rounded up to the 50 kg pack = 250
    (po,) = r.suggested_pos
    assert (po.supplier_id, po.qty, po.need_by, po.expedite) == ("SUP-03", 250, date(2026, 11, 26), False)
    assert "rounded up to 250" in po.reason
    fab = by_item(r.details)["FAB-PIQ-NVY"]
    assert fab.wastage_qty == pytest.approx(12.0) and fab.make_or_buy == "buy" and fab.level == 1
    assert r.work_orders[0].material_ready_date is None and r.work_orders[0].short_items == ["FAB-PIQ-NVY"]
    assert r.issues == []


def test_reserved_stock_is_not_usable():
    # 10000 usable buttons, need 7344 -> fine; reserve 5000 -> 7000 usable -> 344 short
    r = run_mrp(polo_input(stock=[{"item_code": "BTN-4H-15L", "on_hand_qty": 12000, "uom": "pcs", "reserved_qty": 5000},
                                  {"item_code": "FAB-PIQ-NVY", "on_hand_qty": 1000, "uom": "kg"}]))
    assert by_item(r.shortages)["BTN-4H-15L"].shortfall_qty == 344
    po = by_item(r.suggested_pos)["BTN-4H-15L"]
    assert po.supplier_id is None and po.qty == 344


def two_wo_input(po_date):
    # Two orders for one component: 100 kg needed 1 Dec (WO-A), 100 kg needed 10 Dec (WO-B).
    # Stock 120 kg, PO of 100 kg arriving po_date.
    return {
        "planning_start": START,
        "work_orders": [
            {"wo_id": "WO-B", "item_code": "P", "qty": 100, "need_by": "2026-12-10"},
            {"wo_id": "WO-A", "item_code": "P", "qty": 100, "need_by": "2026-12-01"},
        ],
        "bom": [{"parent_item": "P", "component_item": "C", "qty_per": 1, "uom": "kg"}],
        "stock": [{"item_code": "C", "on_hand_qty": 120, "uom": "kg"}],
        "open_pos": [{"po_number": "PO-9", "item_code": "C", "open_qty": 100, "uom": "kg", "expected_date": po_date}],
    }


def test_time_phased_po_in_time():
    # WO-A (earliest) takes 100 of stock; WO-B takes 20 stock + 80 from PO on 5 Dec <= 10 Dec: no shortage
    r = run_mrp(two_wo_input("2026-12-05"))
    assert r.shortages == [] and r.suggested_pos == []
    ready = {w.wo_id: w.material_ready_date for w in r.work_orders}
    assert ready == {"WO-A": date(2026, 11, 24), "WO-B": date(2026, 12, 5)}
    assert by_item(r.material_requirements)["C"].on_order_qty == 100


def test_time_phased_late_po_is_supplier_delay():
    # Same, but the PO arrives 15 Dec: WO-B's 80 kg arrive after its need date
    r = run_mrp(two_wo_input("2026-12-15"))
    (s,) = r.shortages
    assert (s.shortfall_qty, s.cause, s.need_by, s.affected_work_orders) == (80, "supplier_delay", date(2026, 12, 10), ["WO-B"])
    assert r.suggested_pos == []  # the late PO still covers it eventually; expediting is the planner's call
    assert {w.wo_id: w.material_ready_date for w in r.work_orders}["WO-B"] == date(2026, 12, 15)
    assert any("PO PO-9" in i and "after the need date" in i for i in r.issues)
    d = by_item(r.details)["C"]
    assert (d.on_order_by_need_qty, d.on_order_late_qty, d.uncovered_qty, d.covered_by_date) == (0, 100, 0, date(2026, 12, 15))


def test_quality_hold_cause():
    # Need 612 kg; 700 on hand but 100 on QC hold -> 600 usable -> 12 short, would be covered by held stock
    r = run_mrp(polo_input(stock=[{"item_code": "FAB-PIQ-NVY", "on_hand_qty": 700, "uom": "kg", "quality_hold_qty": 100},
                                  {"item_code": "BTN-4H-15L", "on_hand_qty": 99999, "uom": "pcs"}]))
    (s,) = r.shortages
    assert (s.shortfall_qty, s.cause) == (12, "quality_reject")


def test_safety_stock_and_over_reserved_warning():
    r = run_mrp(polo_input(
        stock=[{"item_code": "FAB-PIQ-NVY", "on_hand_qty": 700, "uom": "kg"},
               {"item_code": "BTN-4H-15L", "on_hand_qty": 100, "uom": "pcs", "reserved_qty": 500}],
        safety_stock={"FAB-PIQ-NVY": 100},
    ))
    # 700 - 100 safety = 600 usable -> 12 short; buttons: reserved > on hand -> usable 0, all 7344 short
    sh = by_item(r.shortages)
    assert sh["FAB-PIQ-NVY"].shortfall_qty == 12 and sh["BTN-4H-15L"].shortfall_qty == 7344
    assert any("exceed on hand" in i for i in r.issues)


def test_supplier_moq_and_choice():
    data = polo_input(suppliers=[
        {"item_code": "FAB-PIQ-NVY", "supplier_id": "SLOW", "lead_time_days": 10},
        {"item_code": "FAB-PIQ-NVY", "supplier_id": "FAST", "lead_time_days": 5, "moq": 500},
        {"item_code": "FAB-PIQ-NVY", "supplier_id": "FAST-DEAR", "lead_time_days": 5, "moq": 500, "unit_price": 9},
    ])
    po = by_item(run_mrp(data).suggested_pos)["FAB-PIQ-NVY"]
    # FAST and FAST-DEAR both 5 days; FAST has no price (0) so it wins the tie; MOQ 500 > 244.8
    # arrives 29 Nov, 3 days after need date 26 Nov -> expedite
    assert (po.supplier_id, po.qty, po.expedite) == ("FAST", 500, True)
    assert "3 days after the need date" in po.reason


def test_units_are_converted_or_reported():
    data = {
        "planning_start": START,
        "work_orders": [{"wo_id": "WO-1", "item_code": "SHIRT", "qty": 100}],
        "bom": [
            {"parent_item": "SHIRT", "component_item": "THREAD", "qty_per": 50, "uom": "m"},
            {"parent_item": "SHIRT", "component_item": "DYE", "qty_per": 20, "uom": "g"},
            {"parent_item": "SHIRT", "component_item": "FAB", "qty_per": 1.5, "uom": "m"},
        ],
        "stock": [
            {"item_code": "thread ", "on_hand_qty": 5000, "uom": "Meters"},   # alias + messy code
            {"item_code": "DYE", "on_hand_qty": 1.5, "uom": "kg"},           # 1500 g
            {"item_code": "FAB", "on_hand_qty": 100, "uom": "kg"},           # no kg<->m for fabric given
        ],
        "open_pos": [{"po_number": "PO-1", "item_code": "FAB", "open_qty": 100, "uom": "yd", "expected_date": START}],
    }
    r = run_mrp(data)
    d = by_item(r.details)
    assert d["THREAD"].available_now_qty == 5000 and d["THREAD"].shortfall_qty == 0
    assert d["DYE"].on_hand_qty == 1500 and d["DYE"].shortfall_qty == 500           # need 2000 g
    assert d["FAB"].on_hand_qty == 0 and d["FAB"].on_order_by_need_qty == pytest.approx(91.44)  # 100 yd
    assert d["FAB"].shortfall_qty == pytest.approx(150 - 91.44)
    assert any("FAB: cannot convert stock unit from 'kg' to 'm'" in i for i in r.issues)

    # With an item-specific conversion the fabric stock counts: 100 kg * 3 m/kg = 300 m
    data["uom_conversions"] = [{"item_code": "FAB", "from_uom": "kg", "to_uom": "m", "factor": 3}]
    assert by_item(run_mrp(data).details)["FAB"].shortfall_qty == 0


def test_bom_versions():
    bom = [
        {"parent_item": "P", "component_item": "OLD", "qty_per": 1, "uom": "pcs", "bom_version": "v2"},
        {"parent_item": "P", "component_item": "NEW", "qty_per": 1, "uom": "pcs", "bom_version": "v10"},
    ]
    base = {"planning_start": START, "bom": bom}
    r = run_mrp({**base, "work_orders": [{"wo_id": "W", "item_code": "P", "qty": 5}]})
    assert [m.item_code for m in r.material_requirements] == ["NEW"]  # v10 > v2 (natural order)
    assert any("several BOM versions" in i for i in r.issues)
    r = run_mrp({**base, "work_orders": [{"wo_id": "W", "item_code": "P", "qty": 5, "bom_version": "v2"}]})
    assert [m.item_code for m in r.material_requirements] == ["OLD"] and r.issues == []


def test_multi_level_nets_subassembly_stock():
    # LAMP needs 1 BASE (made) + 2 BULB; BASE needs 3 SCREW. 10 lamps, 4 BASE in stock
    # -> make 6 BASE -> 18 SCREW (not 30); BULB 20
    data = {
        "planning_start": START,
        "work_orders": [{"wo_id": "WO-1", "item_code": "LAMP", "qty": 10, "need_by": "2026-11-30"}],
        "bom": [
            {"parent_item": "LAMP", "component_item": "BASE", "qty_per": 1, "uom": "pcs"},
            {"parent_item": "LAMP", "component_item": "BULB", "qty_per": 2, "uom": "pcs"},
            {"parent_item": "BASE", "component_item": "SCREW", "qty_per": 3, "uom": "pcs"},
        ],
        "stock": [{"item_code": "BASE", "on_hand_qty": 4, "uom": "pcs"},
                  {"item_code": "SCREW", "on_hand_qty": 100, "uom": "pcs"},
                  {"item_code": "BULB", "on_hand_qty": 100, "uom": "pcs"}],
    }
    r = run_mrp(data)
    reqs = by_item(r.material_requirements)
    assert reqs["BASE"].required_qty == 10 and reqs["BASE"].net_shortage_qty == 0
    assert reqs["SCREW"].required_qty == 18 and reqs["BULB"].required_qty == 20
    (pp,) = r.planned_production
    assert (pp.item_code, pp.qty, pp.for_work_orders) == ("BASE", 6, ["WO-1"])
    d = by_item(r.details)
    assert (d["BASE"].level, d["SCREW"].level, d["BASE"].make_or_buy) == (1, 2, "make")
    assert r.shortages == [] and r.work_orders[0].material_ready_date == date(2026, 11, 24)


def test_overdue_po_and_missing_bom_are_reported():
    data = two_wo_input("2026-11-01")
    data["work_orders"].append({"wo_id": "WO-X", "item_code": "UNKNOWN", "qty": 1})
    r = run_mrp(data)
    assert any("PO PO-9" in i and "still open" in i for i in r.issues)
    assert any("WO-X: no BOM" in i for i in r.issues)
    assert {w.wo_id: w.material_ready_date for w in r.work_orders}["WO-X"] is None


def test_bom_cycle_is_an_error():
    data = {"planning_start": START, "work_orders": [{"wo_id": "W", "item_code": "A", "qty": 1}],
            "bom": [{"parent_item": "A", "component_item": "B", "qty_per": 1, "uom": "pcs"},
                    {"parent_item": "B", "component_item": "A", "qty_per": 1, "uom": "pcs"}]}
    with pytest.raises(ToolException, match="cycle: A -> B -> A"):
        run_mrp(data)


def test_tool_returns_json_and_reports_bad_input():
    tool = make_mrp_tool()
    out = json.loads(tool.invoke(polo_input()))
    assert out["shortages"][0]["shortfall_qty"] == 244.8
    SuggestedPO.model_validate(out["suggested_pos"][0])  # plan-compatible

    bad = polo_input()
    bad["work_orders"][0]["qty"] = -5
    msg = tool.invoke(bad)
    # Returned to the model (not raised), naming the exact field
    assert isinstance(msg, str) and "work_orders.0.qty: Input should be greater than 0" in msg
