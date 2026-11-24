"""Render context shared by all renderers: state, master, defects, file registry."""
from __future__ import annotations

import random
import re
from datetime import date
from pathlib import Path

from .common import fmt_date

# Alternate units used by the "unit_variant" defect: item -> (shown uom, base units per shown unit)
UNIT_VARIANTS = {
    "A": {"BTN-4H-15L": ("Gross", 144), "LBL-MAIN": ("Thousand", 1000)},
    "B": {"SCR-M6-20": ("Box (100)", 100), "BLT-M8-16": ("Box (100)", 100)},
    "C": {"FLM-INR-100": ("Roll (500 m)", 500), "FLM-INR-200": ("Roll (500 m)", 500)},
}

DEFAULT_DATE = {"A": "iso", "B": "dmy_dot", "C": "tally"}


def safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")


class Ctx:
    def __init__(self, state, master, plan, out_dir: Path):
        self.state, self.m, self.plan = state, master, plan
        self.p = state["profile"]
        self.defects = set(state["defects"])
        self.rng = random.Random(state["seed"] * 7919 + 1)
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.start = date.fromisoformat(state["planning_start"])
        self.files = {}      # role -> file name (or list)
        self.extract = []    # extraction key entries
        self.locs = {}       # (file, item_code) -> row number in sheet
        self.applied = []    # defects actually applied: {defect, document, detail}
        self.unit_shown = {}

    def has(self, d):
        return d in self.defects

    def applied_defect(self, defect, document, detail):
        self.applied.append({"defect": defect, "document": document, "detail": detail})

    def date_str(self, d, style=None, doc=None):
        if isinstance(d, str):
            d = date.fromisoformat(d[:10])
        style = style or DEFAULT_DATE[self.p]
        return fmt_date(d, style)

    def register(self, role, fname, doc_type, fields=None, lines=None, many=False):
        if many:
            self.files.setdefault(role, []).append(fname)
        else:
            self.files[role] = fname
        self.extract.append({"document": fname, "doc_type": doc_type, "fields": fields or {}, "lines": lines or []})

    def buyer_code(self, code):
        """Outside customers of B and C order with their own article numbers."""
        if self.p == "A":
            return code
        cid = self.state["orders"][0]["customer_id"]
        n = sum(ord(ch) for ch in code + cid) % 9000 + 1000
        return {"B": f"ART-{n}", "C": f"SKU{n}"}[self.p]

    def item_name(self, code):
        return self.m["items"][code]["name"]

    def company(self):
        return self.m["company"]
