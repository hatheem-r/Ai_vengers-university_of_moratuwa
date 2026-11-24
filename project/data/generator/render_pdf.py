"""PDF documents: customer POs, sales order confirmations, supplier POs, supplier tax
invoices / delivery notes and the factory policy sheet.

Every document is first built as a content dict (parties, key fields, table, notes) so the
same content can be drawn as a clean PDF (reportlab) or as a scanned image (render_scan)."""
from __future__ import annotations

from datetime import date, timedelta

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .common import add_wd, money
from .masters import explode

rl_config.invariant = 1  # reproducible PDFs (no timestamps / random ids)
SS = getSampleStyleSheet()
P = ParagraphStyle("p", parent=SS["Normal"], fontSize=8.5, leading=11)
PB = ParagraphStyle("pb", parent=P, fontName="Helvetica-Bold")
H = ParagraphStyle("h", parent=SS["Title"], fontSize=15, leading=18, spaceAfter=4)
SMALL = ParagraphStyle("s", parent=P, fontSize=7, leading=9, textColor=colors.HexColor("#444444"))

VAT = 0.18
USD_LKR = 300.0  # synthetic fixed rate used for LKR equivalents

ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve", "Thirteen",
        "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def words(n: int) -> str:
    def w(n):
        if n < 20:
            return ONES[n]
        if n < 100:
            return TENS[n // 10] + ("-" + ONES[n % 10] if n % 10 else "")
        if n < 1000:
            return ONES[n // 100] + " Hundred" + (" and " + w(n % 100) if n % 100 else "")
        for div, name in ((10 ** 9, "Billion"), (10 ** 6, "Million"), (1000, "Thousand")):
            if n >= div:
                return w(n // div) + f" {name}" + (" " + w(n % div) if n % div else "")
    return w(n) if n else "Zero"


def amount_words(x: float, cur: str) -> str:
    whole = int(x)
    cents = int(round((x - whole) * 100))
    unit = {"LKR": ("Rupees", "Cents"), "USD": ("US Dollars", "Cents")}[cur]
    return f"{unit[0]} {words(whole)}" + (f" and {unit[1]} {words(cents)}" if cents else "") + " Only"


# ---------------------------------------------------------------- content
def customer_po_content(ctx, order):
    m, st = ctx.m, ctx.state
    cu = m["customers"][order["customer_id"]]
    style = ctx.rng.choice(["long", "dmy", "iso"]) if ctx.p != "B" else "dmy"
    if ctx.has("dmy_dates"):
        style = "dmy"
    dd = lambda d: ctx.date_str(d, style)
    ln = order["lines"][0]
    it = m["items"][ln["item_code"]]
    missing = st.get("missing_due_po") == order["po_no"]
    deliv_label = {"A": "Ex-factory date", "B": "Delivery date", "C": "Shipment date"}[ctx.p]
    fields = [("PO number", order["po_no"]), ("PO date", dd(order["po_date"])), ("Currency", cu["currency"]),
              ("Incoterm", cu["incoterm"]), ("Payment terms", cu["payment"])]
    if not missing:
        fields.append((deliv_label, dd(ln["due_date"])))
    else:
        fields.append((deliv_label, "TBC"))
    price = ln["unit_price"] if cu["currency"] == "USD" or ctx.p == "B" else round(ln["unit_price"] * USD_LKR, 2)
    if ctx.p == "A":
        sizes = ln["sizes"]
        head = ["Style", "Description", "Colour"] + list(sizes) + ["Total pcs", f"Unit price ({cu['currency']})", "Amount"]
        rows = [[it["style"], it["name"].split(",")[0], ln["colour"]] + [f"{v:,}" for v in sizes.values()] +
                [f"{ln['qty']:,}", f"{price:.2f}", money(price * ln["qty"])]]
        notes = ["Packing: solid colour, solid size, 24 pcs per carton (polo) / 36 (tee) / 48 (leggings); each piece in individual polybag with hang tag.",
                 "Shipping marks as per buyer manual. Final inspection AQL 2.5 before ex-factory.",
                 "Any delay against the ex-factory date must be advised in writing immediately."]
    else:
        unit = "pcs"
        head = ["Line", "Item code", "Description", "Qty", "UoM", f"Unit price ({cu['currency']})", "Amount"]
        rows = [["1", ctx.buyer_code(ln["item_code"]), it["name"], f"{ln['qty']:,}", unit, money(price), money(price * ln["qty"])]]
        notes = ({"B": ["Deliver to our central warehouse, Monday to Friday 08:30-16:00. Quote our PO number on the delivery note and invoice.",
                        "Goods must be packed flat with corner protection."],
                  "C": ["Best-before date must have at least 18 months remaining at shipment.",
                        "Shipper cartons must carry batch number and best-before date.", "Quote our PO number on all documents."]})[ctx.p]
    total = price * ln["qty"]
    return {"kind": "customer_po", "title": "PURCHASE ORDER", "from": (cu["name"], cu["address"]),
            "to": ("Supplier", m["company"]["name"], m["company"]["address"]), "fields": fields, "table": (head, rows),
            "totals": [("Total", f"{cu['currency']} {money(total)}")], "notes": notes, "rush": order.get("rush"),
            "missing_due": missing, "sign": "Authorised signatory, Merchandising / Purchasing",
            "extract": {"po_number": order["po_no"], "po_date": order["po_date"], "customer": cu["name"],
                        "currency": cu["currency"], "delivery_date": None if missing else ln["due_date"],
                        "lines": [{"item_code": ln["item_code"], "buyer_ref": ctx.buyer_code(ln["item_code"]), "qty": ln["qty"],
                                   "uom": "pcs", "unit_price": price, "sizes": ln.get("sizes")}]}}


def sales_confirmation_content(ctx, order, conf_qty):
    m = ctx.m
    cu = m["customers"][order["customer_id"]]
    ln = order["lines"][0]
    it = m["items"][ln["item_code"]]
    so = f"S{ctx.rng.randint(10000, 19999):05d}" if ctx.p == "A" else str(ctx.rng.randint(2100, 2999))
    fields = [("Order", so if ctx.p == "A" else f"Sales Order {so}"), ("Order date", ctx.date_str(add_wd(date.fromisoformat(order["po_date"]), 2))),
              ("Customer reference", order["po_no"]), ("Salesperson", "Merchandising - Team 2" if ctx.p == "A" else "Sales desk"),
              ("Commitment date", ctx.date_str(ln["due_date"]))]
    head = ["Description", "Quantity", "Unit Price", "Taxes", "Amount"]
    rows = [[f"[{ln['item_code']}] {it['name']}", f"{conf_qty:,.2f} Units", f"{ln['unit_price']:.2f}", "0% Export",
             money(conf_qty * ln["unit_price"])]]
    return {"kind": "sales_confirmation", "title": "Sales Order" if ctx.p == "A" else "SALES ORDER CONFIRMATION",
            "from": (m["company"]["name"], m["company"]["address"]), "to": ("Customer", cu["name"], cu["address"]),
            "fields": fields, "table": (head, rows), "totals": [("Total", f"$ {money(conf_qty * ln['unit_price'])}")],
            "notes": ["Payment terms: " + cu["payment"]], "sign": None,
            "extract": {"order": so, "customer_reference": order["po_no"], "item_code": ln["item_code"], "qty": conf_qty,
                        "commitment_date": ln["due_date"]}}


def supplier_po_content(ctx, po):
    m = ctx.m
    su = m["suppliers"][po["supplier_id"]]
    ln = po["lines"][0]
    it = m["items"][ln["item"]]
    ds = {"A": "mdy", "B": "dmy_dot", "C": "tally"}[ctx.p]
    od = date.fromisoformat(po["order_date"])
    if ctx.p == "A":
        title = f"Purchase Order #{po['po_no']}"
        fields = [("Buyer", "Stores & Purchasing"), ("Order Deadline", ctx.date_str(od, ds)), ("Expected Arrival", ctx.date_str(po["eta_printed"], ds))]
        head = ["Description", "Taxes", "Date Req.", "Qty", "Unit Price", "Amount"]
        rows = [[f"[{ln['item']}] {it['name']}", "VAT 18%", ctx.date_str(po["eta_printed"], ds), f"{ln['qty']:,.2f} {it['uom']}", money(ln["price"]), money(ln["qty"] * ln["price"])]]
    elif ctx.p == "B":
        title = "PURCHASE ORDER"
        fields = [("No.", po["po_no"]), ("Posting Date", ctx.date_str(od, ds)), ("Delivery Date", ctx.date_str(po["eta_printed"], ds)), ("Vendor Ref. No.", "")]
        head = ["#", "Item No.", "Description", "Quantity", "UoM", "Price", "Total (LC)"]
        rows = [["1", ln["item"], it["name"], f"{ln['qty']:,.2f}", it["uom"], money(ln["price"]), money(ln["qty"] * ln["price"])]]
    else:
        title = "PURCHASE ORDER"
        fields = [("Order No.", po["po_no"]), ("Dated", ctx.date_str(od, ds)), ("Mode/Terms of Payment", "30 days credit"), ("Due on", ctx.date_str(po["eta_printed"], ds))]
        head = ["Sl No.", "Description of Goods", "Due on", "Quantity", "Rate", "per", "Amount"]
        rows = [["1", it["name"], ctx.date_str(po["eta_printed"], ds), f"{ln['qty']:,.2f} {it['uom']}", money(ln["price"]), it["uom"], money(ln["qty"] * ln["price"])]]
    sub = ln["qty"] * ln["price"]
    return {"kind": "supplier_po", "title": title, "from": (m["company"]["name"], m["company"]["address"]),
            "to": ("Vendor", su["name"], su["address"]), "fields": fields, "table": (head, rows),
            "totals": [("Untaxed Amount", f"LKR {money(sub)}"), ("VAT 18%", f"LKR {money(sub * VAT)}"), ("Total", f"LKR {money(sub * (1 + VAT))}")],
            "notes": ["Please quote the PO number on your delivery note and tax invoice."], "sign": "Purchasing Manager",
            "extract": {"po_number": po["po_no"], "supplier_id": po["supplier_id"], "item_code": ln["item"], "qty": ln["qty"],
                        "uom": it["uom"], "expected_arrival_printed": po["eta_printed"]}}


def supplier_invoice_content(ctx, code, lot, qty, delivery_note=False):
    m = ctx.m
    it = m["items"][code]
    su = m["suppliers"][it["supplier"]]
    rec = date.fromisoformat(lot["received"])
    inv_no = f"{'DN' if delivery_note else ctx.rng.choice(['INV', 'TI', 'SI'])}/{rec.strftime('%y')}/{ctx.rng.randint(1000, 9999)}"
    usd = ctx.has("mixed_currency") and not delivery_note
    price = round(it["price"] / USD_LKR, 4) if usd else it["price"]
    cur = "USD" if usd else "LKR"
    sub = round(qty * price, 2)
    vat = round(sub * VAT, 2)
    style = "dmy"
    poref = f"PO ref: {ctx.rng.randint(300, 400)}" if ctx.p != "A" else f"PO ref: P{ctx.rng.randint(300, 400):05d}"
    fields = [("Invoice No." if not delivery_note else "Delivery Note No.", inv_no), ("Date of invoice" if not delivery_note else "Date", ctx.date_str(rec, style)),
              ("Date of supply", ctx.date_str(rec, style)), ("Place of supply", ctx.company()["address"][-1]), ("Your order", poref.split(": ")[1])]
    if delivery_note:
        head = ["#", "Description", "Batch / lot", "Qty ordered", "Qty delivered", "UoM", "Remarks"]
        ordered = qty + 200 if ctx.state["conflicts"] else qty
        rows = [["1", it["name"], lot["lot"], f"{ordered:,.2f}", f"{qty:,.2f}", it["uom"], "Balance to follow" if ordered != qty else ""]]
        totals, notes = [], ["Received in good order and condition: ____________________   Stores, GRN No. ______"]
        title = "DELIVERY NOTE"
    else:
        head = ["#", "Description of goods", "Quantity", "UoM", f"Unit price ({cur})", f"Value excl. VAT ({cur})"]
        rows = [["1", f"{it['name']} (lot {lot['lot']})", f"{qty:,.2f}", it["uom"], f"{price:,.4f}" if usd else money(price), money(sub)]]
        totals = [("Total value excluding VAT", f"{cur} {money(sub)}"), ("VAT @ 18%", f"{cur} {money(vat)}"), ("Total value including VAT", f"{cur} {money(sub + vat)}")]
        if usd:
            totals.append(("LKR equivalent incl. VAT (rate 300.00)", f"LKR {money((sub + vat) * USD_LKR)}"))
        notes = [f"Amount in words: {amount_words(sub + vat, cur)}", "Mode of payment: Credit 30 days, cheque / bank transfer"]
        title = "TAX INVOICE"
    return {"kind": "supplier_invoice" if not delivery_note else "delivery_note", "title": title,
            "from": (su["name"], su["address"] + [f"TIN: {su['tin']}   Tel: +94 11 2{ctx.rng.randint(100000, 999999)}"]),
            "to": ("Purchaser", m["company"]["name"], m["company"]["address"] + [f"TIN: {m['company']['tin']}"]),
            "fields": fields, "table": (head, rows), "totals": totals, "notes": notes, "sign": "Authorised signature",
            "extract": {"document_no": inv_no, "supplier_id": it["supplier"], "item_code": code, "lot": lot["lot"], "qty": qty,
                        "uom": it["uom"], "currency": cur, "unit_price": price, "date_of_supply": lot["received"]}}


# ---------------------------------------------------------------- drawing
def draw_pdf(path, c, layout=1):
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                            title=c["title"], author=c["from"][0])
    story = []
    fname, faddr = c["from"]
    role, tname, taddr = c["to"]
    if layout == 1:
        left = [Paragraph(f"<b>{fname}</b>", ParagraphStyle("x", parent=P, fontSize=12, leading=15))] + [Paragraph(a, P) for a in faddr]
        right = [Paragraph(f"<b>{c['title']}</b>", ParagraphStyle("t", parent=P, fontSize=14, leading=17, alignment=2))]
        t = Table([[left, right]], colWidths=[110 * mm, 68 * mm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, 0), 1.2, colors.HexColor("#1F3864"))]))
        story += [t, Spacer(1, 6)]
        kv = [[Paragraph(f"<b>{k}</b>", P), Paragraph(str(v), P)] for k, v in c["fields"]]
        party = [Paragraph(f"<b>{role}:</b>", P), Paragraph(f"<b>{tname}</b>", P)] + [Paragraph(a, P) for a in taddr]
        t2 = Table([[party, Table(kv, colWidths=[34 * mm, 50 * mm])]], colWidths=[94 * mm, 84 * mm])
        t2.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story += [t2, Spacer(1, 8)]
    else:
        story += [Paragraph(c["title"], H)]
        a = [Paragraph(f"<b>From</b>", PB), Paragraph(fname, P)] + [Paragraph(x, P) for x in faddr]
        b = [Paragraph(f"<b>{role}</b>", PB), Paragraph(tname, P)] + [Paragraph(x, P) for x in taddr]
        t = Table([[a, b]], colWidths=[89 * mm, 89 * mm])
        t.setStyle(TableStyle([("BOX", (0, 0), (0, 0), 0.6, colors.grey), ("BOX", (1, 0), (1, 0), 0.6, colors.grey),
                               ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 6)]))
        story += [t, Spacer(1, 6)]
        kv = [[Paragraph(f"<b>{k}</b>", P) for k, _ in c["fields"]], [Paragraph(str(v), P) for _, v in c["fields"]]]
        t2 = Table(kv, colWidths=[178 * mm / len(c["fields"])] * len(c["fields"]))
        t2.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEEEEE"))]))
        story += [t2, Spacer(1, 8)]
    if c.get("rush"):
        story += [Paragraph("<font color='#C00000'><b>URGENT - RUSH ORDER. Please confirm capacity within 24 hours.</b></font>", P), Spacer(1, 6)]
    head, rows = c["table"]
    data = [[Paragraph(f"<b>{h}</b>", SMALL) for h in head]] + [[Paragraph(str(x), P) for x in r] for r in rows]
    n = len(head)
    weight = [min(max([len(str(h)) * 0.7] + [len(str(r[j])) for r in rows]), 34) + 4 for j, h in enumerate(head)]
    t3 = Table(data, repeatRows=1, colWidths=[178 * mm * w / sum(weight) for w in weight])
    t3.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D9E1F2" if layout == 1 else "#EEEEEE")),
                            ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story += [t3, Spacer(1, 6)]
    if c["totals"]:
        tt = Table([[Paragraph(k, PB), Paragraph(v, PB)] for k, v in c["totals"]], colWidths=[70 * mm, 45 * mm], hAlign="RIGHT")
        tt.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.6, colors.grey)]))
        story += [tt, Spacer(1, 8)]
    for n_ in c["notes"]:
        story += [Paragraph(n_, P)]
    if c.get("sign"):
        story += [Spacer(1, 26), Paragraph("______________________________", P), Paragraph(c["sign"], SMALL)]
    doc.build(story)


def policy_pdf(ctx, path):
    pol = ctx.state["policy"]
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                            title="Working hours and overtime policy")
    co = ctx.company()
    buyer_code = {"A": "Our buyers' codes of conduct (ETI Base Code clause 6)", "B": "Our customers' supplier code", "C": "Our customers' ethical sourcing requirements"}[ctx.p]
    items = [
        f"Regular working time is {pol['regular_hours_week']} hours per week: one 8-hour shift, Monday to Saturday, 08:00-17:00 with a one-hour lunch break.",
        f"Overtime is voluntary and is paid at not less than {pol['ot_premium_pct']}% of the normal hourly rate.",
        f"Overtime must not exceed {pol['max_ot_hours_day']} hours per day or {pol['max_ot_hours_week']} hours per week.",
        f"Total working time (regular + overtime) must not exceed {pol['max_total_hours_week']} hours in any 7-day period.",
        f"Every worker gets at least {pol['days_off_per_7']} full day off in every 7-day period. Sunday work counts toward the 60-hour limit.",
        "Public and Poya holidays are non-working days unless the factory manager approves a substitute day in writing.",
        "Any plan that needs more overtime than above must be escalated to the General Manager and the buyer's compliance team before work starts.",
    ]
    story = [Paragraph(co["name"], PB), Paragraph(", ".join(co["address"]), SMALL), Spacer(1, 10),
             Paragraph("Working Hours and Overtime Policy", H), Paragraph(f"Policy HR-07, revision 3. Applies to all production staff. {buyer_code} applies.", P), Spacer(1, 8)]
    for i, t in enumerate(items, 1):
        story += [Paragraph(f"{i}. {t}", P), Spacer(1, 4)]
    story += [Spacer(1, 18), Paragraph("Approved: General Manager", SMALL)]
    doc.build(story)
