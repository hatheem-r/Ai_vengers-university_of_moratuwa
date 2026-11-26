"""Bundle catalogue and the hidden ground-truth state for one bundle."""
from __future__ import annotations

import copy
import random
from datetime import date, timedelta

from .common import add_wd, ceil_to
from .masters import MASTERS, explode, routing_for
from .solver import duration, solve

SCENARIOS = {
    "S1": "Normal order", "S2": "Rush order", "S3": "Material shortage", "S4": "Machine breakdown",
    "S5": "Supplier delay", "S6": "Conflicting documents", "S7": "Capacity overload",
    "S8": "Combined shock (shortage + breakdown)", "S9": "Compliance breach (overtime)",
}

# 18 test bundles: 8 A, 5 B, 5 C; mess 5 clean / 8 typical / 5 messy
TEST = [
    ("A", "S1", "clean"), ("A", "S2", "typical"), ("A", "S3", "typical"), ("A", "S4", "messy"),
    ("A", "S6", "typical"), ("A", "S7", "clean"), ("A", "S8", "messy"), ("A", "S9", "typical"),
    ("B", "S1", "typical"), ("B", "S2", "clean"), ("B", "S3", "messy"), ("B", "S4", "typical"), ("B", "S5", "clean"),
    ("C", "S1", "messy"), ("C", "S3", "typical"), ("C", "S5", "typical"), ("C", "S6", "clean"), ("C", "S8", "messy"),
]
DEV = [("A", "S1", "clean", 7), ("B", "S3", "typical", 11), ("C", "S5", "messy", 13)]
SCALE = [("A", "S1", "typical", 21, n) for n in (1, 3, 5)]
HUMAN = ["A_S3_typical", "A_S8_messy", "A_S9_typical", "B_S5_clean", "C_S1_messy", "C_S6_clean"]


def catalogue():
    out = []
    for i, (p, s, m) in enumerate(TEST):
        out.append({"bundle_id": f"{p}_{s}_{m}", "set": "test", "profile": p, "scenario": s, "mess": m, "seed": 1000 + i, "orders": None})
    for p, s, m, seed in DEV:
        out.append({"bundle_id": f"dev_{p}_{s}_{m}", "set": "dev", "profile": p, "scenario": s, "mess": m, "seed": seed, "orders": None})
    for p, s, m, seed, n in SCALE:
        out.append({"bundle_id": f"scale_{p}_{s}_{m}_o{n}", "set": "scale", "profile": p, "scenario": s, "mess": m, "seed": seed, "orders": n})
    return out


TYPICAL = ["title_block", "dmy_dates", "names_not_codes", "unit_variant", "notes_column", "blank_noncritical", "mixed_currency"]
HEAVY = ["scanned_doc", "two_sheets", "column_renamed", "total_row_inside", "missing_critical"]

PO_FMT = {
    "CUS-01": "NWO-PO-{n}", "CUS-02": "45000{n}", "CUS-03": "KSW/26/{n}",
    "CUS-11": "AOI/PO/2026/{n}", "CUS-12": "HHL-PUR-{n}", "CUS-13": "MWS{n}",
    "CUS-21": "GPF-PO-{n}", "CUS-22": "LSM/PO/{n}", "CUS-23": "PRT{n}",
}


def pick_defects(rng, mess, profile):
    if mess == "clean":
        return []
    d = rng.sample(TYPICAL, rng.randint(2, 4))
    if mess == "messy":
        d += rng.sample(HEAVY, rng.randint(1, 2))
        if "scanned_doc" not in d and profile == "C":
            d.append("scanned_doc")
    return d


def primary_of(master, item, kind):
    """The work centre of the main (bottleneck) operation for an item."""
    rt = routing_for(master, item)
    if kind == "bottleneck":
        return max(rt, key=lambda r: r[3] / (master["work_centres"][r[2]]["units"] * master["work_centres"][r[2]]["eff"]))[2]
    return rt[0][2]


def build_state(profile, scenario, mess, seed, n_orders=None, bundle_id=None):
    m = MASTERS[profile]
    rng = random.Random(seed)
    start = date(2026, 11, 30) + timedelta(days=7 * rng.choice([0, 1, 2]))
    st = {"bundle_id": bundle_id or f"{profile}_{scenario}_{mess}", "profile": profile, "profile_name": m["profile_name"],
          "scenario": scenario, "scenario_name": SCENARIOS[scenario], "mess": mess, "seed": seed,
          "planning_start": start.isoformat(), "orders": [], "stock": {}, "open_pos": [], "downtime": [],
          "maintenance_noise": [], "wip": [], "conflicts": [], "events": [],
          "policy": {"regular_hours_week": 48, "max_total_hours_week": 60, "max_ot_hours_day": 2, "max_ot_hours_week": 12,
                     "days_off_per_7": 1, "ot_voluntary": True, "ot_premium_pct": 125},
          "ot_proposal": None}
    st["defects"] = pick_defects(rng, mess, profile)
    # layout choice for outside documents (2 templates each)
    st["layouts"] = {"customer_po": rng.choice([1, 2]), "supplier_invoice": rng.choice([1, 2])}

    # ---- orders
    if n_orders is None:
        n_orders = {"S1": rng.choice([1, 2]), "S2": 1, "S3": rng.choice([1, 2]), "S4": rng.choice([1, 2]),
                    "S5": rng.choice([1, 2]), "S6": rng.choice([1, 2]), "S7": 3, "S8": 2, "S9": 1}[scenario]
    groups = m["order_items"]
    if scenario in ("S7", "S9"):
        g = groups[0]
        chosen = [rng.choice(g) for _ in range(n_orders)]
    else:
        chosen = []
        for i in range(n_orders):
            chosen.append(rng.choice(groups[(i + rng.randint(0, len(groups) - 1)) % len(groups)]))
    cust_ids = list(m["customers"])
    lo, hi, step = m["qty_range"]
    used_nums = set()
    for i, item in enumerate(chosen):
        cid = rng.choice(cust_ids)
        n = rng.randint(1000, 9999)
        while n in used_nums:
            n = rng.randint(1000, 9999)
        used_nums.add(n)
        qty = rng.randrange(lo, hi + 1, step)
        if scenario == "S9":
            qty = rng.randrange(int(hi * 0.8) // step * step, hi + 1, step)
        st["orders"].append(make_order(m, rng, cid, PO_FMT[cid].format(n=n), item, qty,
                                       (start - timedelta(days=rng.randint(4, 12))).isoformat(), "2027-03-31"))

    # ---- stock (generous by default)
    need = {}
    for o in st["orders"]:
        for ln in o["lines"]:
            for c, q in explode(m, ln["item_code"], ln["qty"]).items():
                need[c] = need.get(c, 0.0) + q
    for code, it in m["items"].items():
        if it["kind"] != "rm":
            continue
        base = need.get(code, 0.0)
        if base == 0:
            base = it["pack"] * rng.randint(2, 12)
        reserved = round(base * rng.uniform(0.05, 0.2), 2) if (code in need and profile != "C") else 0.0  # Tally keeps no reservations
        total = base * rng.uniform(1.35, 2.2) + reserved
        st["stock"][code] = {"lots": make_lots(m, rng, code, total, start), "reserved": round_q(reserved, it["uom"])}
    if profile == "C":  # a quality-hold lot of raw tea that must not be counted
        code = "BLK-BOP"
        st["stock"][code]["lots"].append({"location": "Quality Hold Godown", "lot": f"RT-{rng.randint(5100, 5199)}",
                                          "qty": float(rng.randrange(150, 400, 50)), "status": "QC HOLD",
                                          "expiry": (start + timedelta(days=200)).isoformat(),
                                          "received": (start - timedelta(days=9)).isoformat()})

    # ---- a routine open supplier PO that arrives before it is needed
    noise_items = [c for c in need if m["items"][c]["kind"] == "rm"]
    c = rng.choice(noise_items)
    st["open_pos"].append(make_po(m, rng, c, ceil_to(need[c] * 0.3, m["items"][c]["pack"]), start, add_wd(start, -1)))

    # ---- WIP on lines (existing orders)
    wcs_main = sorted({primary_of(m, it, "bottleneck") for g in groups for it in g})
    for wc in wcs_main:
        days = rng.uniform(0.3, 1.5)
        st["wip"].append(make_wip(m, rng, wc, days, start))

    # ---- maintenance noise: a past, closed breakdown
    wc = rng.choice(list(m["key_machine"]))
    st["maintenance_noise"].append({"wc": wc, "machine": m["key_machine"][wc], "start": add_wd(start, -rng.randint(6, 12)).isoformat(),
                                    "end": None, "hours": round(rng.uniform(1.5, 5), 1), "reason": "Electrical fault",
                                    "status": "Closed", "remarks": "Contactor replaced, tested OK"})

    if scenario in ("S2", "S7"):  # the other lines are booked with running orders
        g0 = primary_of(m, chosen[0], "bottleneck")
        for alt in m["work_centres"][g0]["alts"]:
            st["wip"] = [w for w in st["wip"] if w["wc"] != alt]
            st["wip"].append(make_wip(m, rng, alt, rng.uniform(9, 12) if scenario == "S7" else rng.uniform(5, 7), start))

    # ---- due dates from a nominal (undisrupted) solve
    tune_due_dates(st, m, rng, scenario)
    if scenario == "S2":  # stock also covers the rush order
        rush = st["orders"][-1]["lines"][0]
        for c, q in explode(m, rush["item_code"], rush["qty"]).items():
            s = st["stock"][c]
            free = sum(l["qty"] for l in s["lots"] if l["status"] == "OK") - s["reserved"]
            if free < (need.get(c, 0) + q) * 1.15:
                s["lots"][0]["qty"] = round_q(s["lots"][0]["qty"] + (need.get(c, 0) + q) * 1.25 - free, m["items"][c]["uom"])

    # ---- disruptions
    first_item = st["orders"][0]["lines"][0]["item_code"]
    if scenario in ("S3", "S8"):
        inject_shortage(st, m, rng, first_item)
    if scenario in ("S4", "S8"):
        inject_breakdown(st, m, rng, first_item)
    if scenario == "S5":
        inject_supplier_delay(st, m, rng, first_item)
    if scenario == "S6":
        inject_conflict(st, m, rng)
    if scenario == "S9":
        wc = primary_of(m, first_item, "bottleneck")
        st["ot_proposal"] = {"wc": wc, "weekday_ot_hours": 4, "sunday_hours": 8, "weeks": 2,
                             "weekly_total_hours": 48 + 6 * 4 + 8, "proposed_by": "Production Manager"}
        st["events"].append({"type": "ot_proposal", "wc": wc})
    if "missing_critical" in st["defects"]:
        st["missing_due_po"] = st["orders"][-1]["po_no"]
    return st


def round_q(q, uom):
    return float(round(q)) if uom in ("pcs", "cone") else round(q, 2)


def make_order(m, rng, cid, po_no, item, qty, po_date, due):
    it = m["items"][item]
    ln = {"line_no": 1, "item_code": item, "qty": qty, "uom": "pcs", "due_date": due,
          "unit_price": m["unit_price"][item]}
    if m["profile"] == "A":
        ratio = m["size_ratio"]
        unit = qty // sum(ratio)
        sizes = {s: unit * r for s, r in zip(m["sizes"], ratio)}
        sizes[m["sizes"][1]] += qty - sum(sizes.values())
        ln.update({"colour": it["colour"], "sizes": sizes, "style": it["style"]})
    return {"po_no": po_no, "customer_id": cid, "po_date": po_date, "rush": False, "lines": [ln]}


def make_lots(m, rng, code, total, start):
    it = m["items"][code]
    uom = it["uom"]
    n = 2 if (it["cat"] in ("Fabric", "Steel", "Raw tea") and total > it["pack"] * 3) else 1
    parts = [total * 0.6, total * 0.4] if n == 2 else [total]
    lots = []
    for i, q in enumerate(parts):
        loc = {"A": lambda: m["locations"][it["cat"]], "B": lambda: m["warehouses"][it["cat"]],
               "C": lambda: m["godowns"][it["cat"]]}[m["profile"]]()
        lot = {"location": loc, "lot": f"{code.split('-')[0]}-{rng.randint(2600, 2699)}{i}", "qty": round_q(q, uom), "status": "OK",
               "received": (start - timedelta(days=rng.randint(3, 40))).isoformat()}
        if m["profile"] == "C":
            lot["expiry"] = (start + timedelta(days=rng.randint(120, 500))).isoformat()
        lots.append(lot)
    return lots


def make_po(m, rng, code, qty, start, order_date, eta=None):
    it = m["items"][code]
    sup = it["supplier"]
    lead = m["suppliers"][sup]["lead_days"]
    eta = eta or add_wd(start, rng.randint(0, 1))
    prefix = {"A": "P{n:05d}", "B": "{n}", "C": "PO/{n}/26-27"}[m["profile"]]
    return {"po_no": prefix.format(n=rng.randint(410, 980)) if m["profile"] != "A" else prefix.format(n=rng.randint(410, 980)),
            "supplier_id": sup, "order_date": add_wd(start, -(lead + 1)).isoformat(),
            "eta": eta.isoformat(), "eta_printed": eta.isoformat(),
            "lines": [{"item": code, "qty": float(qty), "price": it["price"]}]}


def make_wip(m, rng, wc, days, start):
    fg = [c for c, it in m["items"].items() if it["kind"] == "fg"]
    cands = [c for c in fg if wc in [r[2] for r in routing_for(m, c)]] or fg
    item = rng.choice(cands)
    op = [r for r in routing_for(m, item) if r[2] == wc]
    std = op[0][3] if op else 1.0
    w = m["work_centres"][wc]
    qty = int(days * 480 * w["units"] * w["eff"] / std)
    qty = max(qty - qty % 10, 10)
    minutes = qty * std / (w["units"] * w["eff"])
    prefix = {"A": "WH/MO/{n:05d}", "B": "PRD-{n}", "C": "MJ/{n}"}[m["profile"]]
    return {"wo": prefix.format(n=rng.randint(300, 900)), "item": item, "wc": wc, "remaining_qty": qty,
            "minutes": round(minutes, 1), "done_qty": int(qty * rng.uniform(0.3, 1.5)) // 10 * 10,
            "customer": rng.choice(list(m["customers"]))}


def tune_due_dates(st, m, rng, scenario):
    start = date.fromisoformat(st["planning_start"])
    plan = solve(st, m, force_ot=scenario in ("S7", "S9"))
    fin = {(w["customer_po"], w["line_no"]): date.fromisoformat(w["finish_date"]) for w in plan["work_orders"]}
    orders = st["orders"]
    if scenario == "S7":
        ranked = sorted(orders, key=lambda o: fin[(o["po_no"], 1)])
        f = [fin[(o["po_no"], 1)] for o in ranked]
        dues = [add_wd(f[0], 1), add_wd(f[1], 1), add_wd(f[2], -2)]
        for o, d in zip(ranked, dues):
            o["lines"][0]["due_date"] = d.isoformat()
    elif scenario == "S9":
        o = orders[0]
        o["lines"][0]["due_date"] = add_wd(fin[(o["po_no"], 1)], -2).isoformat()
    else:
        for o in orders:
            for ln in o["lines"]:
                ln["due_date"] = add_wd(fin[(o["po_no"], ln["line_no"])], rng.randint(1, 3)).isoformat()
    if scenario == "S2":  # a rush order on the same line as order 1, placed after the plan was made
        base = orders[0]["lines"][0]["item_code"]
        lo, hi, step = m["qty_range"]
        cid = rng.choice([c for c in m["customers"] if c != orders[0]["customer_id"]])
        rush = make_order(m, rng, cid, PO_FMT[cid].format(n=rng.randint(1000, 9999)), base,
                          rng.randrange(lo, (lo + hi) // 2 + 1, step), (start - timedelta(days=1)).isoformat(),
                          add_wd(start, rng.randint(5, 7)).isoformat())
        rush["rush"] = True
        orders.append(rush)
        # the existing order now has only one day of slack
        o = orders[0]
        o["lines"][0]["due_date"] = add_wd(fin[(o["po_no"], 1)], 1).isoformat()
        st["events"].append({"type": "rush_order", "po_no": rush["po_no"]})


def critical_component(m, item, rng):
    comps = list(explode(m, item, 1).items())
    if m["profile"] == "A":
        return [c for c, _ in comps if m["items"][c]["cat"] == "Fabric"][0]
    if m["profile"] == "B":
        return rng.choice([c for c, _ in comps if m["items"][c]["cat"] in ("Steel", "Hardware")])
    return rng.choice([c for c, _ in comps if m["items"][c]["cat"] in ("Raw tea", "Packing material")])


def total_need(st, m, code):
    t = 0.0
    for o in st["orders"]:
        for ln in o["lines"]:
            t += explode(m, ln["item_code"], ln["qty"]).get(code, 0.0)
    return t


def set_free_stock(st, m, code, free):
    s = st["stock"][code]
    ok = [l for l in s["lots"] if l["status"] == "OK"]
    target = max(free, 0) + s["reserved"]
    cur = sum(l["qty"] for l in ok)
    for l in ok:
        l["qty"] = round_q(l["qty"] * target / cur, m["items"][code]["uom"])
    st["open_pos"] = [p for p in st["open_pos"] if p["lines"][0]["item"] != code]


def inject_shortage(st, m, rng, item):
    code = critical_component(m, item, rng)
    need = total_need(st, m, code)
    set_free_stock(st, m, code, need * rng.uniform(0.55, 0.7))
    st["events"].append({"type": "shortage", "item": code})


def inject_breakdown(st, m, rng, item):
    wc = primary_of(m, item, "bottleneck")
    start = date.fromisoformat(st["planning_start"])
    d0 = add_wd(start, -1)
    d1 = add_wd(start, rng.randint(3, 5))
    st["downtime"].append({"wc": wc, "machine": m["key_machine"].get(wc, wc), "start": d0.isoformat(), "end": d1.isoformat(),
                           "reason": rng.choice(["Mechanical breakdown", "Electrical fault", "Spare part awaited"]),
                           "status": "Open", "expected_return": add_wd(d1, 1).isoformat(),
                           "remarks": rng.choice(["Motor burnt, rewinding at outside workshop",
                                                  "Spare ordered from Colombo agent, ETA confirmed",
                                                  "Gearbox bearing failed, part on order"])})
    # alternates are partly booked with existing orders
    for alt in m["work_centres"][wc]["alts"]:
        st["wip"].append(make_wip(m, rng, alt, rng.uniform(1.0, 2.5), start))
    st["events"].append({"type": "breakdown", "wc": wc})


def inject_supplier_delay(st, m, rng, item):
    code = critical_component(m, item, rng)
    need = total_need(st, m, code)
    set_free_stock(st, m, code, need * rng.uniform(0.2, 0.35))
    start = date.fromisoformat(st["planning_start"])
    po = make_po(m, rng, code, ceil_to(need * 0.85, m["items"][code]["pack"]), start, None, add_wd(start, 1))
    new_eta = add_wd(start, rng.randint(6, 9))
    po["eta"] = new_eta.isoformat()
    st["open_pos"].append(po)
    st["events"].append({"type": "supplier_delay", "item": code, "po_no": po["po_no"], "old_eta": po["eta_printed"],
                         "new_eta": new_eta.isoformat(), "reason": rng.choice(["vessel rollover at Colombo port", "machine breakdown at our mill", "customs inspection hold on raw material"])})


def inject_conflict(st, m, rng):
    o = st["orders"][0]
    ln = o["lines"][0]
    if m["profile"] == "C":
        code = [c for c in explode(m, ln["item_code"], 1) if m["items"][c]["cat"] == "Raw tea"][0]
        lot = [l for l in st["stock"][code]["lots"] if l["status"] == "OK"][0]
        diff = 200.0
        # truth: the delivery note; the stock report over-states this lot by 200 kg
        st["conflicts"].append({"kind": "receipt_mismatch", "item": code, "lot": lot["lot"],
                                "stock_report_qty": lot["qty"] + diff, "delivery_note_qty": lot["qty"],
                                "truth": "delivery_note", "expected_resolution": "Use the lower, delivered quantity and ask stores to recount the lot."})
        lot["received_from"] = m["items"][code]["supplier"]
    else:
        delta = rng.choice([120, 240, 360]) if m["profile"] == "A" else rng.choice([20, 30, 50])
        st["conflicts"].append({"kind": "qty_mismatch", "po_no": o["po_no"], "item": ln["item_code"],
                                "customer_po_qty": ln["qty"], "confirmation_qty": ln["qty"] - delta,
                                "truth": "customer_po", "expected_resolution": "Plan the customer PO quantity, flag for sales to confirm with the buyer."})
