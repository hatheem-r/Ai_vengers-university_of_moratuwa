"""Generate synthetic factory document bundles and their answer keys.

Usage (from the repository root):
    python -m data.generator.generate --all                      # 18 test + 3 dev + 3 scale bundles
    python -m data.generator.generate --profile A --scenario S3 --mess typical --seed 7
    python -m data.generator.generate --profile A --scenario S1 --mess typical --seed 21 --orders 5

Each bundle folder gets:
    inputs/              the only files a system may read
    state.json           hidden ground truth the documents were rendered from
    manifest.json        scenario, mess level, applied defects, planted conflicts, file list
    answer_key.json      expected WorkOrderPlan (+ expected flags) computed by solver.py
    extraction_key.json  what each input document says, field by field (for Intake evaluation)
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import date, datetime
from pathlib import Path

from . import render_pdf as rp
from . import render_scan as rs
from . import render_xlsx as rx
from .common import add_wd
from .context import Ctx
from .masters import MASTERS
from .scenarios import HUMAN, SCENARIOS, build_state, catalogue
from .solver import solve

ROOT = Path(__file__).resolve().parents[1]
BUNDLES = ROOT / "bundles"
GENERATOR_VERSION = "1.0"


def src(document, field, value=None, row=None, page=None):
    return {"document": document, "field": field, "value": value, "page": page, "row": row}


def render_bundle(state, master, plan, out_dir):
    ctx = Ctx(state, master, plan, out_dir)
    p = ctx.p
    ctx.dmy_docs = {"capacity_shift_plan.xlsx"} if ctx.has("dmy_dates") else set()
    if ctx.has("dmy_dates"):
        ctx.applied_defect("dmy_dates", "capacity_shift_plan.xlsx + customer POs", "dates written as DD/MM/YYYY text")
    # customer POs
    scanned_done = False
    for i, o in enumerate(state["orders"]):
        c = rp.customer_po_content(ctx, o)
        fname = f"customer_po_{o['po_no'].replace('/', '-')}.pdf"
        if ctx.has("scanned_doc") and not scanned_done and p in ("A", "B"):
            rs.scan_pdf(c, ctx.out / fname, ctx.rng, note=ctx.rng.choice(["pls confirm ex-fty date", "check price w/ costing", "rec'd by email + hard copy"]))
            scanned_done = True
            ctx.applied_defect("scanned_doc", fname, "customer PO is a scanned image (rotation, blur, stamp, handwritten note)")
        else:
            rp.draw_pdf(ctx.out / fname, c, state["layouts"]["customer_po"])
        if c["missing_due"]:
            ctx.applied_defect("missing_critical", fname, "delivery / ex-factory date shown as 'TBC'")
        ctx.register("customer_po", fname, "customer_po", c["extract"], c["extract"]["lines"], many=True)
        o["_file"] = fname
    # sales order confirmation (conflict scenario)
    for cf in state["conflicts"]:
        if cf["kind"] == "qty_mismatch":
            o = next(x for x in state["orders"] if x["po_no"] == cf["po_no"])
            c = rp.sales_confirmation_content(ctx, o, cf["confirmation_qty"])
            fname = f"sales_order_confirmation_{o['po_no'].replace('/', '-')}.pdf"
            rp.draw_pdf(ctx.out / fname, c, 1)
            ctx.register("sales_confirmation", fname, "sales_order_confirmation", c["extract"])
    rx.bom(ctx)
    rx.routing(ctx)
    rx.stock_report(ctx)
    rx.capacity(ctx)
    pr = rx.production_report(ctx)
    if pr is None:  # C, scanned paper log
        yday = add_wd(ctx.start, -1)
        rows = [[w["wc"], master["items"][w["item"]]["name"], f"B{yday.strftime('%y%m%d')}-{i + 1}", w["wo"], f"{w['done_qty']:,}",
                 str(ctx.rng.randint(20, 90)), f"{w['remaining_qty']:,}", ctx.rng.choice(["", "film joint", "sealer temp adj."])]
                for i, w in enumerate(state["wip"])]
        fname = f"daily_packing_log_{yday.isoformat()}.jpg"
        rs.packing_log_photo(ctx, rows, ctx.out / fname, ctx.date_str(yday, "dmy"))
        ctx.register("production_report", fname, "production_wip_report", {"date": yday.isoformat()},
                     [{"order": w["wo"], "item": w["item"], "work_centre": w["wc"], "remaining_qty": w["remaining_qty"],
                       "load_minutes": w["minutes"]} for w in state["wip"]])
        ctx.applied_defect("scanned_doc", fname, "paper packing log, photographed")
    rx.maintenance_log(ctx)
    # supplier POs (ours, open)
    for po in state["open_pos"]:
        c = rp.supplier_po_content(ctx, po)
        fname = f"purchase_order_{po['po_no'].replace('/', '-')}.pdf"
        rp.draw_pdf(ctx.out / fname, c, 1)
        ctx.register("supplier_po", fname, "supplier_purchase_order", c["extract"], many=True)
        po["_file"] = fname
    # supplier invoice / delivery note for a recent receipt
    cf = next((c for c in state["conflicts"] if c["kind"] == "receipt_mismatch"), None)
    if cf:
        lot = next(l for l in state["stock"][cf["item"]]["lots"] if l["lot"] == cf["lot"])
        c = rp.supplier_invoice_content(ctx, cf["item"], lot, cf["delivery_note_qty"], delivery_note=True)
        fname = f"delivery_note_{c['extract']['document_no'].replace('/', '-')}.pdf"
        rp.draw_pdf(ctx.out / fname, c, state["layouts"]["supplier_invoice"])
        ctx.register("delivery_note", fname, "delivery_note", c["extract"])
    else:
        needed = [r["item_code"] for r in plan["material_requirements"]]
        cands = [(l["received"], code, l) for code in needed for l in state["stock"][code]["lots"] if l["status"] == "OK"]
        _, code, lot = max(cands, key=lambda x: (x[0], x[1]))
        c = rp.supplier_invoice_content(ctx, code, lot, lot["qty"])
        fname = f"supplier_invoice_{c['extract']['document_no'].replace('/', '-')}.pdf"
        if ctx.has("scanned_doc") and not scanned_done:
            rs.scan_pdf(c, ctx.out / fname, ctx.rng, note="GRN done - pay 30d")
            scanned_done = True
            ctx.applied_defect("scanned_doc", fname, "supplier tax invoice is a scanned image")
        else:
            rp.draw_pdf(ctx.out / fname, c, state["layouts"]["supplier_invoice"])
        if ctx.has("mixed_currency"):
            ctx.applied_defect("mixed_currency", fname, "invoice priced in USD while the rest of the bundle is in LKR")
        ctx.register("supplier_invoice", fname, "supplier_tax_invoice", c["extract"])
    # supplier email (delay)
    for ev in state["events"]:
        if ev["type"] == "supplier_delay":
            po = next(x for x in state["open_pos"] if x["po_no"] == ev["po_no"])
            su = master["suppliers"][po["supplier_id"]]
            it = master["items"][ev["item"]]
            sent = add_wd(ctx.start, -1)
            body = (f"From: dispatch@{su['name'].split()[0].lower()}.example\nTo: {master['company']['email']}\n"
                    f"Date: {sent.strftime('%a, %d %b %Y')} 16:42 +0530\nSubject: RE: PO {po['po_no']} - revised delivery date\n\n"
                    f"Dear Sir/Madam,\n\nWe regret to inform you that delivery of your PO {po['po_no']} "
                    f"({it['name']}, {po['lines'][0]['qty']:,.0f} {it['uom']}) will be delayed due to {ev['reason']}.\n"
                    f"The revised delivery date is {date.fromisoformat(ev['new_eta']).strftime('%d/%m/%Y')} (earlier confirmed "
                    f"{date.fromisoformat(ev['old_eta']).strftime('%d/%m/%Y')}).\n\n"
                    "We apologise for the inconvenience. Please let us know if a part shipment would help.\n\n"
                    f"Best regards,\nDispatch Coordinator\n{su['name']}\n")
            fname = f"supplier_email_{po['po_no'].replace('/', '-')}.txt"
            (ctx.out / fname).write_text(body)
            ctx.register("supplier_email", fname, "supplier_email", {"po_number": po["po_no"], "item_code": ev["item"],
                                                                      "old_eta": ev["old_eta"], "new_eta": ev["new_eta"]})
    # factory policy
    rp.policy_pdf(ctx, ctx.out / "factory_policy_working_hours.pdf")
    ctx.register("policy", "factory_policy_working_hours.pdf", "factory_policy", state["policy"])
    return ctx


def expected_flags(state, master, plan, ctx):
    f = ctx.files
    flags = []

    def add(t, sev, msg, sources, needs_human, required=True, resolution=None):
        flags.append({"flag_id": f"F{len(flags) + 1}", "type": t, "severity": sev, "message": msg, "sources": sources,
                      "needs_human": needs_human, "resolution": resolution, "required": required})

    po_file = {o["po_no"]: o["_file"] for o in state["orders"]}
    for s in plan["shortages"]:
        it = master["items"][s["item_code"]]
        srcs = [src(f["stock_report"], "on_hand", None, ctx.locs.get((f["stock_report"], s["item_code"]))),
                src(f["bom"], "qty_per_unit", None, ctx.locs.get((f["bom"], s["item_code"])))]
        if s["cause"] == "supplier_delay":
            ev = next(e for e in state["events"] if e["type"] == "supplier_delay")
            po = next(x for x in state["open_pos"] if x["po_no"] == ev["po_no"])
            srcs = [src(f["supplier_email"], "revised_delivery_date", ev["new_eta"]), src(po["_file"], "expected_arrival", ev["old_eta"])] + srcs
            add("supplier_delay", "high", f"Open PO {ev['po_no']} for {it['name']} slips from {ev['old_eta']} to {ev['new_eta']}; "
                f"{s['shortfall_qty']} {s['uom']} is not available when {', '.join(s['affected_work_orders'])} needs it.", srcs, True,
                resolution="Wait for the delayed PO or approve the suggested expedited PO to the alternate supplier.")
        else:
            add("shortage", "high", f"{it['name']} is {s['shortfall_qty']} {s['uom']} short for {', '.join(s['affected_work_orders'])}.",
                srcs, False, resolution="Suggested PO raised; affected work order starts after the material is usable.")
    for d in state["downtime"]:
        add("machine_down", "high", f"{d['wc']} ({d['machine']}) is down until {d['end']} (expected back {d['expected_return']}).",
            [src(f["maintenance_log"], "status", "Open", ctx.locs.get((f["maintenance_log"], d["wc"]))),
             src(f["capacity"], "status", "DOWN")], False, resolution="Operations moved to an alternate work centre or after the repair.")
    for w in plan["work_orders"]:
        if w["is_late"]:
            add("due_date_risk", "high" if w["days_late"] > 2 else "medium",
                f"{w['wo_id']} ({w['customer_po']}) finishes {w['finish_date']}, {w['days_late']} working day(s) after the due date {w['due_date']}.",
                [src(po_file[w["customer_po"]], "delivery_date", w["due_date"], page=1)], True)
    if state["scenario"] == "S7":
        add("capacity_overload", "high", "Three orders compete for the same sewing capacity; not all can meet their due dates even with policy overtime.",
            [src(f["capacity"], "units"), src(f["production_report"], "remaining_qty")] +
            [src(po_file[o["po_no"]], "delivery_date", o["lines"][0]["due_date"], page=1) for o in state["orders"]], True)
    for cf in state["conflicts"]:
        if cf["kind"] == "qty_mismatch":
            add("conflict", "high", f"Customer PO {cf['po_no']} says {cf['customer_po_qty']:,} pcs but the sales order confirmation says "
                f"{cf['confirmation_qty']:,}.", [src(po_file[cf["po_no"]], "qty", cf["customer_po_qty"], page=1),
                                                   src(f["sales_confirmation"], "qty", cf["confirmation_qty"], page=1)], True,
                resolution=cf["expected_resolution"])
        else:
            add("conflict", "high", f"Stock report shows {cf['stock_report_qty']:,.2f} kg in lot {cf['lot']} but the delivery note says "
                f"{cf['delivery_note_qty']:,.2f} kg were delivered.", [src(f["stock_report"], "closing_quantity", cf["stock_report_qty"]),
                                                                        src(f["delivery_note"], "qty_delivered", cf["delivery_note_qty"], page=1)],
                True, resolution=cf["expected_resolution"])
    if state.get("ot_proposal"):
        op = state["ot_proposal"]
        add("compliance_breach", "high", f"The OT plan for {op['wc']} needs {op['weekly_total_hours']} h/week and Sunday work; the policy limit is "
            f"{state['policy']['max_total_hours_week']} h with one day off in seven.",
            [src(f["capacity"], "OT plan / weekly total hours", op["weekly_total_hours"]),
             src(f["policy"], "max_total_hours_week", state["policy"]["max_total_hours_week"], page=1)], True,
            resolution="Plan within policy overtime and report the later finish date.")
    if state.get("missing_due_po"):
        add("missing_field", "high", f"Customer PO {state['missing_due_po']} has no delivery date ('TBC').",
            [src(po_file[state["missing_due_po"]], "delivery_date", "TBC", page=1)], True)
    if plan["ot_work_centres"]:
        add("needs_approval", "low", f"Plan uses overtime within policy (max 2 h/day) on {', '.join(plan['ot_work_centres'])}.",
            [src(f["policy"], "max_ot_hours_day", 2, page=1)], True, required=False)
    return flags


def answer_key(state, master, plan, flags):
    wos = []
    for w in plan["work_orders"]:
        variant = {"colour": w["colour"], "sizes": w["sizes"]} if w.get("colour") else None
        wos.append({"wo_id": w["wo_id"], "item_code": w["item_code"], "qty": w["qty"], "uom": w["uom"], "customer_po": w["customer_po"],
                    "variant": variant, "bom_version": master["boms"][w["item_code"]]["version"], "priority": w["priority"],
                    "due_date": w["due_date"], "planned_start": w["planned_start"], "planned_finish": w["planned_finish"],
                    "is_late": w["is_late"], "days_late": w["days_late"],
                    "operations": [{k: o[k] for k in ("seq", "operation", "machine_id", "start", "end", "qty", "uses_overtime")} for o in w["operations"]]})
    return {
        "schema_version": "1.0", "bundle_id": state["bundle_id"], "system_id": "answer_key",
        "created_at": datetime(2026, 11, 21, 12, 0).isoformat() + "+05:30", "planning_start": state["planning_start"],
        "work_orders": wos, "material_requirements": plan["material_requirements"], "shortages": plan["shortages"],
        "suggested_pos": plan["suggested_pos"], "flags": flags,
        "assumptions": ["Solver rules: see data/generator/solver.py docstring.",
                        "One 8 h shift Mon-Sat; overtime max 2 h/day only where it reduces lateness.",
                        "Goods received on day D are usable from the next working day.",
                        "Quality-hold stock is not available."],
        "rationale": "Reference plan computed by solver.py (EDD sequencing, finite capacity, BoM netting). Several valid schedules exist; "
                     "score schedules on feasibility and lateness, not on exact times.",
        "checker": None,
        "scoring_notes": {"qty": "exact after unit normalisation (kg/m to 0.01, countable items to whole units)",
                          "dates": "within +-1 working day", "schedule": "feasibility: no double-booked work centre, no op before material is usable, no op on a down work centre",
                          "flags": "required=true flags must be found; required=false flags are optional and not penalised",
                          "item_aliases": "names and buyer article numbers map to item codes via extraction_key.json"},
        "ot_work_centres": plan["ot_work_centres"],
    }


def build(entry, root=BUNDLES, clean=True):
    m = MASTERS[entry["profile"]]
    state = build_state(entry["profile"], entry["scenario"], entry["mess"], entry["seed"], entry.get("orders"), entry["bundle_id"])
    plan = solve(state, m)
    bdir = Path(root) / entry["bundle_id"]
    if clean and bdir.exists():
        shutil.rmtree(bdir)
    ctx = render_bundle(state, m, plan, bdir / "inputs")
    flags = expected_flags(state, m, plan, ctx)
    key = answer_key(state, m, plan, flags)
    files = []
    for fp in sorted((bdir / "inputs").iterdir()):
        files.append({"name": fp.name, "type": fp.suffix.lstrip("."), "size_bytes": fp.stat().st_size,
                      "pages": 1 if fp.suffix == ".pdf" else None})
    manifest = {"bundle_id": entry["bundle_id"], "set": entry.get("set", "custom"), "profile": entry["profile"],
                "profile_name": m["profile_name"], "scenario": entry["scenario"], "scenario_name": SCENARIOS[entry["scenario"]],
                "mess_level": entry["mess"], "seed": entry["seed"], "n_documents": len(files),
                "n_orders": len(state["orders"]), "has_answer_key": True, "human_baseline": entry["bundle_id"] in HUMAN,
                "files": files, "defects_selected": state["defects"], "defects_applied": ctx.applied,
                "planted_conflicts": state["conflicts"], "scenario_events": state["events"],
                "generator_version": GENERATOR_VERSION, "planning_start": state["planning_start"]}
    clean_state = json.loads(json.dumps(state, default=str))
    for o in clean_state["orders"]:
        o.pop("_file", None)
    for p in clean_state["open_pos"]:
        p.pop("_file", None)
    (bdir / "state.json").write_text(json.dumps(clean_state, indent=2))
    (bdir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (bdir / "answer_key.json").write_text(json.dumps(key, indent=2))
    (bdir / "extraction_key.json").write_text(json.dumps({"bundle_id": entry["bundle_id"], "documents": ctx.extract}, indent=2, default=str))
    return manifest, key


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="build the full catalogue (test, dev, scale)")
    ap.add_argument("--set", choices=["test", "dev", "scale"], help="build only one set from the catalogue")
    ap.add_argument("--profile", choices=list(MASTERS))
    ap.add_argument("--scenario", choices=list(SCENARIOS))
    ap.add_argument("--mess", choices=["clean", "typical", "messy"])
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--orders", type=int, default=None, help="number of customer orders (scalability bundles)")
    ap.add_argument("--out", default=str(BUNDLES))
    a = ap.parse_args(argv)
    if a.all or a.set:
        entries = [e for e in catalogue() if a.all or e["set"] == a.set]
    else:
        if not (a.profile and a.scenario and a.mess):
            ap.error("give --all/--set, or --profile, --scenario and --mess")
        bid = f"{a.profile}_{a.scenario}_{a.mess}_seed{a.seed}" + (f"_o{a.orders}" if a.orders else "")
        entries = [{"bundle_id": bid, "set": "custom", "profile": a.profile, "scenario": a.scenario, "mess": a.mess,
                    "seed": a.seed, "orders": a.orders}]
    index = []
    for e in entries:
        man, key = build(e, a.out)
        late = sum(1 for w in key["work_orders"] if w["is_late"])
        print(f"{e['bundle_id']:<28} {man['n_documents']:>2} docs  {man['n_orders']} orders  "
              f"{len(key['shortages'])} shortages  {late} late  {len(key['flags'])} flags")
        index.append({k: man[k] for k in ("bundle_id", "set", "profile", "scenario", "scenario_name", "mess_level", "n_documents",
                                          "n_orders", "human_baseline")})
    if a.all:
        (Path(a.out) / "index.json").write_text(json.dumps(index, indent=2))


if __name__ == "__main__":
    main()
