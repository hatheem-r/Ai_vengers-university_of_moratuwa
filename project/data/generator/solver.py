"""Reference solver: turns a bundle's hidden state into the expected plan (answer key).

Rules (kept simple on purpose so a human can check them by hand):
1. One work order per customer PO line. Orders are sequenced by due date (EDD),
   ties broken by PO date.
2. Material: multi-level BoM explosion with wastage. Each work order takes, in order,
   free stock (on hand in OK status minus reserved), then open supplier POs by ETA.
   Anything left is a shortage covered by a suggested PO to the fastest supplier.
   Goods received on day D are usable from the next working day (receiving + QC).
3. Scheduling: forward, finite capacity, operations in sequence. Each work centre
   is one resource whose daily capacity is 480 working minutes (one shift),
   0 on downtime days. Duration = setup + qty x std minutes / (units x efficiency).
   An operation may move to an alternate work centre when that finishes earlier.
   Existing WIP is loaded on its work centre first.
4. Overtime: only if an order is late, and only up to the policy limit
   (2 h/day, 60 h/week), on the work centres that hold the late orders.
"""
from __future__ import annotations

from datetime import date, timedelta

from .common import (OT_MIN_POLICY, REG_MIN, add_wd, iso_dt, minute_to_clock, next_wd,
                     qty_round, ceil_to, wd_between, working_days)
from .masters import explode, routing_for

HORIZON = 160


class Calendar:
    def __init__(self, start: date, downtime: list, ot_wcs: set, ot_min: int = OT_MIN_POLICY):
        self.days = working_days(start, HORIZON)
        self.index = {d: i for i, d in enumerate(self.days)}
        self.down = {}
        for dt in downtime:
            d0, d1 = date.fromisoformat(dt["start"]), date.fromisoformat(dt["end"])
            self.down.setdefault(dt["wc"], []).append((d0, d1))
        self.ot_wcs = ot_wcs
        self.ot_min = ot_min

    def cap(self, wc: str, i: int) -> int:
        d = self.days[i]
        for d0, d1 in self.down.get(wc, []):
            if d0 <= d <= d1:
                return 0
        return REG_MIN + (self.ot_min if wc in self.ot_wcs else 0)

    def pos_of(self, d: date):
        d = next_wd(d)
        return (self.index[d], 0.0)

    def consume(self, wc, start, dur):
        d, m = start
        while m >= self.cap(wc, d) - 1e-9:
            d, m = d + 1, 0.0
        s = (d, m)
        rem, ot = dur, False
        while rem > 1e-9:
            c = self.cap(wc, d)
            avail = c - m
            if avail <= 1e-9:
                d, m = d + 1, 0.0
                continue
            take = min(avail, rem)
            if m + take > REG_MIN + 1e-9:
                ot = True
            m += take
            rem -= take
            if rem > 1e-9:
                d, m = d + 1, 0.0
        return s, (d, m), ot

    def to_dt(self, pos):
        d, m = pos
        return minute_to_clock(self.days[d], m)


def duration(master, wc, qty, std, setup):
    w = master["work_centres"][wc]
    return setup + qty * std / (w["units"] * w["eff"])


def work_orders_from(state):
    wos = []
    for o in state["orders"]:
        for ln in o["lines"]:
            wos.append({"customer_po": o["po_no"], "po_date": o["po_date"], "item_code": ln["item_code"],
                        "qty": ln["qty"], "uom": ln["uom"], "due_date": ln["due_date"],
                        "colour": ln.get("colour"), "sizes": ln.get("sizes"), "line_no": ln["line_no"]})
    wos.sort(key=lambda w: (w["due_date"], w["po_date"], w["customer_po"], w["line_no"]))
    for i, w in enumerate(wos, 1):
        w["wo_id"] = f"WO-{i:03d}"
        w["priority"] = i
    return wos


def schedule(state, master, wos, ready, ot_wcs):
    start = date.fromisoformat(state["planning_start"])
    cal = Calendar(start, state.get("downtime", []), ot_wcs)
    pos = {wc: (0, 0.0) for wc in master["work_centres"]}
    for w in state.get("wip", []):  # existing load first
        _, pos[w["wc"]], _ = cal.consume(w["wc"], pos[w["wc"]], w["minutes"])
    out = []
    for wo in wos:
        earliest = cal.pos_of(ready[wo["wo_id"]])
        ops = []
        for seq, op, wc0, std, setup in routing_for(master, wo["item_code"]):
            best = None
            for wc in [wc0] + master["work_centres"][wc0]["alts"]:
                dur = duration(master, wc, wo["qty"], std, setup)
                st = max(earliest, pos[wc])
                s, e, ot = cal.consume(wc, st, dur)
                if best is None or e < best[2]:
                    best = (wc, s, e, ot, dur)
            wc, s, e, ot, dur = best
            pos[wc] = e
            earliest = e
            ops.append({"seq": seq, "operation": op, "machine_id": wc, "start_pos": s, "end_pos": e,
                        "start": iso_dt(cal.to_dt(s)), "end": iso_dt(cal.to_dt(e)), "qty": wo["qty"],
                        "uses_overtime": ot, "minutes": round(dur, 1), "primary": wc0})
        fin = cal.days[ops[-1]["end_pos"][0]]
        due = date.fromisoformat(wo["due_date"])
        out.append({**wo, "operations": ops, "planned_start": ops[0]["start"], "planned_finish": ops[-1]["end"],
                    "start_date": cal.days[ops[0]["start_pos"][0]].isoformat(), "finish_date": fin.isoformat(),
                    "is_late": fin > due, "days_late": wd_between(due, fin)})
    return out


def free_stock(state, code):
    s = state["stock"].get(code, {"lots": [], "reserved": 0})
    ok = sum(l["qty"] for l in s["lots"] if l.get("status", "OK") == "OK")
    return ok - s.get("reserved", 0)


def allocate(state, master, wos, need_by):
    """Returns per-WO ready dates, material requirement rows, shortages, suggested POs."""
    start = date.fromisoformat(state["planning_start"])
    stock = {}
    pos_q = {}
    for p in state["open_pos"]:
        for ln in p["lines"]:
            pos_q.setdefault(ln["item"], []).append({"po": p["po_no"], "ready": add_wd(date.fromisoformat(p["eta"]), 1),
                                                    "qty": ln["qty"], "supplier": p["supplier_id"]})
    for v in pos_q.values():
        v.sort(key=lambda x: x["ready"])
    req, onorder, short = {}, {}, {}
    ready = {}
    for wo in wos:
        nb = date.fromisoformat(need_by[wo["wo_id"]])
        r = start
        for code, q in explode(master, wo["item_code"], wo["qty"]).items():
            req.setdefault(code, {"qty": 0.0, "wos": []})
            req[code]["qty"] += q
            req[code]["wos"].append(wo["wo_id"])
            if code not in stock:
                stock[code] = max(free_stock(state, code), 0.0)
            take = min(stock[code], q)
            stock[code] -= take
            q -= take
            for po in pos_q.get(code, []):
                if q <= 1e-9:
                    break
                t = min(po["qty"], q)
                if t <= 0:
                    continue
                po["qty"] -= t
                q -= t
                if po["ready"] <= nb:
                    onorder[code] = onorder.get(code, 0.0) + t
                    r = max(r, po["ready"])
                else:  # arrives after it is needed: counts as a supplier-delay shortage
                    short.setdefault(code, []).append({"wo": wo["wo_id"], "qty": t, "cause": "supplier_delay",
                                                       "po_ready": po["ready"], "po": po["po"], "need_by": nb})
            if q > 1e-9:
                short.setdefault(code, []).append({"wo": wo["wo_id"], "qty": q, "cause": "insufficient_stock",
                                                   "po_ready": None, "po": None, "need_by": nb})
        ready[wo["wo_id"]] = r

    shortages, sugg = [], []
    for code, rows in short.items():
        it = master["items"][code]
        cands = [s for s in (it["supplier"], it.get("alt")) if s]
        sup = min(cands, key=lambda s: master["suppliers"][s]["lead_days"])
        lead = master["suppliers"][sup]["lead_days"]
        new_ready = add_wd(add_wd(start, lead), 1)
        total = sum(x["qty"] for x in rows)
        nb = min(x["need_by"] for x in rows)
        cause = "supplier_delay" if any(x["cause"] == "supplier_delay" for x in rows) else "insufficient_stock"
        for x in rows:
            alt_ready = new_ready if x["po_ready"] is None else min(new_ready, x["po_ready"])
            ready[x["wo"]] = max(ready[x["wo"]], alt_ready)
        shortages.append({"item_code": code, "uom": it["uom"], "shortfall_qty": qty_round(total, it["uom"]),
                          "need_by": nb.isoformat(), "affected_work_orders": sorted({x["wo"] for x in rows}),
                          "cause": cause})
        reason = (f"Covers the {qty_round(total, it['uom'])} {it['uom']} shortfall; "
                  f"{master['suppliers'][sup]['name']} lead time is {lead} working days.")
        if cause == "supplier_delay":
            dl = [x for x in rows if x["po_ready"] is not None]
            late = max(x["po_ready"] for x in dl)
            reason = (f"Open PO {dl[0]['po']} now arrives too late (usable {late.isoformat()}); "
                      f"alternate supplier {master['suppliers'][sup]['name']} can deliver in {lead} working days. Needs approval.")
        sugg.append({"item_code": code, "qty": ceil_to(total, it["pack"]), "uom": it["uom"], "supplier_id": sup,
                     "need_by": nb.isoformat(), "expedite": new_ready > nb, "reason": reason,
                     "expected_usable_date": new_ready.isoformat()})

    mat = []
    for code, v in sorted(req.items()):
        it = master["items"][code]
        s = state["stock"].get(code, {"lots": [], "reserved": 0})
        on_hand = sum(l["qty"] for l in s["lots"] if l.get("status", "OK") == "OK")
        sh = sum(x["qty"] for x in short.get(code, []))
        mat.append({"item_code": code, "uom": it["uom"], "required_qty": qty_round(v["qty"], it["uom"]),
                    "on_hand_qty": round(on_hand, 2), "reserved_qty": round(s.get("reserved", 0), 2),
                    "on_order_qty": round(onorder.get(code, 0.0), 2),
                    "net_shortage_qty": qty_round(sh, it["uom"]) if sh > 1e-9 else 0,
                    "for_work_orders": sorted(set(v["wos"]))})
    return ready, mat, shortages, sugg


def solve(state, master, force_ot=False):
    wos = work_orders_from(state)
    start = state["planning_start"]
    # pass 1: nominal schedule with all material available -> need-by dates
    nominal = schedule(state, master, wos, {w["wo_id"]: date.fromisoformat(start) for w in wos}, set())
    need_by = {w["wo_id"]: w["start_date"] for w in nominal}
    ready, mat, shortages, sugg = allocate(state, master, wos, need_by)
    ot = set(master["work_centres"]) if force_ot else set()
    plan = schedule(state, master, wos, ready, ot)
    for _ in range(4):  # add policy overtime where it reduces lateness
        late = [w for w in plan if w["is_late"]]
        if not late:
            break
        cand = set(ot)
        for w in late:
            cand |= {op["machine_id"] for op in w["operations"]}
        if cand == ot:
            break
        trial = schedule(state, master, wos, ready, cand)
        if sum(w["days_late"] for w in trial) < sum(w["days_late"] for w in plan):
            plan, ot = trial, cand
        else:
            break
    return {"work_orders": plan, "material_requirements": mat, "shortages": shortages,
            "suggested_pos": sugg, "ot_work_centres": sorted(ot), "need_by": need_by,
            "ready": {k: v.isoformat() for k, v in ready.items()}}
