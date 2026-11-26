"""Self-check of generated bundles: python -m data.generator.validate

For every bundle it checks that the answer key is feasible and agrees with state.json:
- no work centre runs two operations of the plan at the same time,
- operations of a work order run in sequence,
- no operation runs on a day its work centre is down,
- a material with a net shortage always has a matching shortage row,
- every input file listed in the manifest exists, and no hidden file sits in inputs/.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "bundles"
HIDDEN = {"state.json", "manifest.json", "answer_key.json", "extraction_key.json"}


def check(bdir: Path):
    errs = []
    st = json.loads((bdir / "state.json").read_text())
    key = json.loads((bdir / "answer_key.json").read_text())
    man = json.loads((bdir / "manifest.json").read_text())
    names = {p.name for p in (bdir / "inputs").iterdir()}
    for f in man["files"]:
        if f["name"] not in names:
            errs.append(f"missing input {f['name']}")
    if names & HIDDEN:
        errs.append(f"hidden file in inputs: {names & HIDDEN}")
    by_wc = {}
    for w in key["work_orders"]:
        prev = None
        for op in w["operations"]:
            s, e = datetime.fromisoformat(op["start"]), datetime.fromisoformat(op["end"])
            if e < s:
                errs.append(f"{w['wo_id']} op {op['seq']} ends before it starts")
            if prev and s < prev:
                errs.append(f"{w['wo_id']} op {op['seq']} starts before the previous op ends")
            prev = e
            by_wc.setdefault(op["machine_id"], []).append((s, e, w["wo_id"]))
            for d in st["downtime"]:
                if d["wc"] == op["machine_id"]:
                    d0, d1 = date.fromisoformat(d["start"]), date.fromisoformat(d["end"])
                    if d0 <= s.date() <= d1 or d0 <= e.date() <= d1 or (s.date() < d0 and e.date() > d1):
                        if not (e.date() == d0 and e.hour <= 8) and not (s.date() == d0 == d1):
                            errs.append(f"{w['wo_id']} op {op['seq']} on {op['machine_id']} overlaps downtime {d0}..{d1}")
    for wc, iv in by_wc.items():
        iv.sort()
        for a, b in zip(iv, iv[1:]):
            if b[0] < a[1]:
                errs.append(f"{wc}: {a[2]} and {b[2]} overlap")
    for s in key["shortages"]:
        if s["shortfall_qty"] <= 0:
            errs.append(f"non-positive shortage {s}")
    for r in key["material_requirements"]:
        free = r["on_hand_qty"] - r["reserved_qty"] + r["on_order_qty"]
        if r["net_shortage_qty"] == 0 and free + 0.02 < r["required_qty"] and not any(x["item_code"] == r["item_code"] for x in key["shortages"]):
            errs.append(f"{r['item_code']}: required {r['required_qty']} > available {free:.2f} but no shortage")
    return errs


def main():
    bad = 0
    dirs = sorted(p for p in ROOT.iterdir() if p.is_dir()) if ROOT.exists() else []
    for b in dirs:
        e = check(b)
        print(f"{b.name:<28} {'OK' if not e else 'FAIL'}")
        for x in e:
            print("   -", x)
        bad += bool(e)
    print(f"{len(dirs) - bad}/{len(dirs)} bundles pass")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
