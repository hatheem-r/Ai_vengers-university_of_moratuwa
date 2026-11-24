"""Excel renderers. Each mirrors the export layout of the system the profile 'runs':
A = Odoo exports + hand-built apparel sheets (tech pack BoM, operation bulletin, hourly report),
B = SAP Business One exports, C = TallyPrime reports. Defects are applied here and logged."""
from __future__ import annotations

import io
import re
import zipfile
from datetime import date, datetime, timedelta

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .common import HOLIDAYS, REG_MIN, add_wd, working_days
from .context import UNIT_VARIANTS
from .masters import explode, routing_for

BOLD = Font(bold=True)
HFILL = PatternFill("solid", fgColor="D9E1F2")
TFILL = PatternFill("solid", fgColor="F2F2F2")
THIN = Side(style="thin", color="999999")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


FIXED_TS = datetime(2026, 11, 21, 9, 0, 0)


def save(wb, path):
    """Save with fixed document timestamps so the same seed gives byte-identical files."""
    wb.properties.created = FIXED_TS
    wb.properties.modified = FIXED_TS
    wb.properties.creator = "generator"
    buf = io.BytesIO()
    wb.save(buf)
    zin = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            zi = zipfile.ZipInfo(info.filename, date_time=(2026, 11, 21, 9, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            data = zin.read(info.filename)
            if info.filename == "docProps/core.xml":  # openpyxl stamps 'modified' with the save time
                data = re.sub(rb"(<dcterms:modified[^>]*>)[^<]*", rb"\g<1>2026-11-21T09:00:00Z", data)
            zout.writestr(zi, data)


def title_block(ws, lines, ncols):
    for i, t in enumerate(lines, 1):
        ws.cell(row=i, column=1, value=t).font = Font(bold=(i == 1), size=13 if i == 1 else 10)
        ws.merge_cells(start_row=i, start_column=1, end_row=i, end_column=max(ncols, 2))
        ws.cell(row=i, column=1).alignment = Alignment(horizontal="center")
    return len(lines) + 2  # one blank row before the header


def header(ws, row, cols):
    for j, h in enumerate(cols, 1):
        c = ws.cell(row=row, column=j, value=h)
        c.font, c.fill, c.border = BOLD, HFILL, BOX
        c.alignment = Alignment(wrap_text=True, vertical="center")
    return row + 1


def put(ws, row, vals, bold=False, fill=None, fmt=None):
    for j, v in enumerate(vals, 1):
        c = ws.cell(row=row, column=j, value=v)
        c.border = BOX
        if bold:
            c.font = BOLD
        if fill:
            c.fill = fill
        if isinstance(v, date):
            c.number_format = fmt or "yyyy-mm-dd"
        elif isinstance(v, float):
            c.number_format = "#,##0.00"
    return row + 1


def widths(ws, w):
    for j, x in enumerate(w, 1):
        ws.column_dimensions[get_column_letter(j)].width = x


def dval(ctx, d, doc):
    """A date as the document would show it. dmy_dates defect: text DD/MM/YYYY in this doc."""
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    if ctx.has("dmy_dates") and doc in ctx.dmy_docs:
        return d.strftime("%d/%m/%Y")
    if ctx.p == "C":
        return ctx.date_str(d, "tally")
    return d


def company_lines(ctx, report, extra=None):
    c = ctx.company()
    return [c["name"], ", ".join(c["address"]), report] + ([extra] if extra else [])


# --------------------------------------------------------------------- stock
def stock_report(ctx):
    st, m, p = ctx.state, ctx.m, ctx.p
    fname = {"A": "stock_on_hand_odoo.xlsx", "B": "inventory_status_report.xlsx", "C": "godown_summary_tally.xlsx"}[p]
    asof = add_wd(ctx.start, -1)
    variants = UNIT_VARIANTS[p] if ctx.has("unit_variant") else {}
    if variants:
        ctx.applied_defect("unit_variant", fname, {k: {"shown_uom": v[0], "base_per_shown": v[1]} for k, v in variants.items()})
    conflict = next((c for c in st["conflicts"] if c["kind"] == "receipt_mismatch"), None)
    rows = []  # (group, values, item_code)
    ordered = {}
    for po in st["open_pos"]:
        for ln in po["lines"]:
            ordered[ln["item"]] = ordered.get(ln["item"], 0) + ln["qty"]
    lines_x = []
    for code, s in st["stock"].items():
        it = m["items"][code]
        shown_u, f = variants.get(code, (it["uom"], 1))
        if p == "A":
            for k, lot in enumerate(s["lots"]):
                res = s["reserved"] if k == 0 else 0.0
                rows.append((lot["location"], [lot["location"], f"[{code}] {it['name']}", code, lot["lot"],
                                               round(lot["qty"] / f, 2), round(res / f, 2), shown_u], code))
                lines_x.append({"item_code": code, "location": lot["location"], "lot": lot["lot"], "on_hand": lot["qty"],
                                "reserved": res, "uom": it["uom"], "shown_uom": shown_u, "status": lot["status"]})
        elif p == "B":
            byw = {}
            for lot in s["lots"]:
                byw[lot["location"]] = byw.get(lot["location"], 0) + lot["qty"]
            for k, (wh, q) in enumerate(sorted(byw.items())):
                com = s["reserved"] if k == 0 else 0.0
                ordq = ordered.get(code, 0) if k == 0 else 0.0
                rows.append((wh, [code, it["name"], wh, round(q / f, 2), round(com / f, 2), round(ordq / f, 2),
                                  round((q - com + ordq) / f, 2), shown_u], code))
                lines_x.append({"item_code": code, "warehouse": wh, "in_stock": q, "committed": com, "ordered": ordq,
                                "uom": it["uom"], "shown_uom": shown_u})
        else:
            for lot in s["lots"]:
                q = lot["qty"]
                if conflict and conflict["lot"] == lot["lot"]:
                    q = conflict["stock_report_qty"]
                rows.append((lot["location"], [it["name"], lot["lot"], ctx.date_str(lot["expiry"], "tally"),
                                               f"{q / f:,.2f} {shown_u}", f"{it['price'] * f:,.2f}/{shown_u}",
                                               round(q * it["price"], 2)], code))
                lines_x.append({"item_code": code, "godown": lot["location"], "batch": lot["lot"], "qty": q, "uom": it["uom"],
                                "shown_uom": shown_u, "expiry": lot["expiry"], "status": lot["status"]})
    if p == "A":
        cols = ["Location", "Product", "Internal Reference", "Lot/Serial Number", "On Hand Quantity", "Reserved Quantity", "Unit of Measure"]
    elif p == "B":
        cols = ["Item No.", "Item Description", "Warehouse", "In Stock", "Committed", "Ordered", "Available", "UoM"]
    else:
        cols = ["Particulars", "Batch", "Expiry", "Closing Quantity", "Rate", "Value"]
    # defects on columns
    drop = None
    if ctx.has("names_not_codes") and p in ("A", "B"):
        drop = 2 if p == "A" else 0
        ctx.applied_defect("names_not_codes", fname, "item code column removed; only names shown")
    if ctx.has("column_renamed"):
        ren = {"A": {"On Hand Quantity": "Qty", "Reserved Quantity": "Allocated"}, "B": {"In Stock": "Qty", "Committed": "Booked"},
               "C": {"Closing Quantity": "Qty", "Particulars": "Item"}}[p]
        cols = [ren.get(c, c) for c in cols]
        ctx.applied_defect("column_renamed", fname, ren)
    if p == "A":
        rows = [(g, [v[1].split("] ", 1)[1] if drop is not None and j == 1 else x for j, x in enumerate(v)], c) for g, v, c in rows]
    notes = ctx.has("notes_column")
    if notes:
        cols = cols + ["Remarks"]
        ctx.applied_defect("notes_column", fname, "free-text Remarks column")
    if drop is not None:
        cols = [c for j, c in enumerate(cols) if j != drop]
    rows.sort(key=lambda r: (r[0], r[2]))
    groups = {}
    for r in rows:
        groups.setdefault(r[0], []).append(r)
    two = ctx.has("two_sheets")
    if two:
        ctx.applied_defect("two_sheets", fname, "stock split into one sheet per store/warehouse")
    wb = Workbook()
    ws = wb.active
    ws.title = {"A": "Stock", "B": "Inventory Status", "C": "Godown Summary"}[p]
    sheets = {}
    blank_done = False
    remark_pool = ["count pending", "2 rolls shade variation, keep aside", "partly wet, check", "recount done",
                   "moved from receiving bay", "supplier label missing", ""]
    for gi, (g, rs) in enumerate(groups.items()):
        if two:
            ws = wb.active if gi == 0 else wb.create_sheet()
            ws.title = g.replace("/", "-")[:30]
        if ws.title not in sheets:
            r0 = 1
            if p == "C" or ctx.has("title_block"):
                extra = f"1-Apr-26 to {ctx.date_str(asof, 'tally')}" if p == "C" else f"As of {asof.isoformat()}"
                rep = {"A": "Inventory Report - Stock on hand", "B": "Inventory Status Report", "C": "Godown Summary"}[p]
                r0 = title_block(ws, company_lines(ctx, rep, extra), len(cols))
                if ctx.has("title_block") and p != "C" and "title_block" not in [a["defect"] for a in ctx.applied]:
                    ctx.applied_defect("title_block", fname, "report title rows above the header")
            sheets[ws.title] = header(ws, r0, cols)
            widths(ws, [28, 42, 18, 16, 16, 14, 14, 12, 26][:len(cols)] if p != "C" else [40, 12, 12, 20, 16, 16, 26])
        r = sheets[ws.title]
        if p == "C":
            r = put(ws, r, [g] + [None] * (len(cols) - 1), bold=True, fill=TFILL)
        gsum = 0.0
        for _, v, code in rs:
            v = list(v)
            if drop is not None:
                v = [x for j, x in enumerate(v) if j != drop]
            if notes:
                v.append(ctx.rng.choice(remark_pool) if ctx.rng.random() < 0.35 else None)
            if ctx.has("blank_noncritical") and not blank_done and p in ("A", "B") and len(rs) > 2:
                # blank the location / warehouse of one row (non-critical field)
                idx = 0 if p == "A" else 1 if drop == 0 else 2
                v[idx] = None
                blank_done = True
                ctx.applied_defect("blank_noncritical", fname, {"row": r, "column": cols[idx]})
            ctx.locs[(fname, code)] = ctx.locs.get((fname, code), r)
            r = put(ws, r, v)
            gsum += v[-2 if notes else -1] if p == "C" and isinstance(v[-2 if notes else -1], float) else 0
        if ctx.has("total_row_inside") or p == "C":
            if p == "C":
                r = put(ws, r, [f"Total {g}", None, None, None, None, round(gsum, 2)] + ([None] if notes else []), bold=True)
            else:
                qcol = cols.index("Qty") if "Qty" in cols else (4 if p == "A" else 3) - (1 if drop is not None and p == "A" and drop < 4 else 0)
                tot = ["" for _ in cols]
                tot[0] = f"Total {g}"
                r = put(ws, r, tot, bold=True)
                if "total_row_inside" not in [a["defect"] for a in ctx.applied]:
                    ctx.applied_defect("total_row_inside", fname, "a 'Total <location>' row after each location group")
        sheets[ws.title] = r
    save(wb, ctx.out / fname)
    ctx.register("stock_report", fname, "stock_report", {"as_of": asof.isoformat(), "system": ctx.m["system"]}, lines_x)
    return fname


# ----------------------------------------------------------------------- BoM
PLACEMENT = {"Fabric": "Body", "Trims": "As per tech pack", "Thread": "All seams", "Labels": "Neck back / side seam", "Packaging": "Packing"}


def bom(ctx):
    m, p, st = ctx.m, ctx.p, ctx.state
    items = sorted({ln["item_code"] for o in st["orders"] for ln in o["lines"]})
    fname = {"A": "bom_techpack.xlsx", "B": "bom_sapb1_export.xlsx", "C": "bom_tally.xlsx"}[p]
    wb = Workbook()
    wb.remove(wb.active)
    xl = []
    ren = ctx.has("column_renamed")
    if p == "A":
        for code in items:
            it = m["items"][code]
            b = m["boms"][code]
            ws = wb.create_sheet(code[:30])
            top = [("Style No.", it["style"]), ("Description", it["name"]), ("Colour / Pantone", f"{it['colour']} / {it['pantone']}"),
                   ("BoM version", b["version"]), ("Base quantity", "1 pc"), ("Size range", "S - XXL")]
            ws.cell(row=1, column=1, value="TECH PACK - BILL OF MATERIALS").font = Font(bold=True, size=13)
            for i, (k, v) in enumerate(top, 3):
                ws.cell(row=i, column=1, value=k).font = BOLD
                ws.cell(row=i, column=2, value=v)
            cols = ["Category", "Item code", "Component", "Colour", "Placement", "Consumption / garment", "UoM", "Wastage %", "Supplier"]
            if ren:
                cols[5] = "Cons."
            if ctx.has("names_not_codes"):
                cols.pop(1)
            r = header(ws, 10, cols)
            for comp, per, w, op in b["lines"]:
                ci = m["items"][comp]
                row = [ci["cat"], comp, ci["name"], it["colour"] if ci["cat"] in ("Fabric", "Trims") else "-",
                       PLACEMENT[ci["cat"]], round(per, 4), ci["uom"], w, m["suppliers"][ci["supplier"]]["name"]]
                if ctx.has("names_not_codes"):
                    row.pop(1)
                ctx.locs[(fname, comp)] = ctx.locs.get((fname, comp), r)
                r = put(ws, r, row)
                xl.append({"parent": code, "component": comp, "qty_per": per, "uom": ci["uom"], "wastage_pct": w, "consumed_in": op})
            widths(ws, [14, 16, 40, 12, 20, 14, 8, 10, 34])
    elif p == "B":
        ws = wb.create_sheet("Bill of Materials")
        cols = ["Product No.", "Product Description", "BOM Type", "Quantity", "Warehouse", "#", "Type", "No.", "Description",
                "Quantity per", "UoM", "Warehouse (line)", "Issue Method", "Scrap % (UDF)"]
        if ren:
            cols[9] = "Qty"
        r = header(ws, 1, cols)
        todo, seen = list(items), set()
        while todo:
            code = todo.pop(0)
            if code in seen:
                continue
            seen.add(code)
            b = m["boms"][code]
            for i, (comp, per, w, op) in enumerate(b["lines"], 1):
                ci = m["items"][comp]
                wh = "05 - WIP" if ci["kind"] == "sa" else m["warehouses"][ci["cat"]]
                r = put(ws, r, [code, m["items"][code]["name"], "Production", 1, "06 - Finished Goods" if m["items"][code]["kind"] == "fg" else "05 - WIP",
                                i, "Item", comp, ci["name"], per, ci["uom"], wh, "Backflush" if ci.get("cat") in ("Hardware", "Coating") else "Manual", w])
                xl.append({"parent": code, "component": comp, "qty_per": per, "uom": ci["uom"], "wastage_pct": w})
                if ci["kind"] == "sa":
                    todo.append(comp)
        widths(ws, [14, 34, 12, 9, 18, 5, 7, 14, 32, 11, 7, 16, 12, 12])
    else:
        ws = wb.create_sheet("BOM")
        r = 1
        for code in items:
            it = m["items"][code]
            b = m["boms"][code]
            nm = it["name"] if True else code
            r = title_block(ws, [ctx.company()["name"], f"Bill of Materials : {nm}"], 4) if r == 1 else r
            ws.cell(row=r, column=1, value=f"Stock Item: {nm}").font = BOLD
            ws.cell(row=r + 1, column=1, value=f"Name of BOM: {b['version']}")
            ws.cell(row=r + 2, column=1, value="Unit of manufacture: 1,000 pcs")
            ws.cell(row=r + 3, column=1, value="Note: component quantities include process loss")
            r = header(ws, r + 5, ["Item", "Type of Item", "Godown", "Quantity" if not ren else "Qty"])
            for comp, per, w, op in b["lines"]:
                ci = m["items"][comp]
                q = per * (1 + w / 100) * 1000
                r = put(ws, r, [ci["name"], "Component", m["godowns"][ci["cat"]], f"{q:,.2f} {ci['uom']}"])
                xl.append({"parent": code, "component": comp, "qty_per_1000_incl_loss": round(q, 4), "uom": ci["uom"],
                           "qty_per": per, "wastage_pct": w})
            r += 2
        widths(ws, [40, 14, 24, 18])
    if ren:
        ctx.applied_defect("column_renamed", fname, "quantity column header renamed")
    if ctx.has("names_not_codes") and p == "A":
        ctx.applied_defect("names_not_codes", fname, "item code column removed from tech pack BoM")
    save(wb, ctx.out / fname)
    ctx.register("bom", fname, "bill_of_materials", {"products": items}, xl)
    return fname


# ------------------------------------------------------------------- routing
def routing(ctx):
    m, p, st = ctx.m, ctx.p, ctx.state
    items = sorted({ln["item_code"] for o in st["orders"] for ln in o["lines"]})
    fname = {"A": "routing_odoo_operations.xlsx", "B": "routing_sapb1_stages.xlsx", "C": "process_sheet.xlsx"}[p]
    wb = Workbook()
    ws = wb.active
    xl = []
    if p == "A":
        ws.title = "Operations"
        r = header(ws, 1, ["Bill of Material", "Sequence", "Operation", "Work Center", "Duration Computation", "Default Duration (min/unit)", "Setup time (min)"])
        styles = sorted({m["items"][c]["style"] for c in items})
        for s in styles:
            for seq, op, wc, std, setup in m["routings"][s]:
                r = put(ws, r, [s, seq, op, f"{wc} {m['work_centres'][wc]['name']}", "Set duration manually", std, setup])
                xl.append({"style": s, "seq": seq, "operation": op, "work_centre": wc, "std_min_per_unit": std, "setup_min": setup})
        widths(ws, [16, 10, 22, 30, 22, 22, 16])
    elif p == "B":
        ws.title = "Routing"
        r = header(ws, 1, ["Product No.", "Stage", "Stage Description", "Resource", "Resource Name", "Run Time / Unit (min)", "Setup Time (min)"])
        for c in items:
            for seq, op, wc, std, setup in m["routings"][c]:
                r = put(ws, r, [c, seq, op, wc, m["work_centres"][wc]["name"], std, setup])
                xl.append({"item": c, "seq": seq, "operation": op, "work_centre": wc, "std_min_per_unit": std, "setup_min": setup})
        widths(ws, [14, 8, 20, 10, 34, 20, 16])
    else:
        ws.title = "Process"
        r0 = title_block(ws, company_lines(ctx, "Process sheet - packing lines"), 6)
        r = header(ws, r0, ["Product", "Step", "Process", "Machine", "Rated speed (units/min)", "Changeover / setup (min)"])
        for c in items:
            for seq, op, wc, std, setup in m["routings"][c]:
                r = put(ws, r, [m["items"][c]["name"], seq // 10, op, wc, round(1 / std, 2), setup])
                xl.append({"item": c, "seq": seq, "operation": op, "work_centre": wc, "std_min_per_unit": std,
                           "rated_units_per_min": round(1 / std, 2), "setup_min": setup})
        widths(ws, [40, 6, 22, 10, 22, 22])
    save(wb, ctx.out / fname)
    ctx.register("routing", fname, "routing", {}, xl)
    if p == "A":
        operation_bulletin(ctx, items)
    return fname


def operation_bulletin(ctx, items):
    m = ctx.m
    fname = "operation_bulletin.xlsx"
    wb = Workbook()
    wb.remove(wb.active)
    xl = []
    for s in sorted({m["items"][c]["style"] for c in items}):
        ws = wb.create_sheet(s)
        rt = [r for r in m["routings"][s] if r[1] == "Sewing"][0]
        wc = m["work_centres"][rt[2]]
        total = round(sum(x[2] for x in m["ob"][s]), 2)
        tgt_day = int(REG_MIN * wc["units"] * wc["eff"] / total)
        ws.cell(row=1, column=1, value="OPERATION BULLETIN").font = Font(bold=True, size=13)
        info = [("Style #", s), ("Line", rt[2]), ("Total SMV", total), ("Operators", wc["units"]),
                ("Planned efficiency", f"{int(wc['eff'] * 100)}%"), ("Target / day (8 h)", tgt_day), ("Prepared by", "IE dept.")]
        for i, (k, v) in enumerate(info, 3):
            ws.cell(row=i, column=1, value=k).font = BOLD
            ws.cell(row=i, column=2, value=v)
        cols = ["#", "Operation", "Machine type", "SMV", f"Target/hr @100%", f"Target/hr @{int(wc['eff'] * 100)}%", "Machines required"]
        r = header(ws, 11, cols)
        for i, (op, mt, smv) in enumerate(m["ob"][s], 1):
            t100 = round(60 / smv)
            req = round(smv * tgt_day / (REG_MIN * wc["eff"]), 2)
            r = put(ws, r, [i, op, mt, smv, t100, round(t100 * wc["eff"]), req])
            xl.append({"style": s, "operation": op, "machine_type": mt, "smv": smv})
        put(ws, r, ["", "Total", "", total, "", "", None], bold=True)
        widths(ws, [5, 30, 14, 8, 15, 15, 17])
    save(wb, ctx.out / fname)
    ctx.register("operation_bulletin", fname, "operation_bulletin", {}, xl)


# -------------------------------------------------------- capacity and shifts
def capacity(ctx):
    m, st = ctx.m, ctx.state
    fname = "capacity_shift_plan.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Work centres"
    r0 = title_block(ws, company_lines(ctx, "Capacity and shift plan", f"Week starting {ctx.start.isoformat() if ctx.p == 'A' else ctx.date_str(ctx.start)}"), 10) if ctx.has("title_block") or ctx.p != "A" else 1
    unit_col = {"A": "Operators / machines", "B": "Stations / machines", "C": "Machines / packers"}[ctx.p]
    r = header(ws, r0, ["Work centre ID", "Name", "Type", unit_col, "Planned efficiency %", "Shift", "Regular hours / day",
                        "Working days", "OT allowed (h/day)", "Alternate for", "Status"])
    down_now = {d["wc"] for d in st["downtime"] if date.fromisoformat(d["start"]) <= ctx.start <= date.fromisoformat(d["end"])}
    xl = []
    for wc, w in m["work_centres"].items():
        alt_for = ", ".join(k for k, v in m["work_centres"].items() if wc in v["alts"]) or "-"
        status = "DOWN - see maintenance log" if wc in down_now else "Running"
        r = put(ws, r, [wc, w["name"], w["type"], w["units"], int(w["eff"] * 100), "Day 08:00-17:00 (lunch 12:30-13:30)",
                        8, "Mon-Sat", 2, alt_for, status])
        xl.append({"work_centre": wc, "units": w["units"], "efficiency": w["eff"], "regular_min_per_day": REG_MIN,
                   "ot_max_min_per_day": 120, "alternates": w["alts"], "status": status})
    widths(ws, [14, 36, 16, 12, 12, 30, 10, 12, 10, 18, 26])
    cal = wb.create_sheet("Calendar")
    r = header(cal, 1, ["Date", "Day", "Working day", "Remarks"])
    d = ctx.start
    for _ in range(42):
        wd = d.weekday() < 6 and d not in HOLIDAYS
        r = put(cal, r, [dval(ctx, d, fname), d.strftime("%a"), "Yes" if wd else "No",
                         HOLIDAYS.get(d, "Sunday" if d.weekday() == 6 else "")])
        d += timedelta(days=1)
    widths(cal, [14, 8, 12, 30])
    if st.get("ot_proposal"):
        op = st["ot_proposal"]
        ot = wb.create_sheet("OT plan")
        ot.cell(row=1, column=1, value=f"Overtime plan to meet ex-factory - prepared by {op['proposed_by']}").font = BOLD
        r = header(ot, 3, ["Date", "Day", "Work centre", "Regular hours", "OT hours", "Total hours", "Approved by"])
        d, days = ctx.start, 0
        while days < 7 * op["weeks"]:
            if d not in HOLIDAYS:
                if d.weekday() < 6:
                    r = put(ot, r, [dval(ctx, d, fname), d.strftime("%a"), op["wc"], 8, op["weekday_ot_hours"], 8 + op["weekday_ot_hours"], None])
                else:
                    r = put(ot, r, [dval(ctx, d, fname), d.strftime("%a"), op["wc"], 0, op["sunday_hours"], op["sunday_hours"], None])
            d += timedelta(days=1)
            days += 1
        ot.cell(row=r + 1, column=1, value=f"Weekly total per operator: {op['weekly_total_hours']} h")
        widths(ot, [14, 8, 14, 14, 10, 12, 16])
    save(wb, ctx.out / fname)
    ctx.register("capacity", fname, "capacity_shift_plan", {"ot_proposal": st.get("ot_proposal")}, xl)
    return fname


# --------------------------------------------------------- production report
def production_report(ctx):
    m, st, p = ctx.m, ctx.state, ctx.p
    yday = add_wd(ctx.start, -1)
    if p == "C" and ctx.has("scanned_doc"):
        return None  # rendered as a scanned paper log instead
    fname = {"A": f"hourly_production_report_{yday.isoformat()}.xlsx", "B": f"daily_production_report_{yday.isoformat()}.xlsx",
             "C": f"daily_packing_log_{yday.isoformat()}.xlsx"}[p]
    wb = Workbook()
    ws = wb.active
    xl = []
    notes = ctx.has("notes_column")
    if p == "A":
        ws.title = "Hourly report"
        r0 = title_block(ws, [ctx.company()["name"], "Hourly production report (sewing)", f"Date: {yday.strftime('%d/%m/%Y')}"], 18)
        hrs = ["8-9", "9-10", "10-11", "11-12", "12-1*", "1-2", "2-3", "3-4", "4-5"]
        r = header(ws, r0, ["Line", "Style", "Buyer", "MO", "Operators", "SAM", "Target/day", "Target/hr"] + hrs + ["Total", "Eff %"] + (["Remarks"] if notes else []))
        for w in st["wip"]:
            wc = m["work_centres"][w["wc"]]
            it = m["items"][w["item"]]
            sam = [x for x in routing_for(m, w["item"]) if x[2] == w["wc"]]
            sam = sam[0][3] if sam else routing_for(m, w["item"])[1][3]
            tday = int(REG_MIN * wc["units"] * wc["eff"] / sam)
            th = round(tday / 8)
            outs = [max(0, int(th * ctx.rng.uniform(0.75, 1.08))) if h != "12-1*" else int(th * 0.5) for h in hrs]
            broke = any(d["wc"] == w["wc"] and d["start"] == yday.isoformat() for d in st["downtime"])
            if broke:  # breakdown at 14:15 yesterday
                outs = outs[:6] + [int(outs[6] * 0.25), 0, 0]
            tot = sum(outs)
            r = put(ws, r, [w["wc"], it["style"], m["customers"][w["customer"]]["name"], w["wo"], wc["units"], sam, tday, th] + outs +
                    [tot, round(tot * sam / (wc["units"] * REG_MIN) * 100, 1)] + (["M/C breakdown 14:15" if broke else ctx.rng.choice(["2 absent", "needle change", "", "QC hold 1 hr"])] if notes else []))
        ws2 = wb.create_sheet("Open orders")
        r = header(ws2, 1, ["MO", "Style", "Item", "Line", "Order qty", "Cut", "Sewn", "Packed", "Balance to sew"])
        for w in st["wip"]:
            it = m["items"][w["item"]]
            oq = w["remaining_qty"] + w["done_qty"]
            r = put(ws2, r, [w["wo"], it["style"], w["item"], w["wc"], oq, oq, w["done_qty"], max(0, w["done_qty"] - 60), w["remaining_qty"]])
            xl.append({"order": w["wo"], "item": w["item"], "work_centre": w["wc"], "remaining_qty": w["remaining_qty"], "load_minutes": w["minutes"]})
        widths(ws2, [14, 14, 16, 8, 10, 8, 8, 8, 14])
    elif p == "B":
        ws.title = "Production"
        r0 = title_block(ws, company_lines(ctx, "Daily production report", f"Date: {ctx.date_str(yday, 'dmy_dot')}"), 9) if ctx.has("title_block") else 1
        r = header(ws, r0, ["Prod. Order", "Item No.", "Stage", "Resource", "Planned Qty", "Completed Qty", "Rejected Qty", "Remaining Qty", "Remaining time (min)"] + (["Remarks"] if notes else []))
        for w in st["wip"]:
            stage = [x for x in routing_for(m, w["item"]) if x[2] == w["wc"]]
            sname = stage[0][1] if stage else m["work_centres"][w["wc"]]["type"]
            rej = ctx.rng.randint(0, 6)
            r = put(ws, r, [w["wo"], w["item"], sname, w["wc"], w["remaining_qty"] + w["done_qty"], w["done_qty"], rej, w["remaining_qty"], round(w["minutes"])] +
                    ([ctx.rng.choice(["", "jig repaired", "waiting QC", "powder colour change"])] if notes else []))
            xl.append({"order": w["wo"], "item": w["item"], "work_centre": w["wc"], "remaining_qty": w["remaining_qty"], "load_minutes": w["minutes"]})
    else:
        ws.title = "Packing log"
        r0 = title_block(ws, company_lines(ctx, "Daily packing log", f"Date: {ctx.date_str(yday, 'tally')}"), 9)
        r = header(ws, r0, ["Line", "Product", "Batch No.", "Order ref", "Target (packs)", "Packed", "Rejects", "Pending of order", "Remarks"])
        for w in st["wip"]:
            r = put(ws, r, [w["wc"], m["items"][w["item"]]["name"], f"B{yday.strftime('%y%m%d')}-{ctx.rng.randint(1, 9)}", w["wo"],
                            int(REG_MIN / routing_for(m, w["item"])[1][3] * 0.8), w["done_qty"], ctx.rng.randint(20, 90), w["remaining_qty"],
                            ctx.rng.choice(["", "film joint issue", "sealer temp adjusted"])])
            xl.append({"order": w["wo"], "item": w["item"], "work_centre": w["wc"], "remaining_qty": w["remaining_qty"], "load_minutes": w["minutes"]})
    if notes and p in ("A", "B"):
        ctx.applied_defect("notes_column", fname, "free-text Remarks column")
    save(wb, ctx.out / fname)
    ctx.register("production_report", fname, "production_wip_report", {"date": yday.isoformat()}, xl)
    return fname


# ---------------------------------------------------------- maintenance log
def maintenance_log(ctx):
    m, st, p = ctx.m, ctx.state, ctx.p
    rows = []
    for d in st["maintenance_noise"]:
        rows.append({**d, "end_dt": None})
    for d in st["downtime"]:
        rows.append(d)
    fname = "maintenance_log.csv" if p == "C" else "maintenance_log.xlsx"
    cols = ["Date", "Machine ID", "Work centre", "Machine / part", "Breakdown start", "Breakdown end", "Downtime (h)", "Category",
            "Reason", "Action / remarks", "Expected return", "Status", "Reported by"]
    data, xl = [], []
    for d in rows:
        d0 = date.fromisoformat(d["start"])
        mid = d["machine"].split(" ")[0]
        if d["status"] == "Closed":
            data.append([ctx.date_str(d0, "dmy"), mid, d["wc"], d["machine"], "09:40", f"{9 + int(d['hours'])}:{int((d['hours'] % 1) * 60):02d}+0.40h",
                         d["hours"], "Breakdown", d["reason"], d["remarks"], "", "Closed", "Shift supervisor"])
            data[-1][5] = f"{9 + int(d['hours'] + 0.67):02d}:{int(((d['hours'] + 0.67) % 1) * 60):02d}"
        else:
            data.append([ctx.date_str(d0, "dmy"), mid, d["wc"], d["machine"], "14:15", "", "", "Breakdown", d["reason"], d["remarks"],
                         ctx.date_str(d["expected_return"], "dmy"), "Open - awaiting repair", "Line supervisor"])
        xl.append({"work_centre": d["wc"], "machine": d["machine"], "start": d["start"], "status": d["status"],
                   "unavailable_until_incl": d.get("end") if d["status"] != "Closed" else None,
                   "expected_return": d.get("expected_return")})
    data.sort(key=lambda x: x[0][6:] + x[0][3:5] + x[0][:2])
    if p == "C":
        import csv
        with open(ctx.out / fname, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(cols)
            w.writerows(data)
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = "Breakdown register"
        r = header(ws, 1, cols)
        for row in data:
            ctx.locs[(fname, row[2])] = r
            r = put(ws, r, row)
        widths(ws, [12, 10, 12, 40, 14, 14, 12, 12, 24, 44, 14, 22, 16])
        save(wb, ctx.out / fname)
    ctx.register("maintenance_log", fname, "maintenance_log", {}, xl)
    return fname
