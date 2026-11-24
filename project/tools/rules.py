"""rule_check: deterministic checks on a draft plan and the facts it was built from.

Used by the multi-agent Checker and by the baseline before it submits. Each
rule is a small function registered in :data:`RULES`; it returns violations
already shaped like plan flags (type, severity, message, source documents,
``needs_human``), so an agent can turn a finding into a flag directly.

Rules:

=====================  ==========================================================
document_conflicts     Same order / PO line read with different values in
                       different documents (S6-style conflicts; need a human)
invoice_po_match       Invoice vs PO vs goods received: missing or unknown PO,
                       item not on PO, quantity, price or supplier differences
order_coverage         Every customer order line planned with the right item,
                       quantity and due date; no work order for an unknown order
missing_fields         Order lines without item, quantity or due date
schedule_feasibility   Double-booked machines, work during downtime, unknown
                       machines, operation order, work before planning start or
                       before materials are ready, work outside working hours
overtime_policy        Overtime per machine per day / week over the limit;
                       work on non-working days when not allowed
compliance             Required certificates missing or expired
plan_consistency       is_late / days_late, start/finish vs operations, late
                       orders and shortages without flags, unresolved conflicts
                       marked as not needing a human, flags without sources,
                       duplicate ids
=====================  ==========================================================

A rule that needs input the caller did not give is listed in
``rules_skipped`` with the reason, never silently passed.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from langchain_core.tools import StructuredTool

from project.core.schemas import (
    RuleCheckInput,
    RuleCheckResult,
    RuleViolation,
    SourceRef,
    WorkOrder,
)
from project.core.tracing import SL_TZ
from project.tools.common import validation_error_message

EPS = 1e-6


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=SL_TZ) if dt.tzinfo is None else dt.astimezone(SL_TZ)


def _k(s: str | None) -> str:
    return (s or "").strip().upper()


def _srcs(*items: Any) -> list[SourceRef]:
    out, seen = [], set()
    for it in items:
        src = getattr(it, "source", None)
        if src is not None:
            key = src.model_dump_json()
            if key not in seen:
                seen.add(key)
                out.append(src)
    return out


def _differs(a: float | None, b: float | None, tol_pct: float) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) > max(EPS, abs(b) * tol_pct / 100)


class _Ctx:
    def __init__(self, inp: RuleCheckInput):
        self.inp = inp
        self.plan = inp.plan
        self.out: list[RuleViolation] = []
        self.wos: dict[str, WorkOrder] = {w.wo_id: w for w in inp.plan.work_orders}

    def add(self, rule: str, flag_type: str, severity: str, message: str, *, wo_id: str | None = None,
            sources: list[SourceRef] | None = None, needs_human: bool | None = None) -> None:
        if needs_human is None:
            needs_human = flag_type in ("conflict", "compliance_breach")
        self.out.append(RuleViolation(
            rule=rule, flag_type=flag_type, severity=severity, message=message, wo_id=wo_id,
            sources=sources or [], needs_human=needs_human,
            already_flagged=self._flagged(flag_type, wo_id, sources or []),
        ))

    def _flagged(self, flag_type: str, wo_id: str | None, sources: list[SourceRef]) -> bool:
        docs = {_k(s.document) for s in sources}
        for f in self.plan.flags:
            if f.type != flag_type:
                continue
            if wo_id and wo_id in f.message:
                return True
            if docs & {_k(s.document) for s in f.sources}:
                return True
        return False


# ---------------------------------------------------------------------------
# Rules. Each returns None when it ran, or a string saying why it was skipped.
# ---------------------------------------------------------------------------


def document_conflicts(c: _Ctx) -> str | None:
    inp = c.inp
    if not inp.customer_orders and not inp.purchase_orders:
        return "no customer_orders or purchase_orders given"
    groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for o in inp.customer_orders:
        if o.item_code:
            groups[("Customer order", f"{_k(o.customer_po)} / {_k(o.item_code)}")].append(o)
    for p in inp.purchase_orders:
        groups[("Purchase order", f"{_k(p.po_number)} / {_k(p.item_code)}")].append(p)
    for (kind, key), rows in groups.items():
        if len(rows) < 2:
            continue
        docs = {r.source.document for r in rows if r.source}
        if len(docs) < 2:
            continue  # repeated lines inside one document are not a cross-document conflict
        for field in ("qty", "due_date", "expected_date", "unit_price", "supplier_id"):
            values = {getattr(r, field, None) for r in rows} - {None}
            if len(values) > 1:
                shown = "; ".join(f"{r.source.document if r.source else '?'}: {getattr(r, field)}" for r in rows
                                  if getattr(r, field, None) is not None)
                c.add("document_conflicts", "conflict", "high",
                      f"{kind} {key}: documents disagree on {field} ({shown}).",
                      sources=_srcs(*rows), needs_human=True)
    return None


def invoice_po_match(c: _Ctx) -> str | None:
    inp, pol = c.inp, c.inp.policy
    if not inp.invoices:
        return "no invoices given"
    pos: dict[str, list[Any]] = defaultdict(list)
    for p in inp.purchase_orders:
        pos[_k(p.po_number)].append(p)
    received: dict[tuple[str, str], float] = defaultdict(float)
    receipt_rows: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for r in inp.receipts:
        received[(_k(r.po_number), _k(r.item_code))] += r.qty
        receipt_rows[(_k(r.po_number), _k(r.item_code))].append(r)
    billed: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for inv in inp.invoices:
        if not inv.po_number:
            c.add("invoice_po_match", "missing_field", "medium",
                  f"Invoice {inv.invoice_number} ({inv.item_code}) has no PO number.", sources=_srcs(inv))
            continue
        po_key = _k(inv.po_number)
        if po_key not in pos:
            if inp.purchase_orders:
                c.add("invoice_po_match", "invoice_po_mismatch", "medium",
                      f"Invoice {inv.invoice_number} refers to PO {inv.po_number}, which is not in the documents.",
                      sources=_srcs(inv))
            continue
        lines = [p for p in pos[po_key] if _k(p.item_code) == _k(inv.item_code)]
        if not lines:
            c.add("invoice_po_match", "invoice_po_mismatch", "medium",
                  f"Invoice {inv.invoice_number} bills {inv.item_code}, which is not on PO {inv.po_number}.",
                  sources=_srcs(inv, *pos[po_key]))
            continue
        billed[(po_key, _k(inv.item_code))].append(inv)
        po = lines[0]
        if _differs(inv.unit_price, po.unit_price, pol.price_tolerance_pct):
            c.add("invoice_po_match", "invoice_po_mismatch", "medium",
                  f"Invoice {inv.invoice_number}: unit price {inv.unit_price} for {inv.item_code} differs from "
                  f"PO {inv.po_number} price {po.unit_price}.", sources=_srcs(inv, po))
        if inv.supplier_id and po.supplier_id and _k(inv.supplier_id) != _k(po.supplier_id):
            c.add("invoice_po_match", "invoice_po_mismatch", "medium",
                  f"Invoice {inv.invoice_number} is from {inv.supplier_id} but PO {inv.po_number} is with {po.supplier_id}.",
                  sources=_srcs(inv, po))
    for (po_key, item), invs in billed.items():
        qty = sum(i.qty for i in invs)
        ordered = sum(p.qty or 0 for p in pos[po_key] if _k(p.item_code) == item)
        po_rows = [p for p in pos[po_key] if _k(p.item_code) == item]
        numbers = ", ".join(i.invoice_number for i in invs)
        if any(p.qty is not None for p in po_rows) and qty > ordered * (1 + pol.qty_tolerance_pct / 100) + EPS:
            c.add("invoice_po_match", "invoice_po_mismatch", "medium",
                  f"Invoice(s) {numbers} bill {qty:g} of {item} but PO {invs[0].po_number} ordered {ordered:g}.",
                  sources=_srcs(*invs, *po_rows))
        got = received.get((po_key, item))
        if got is not None and qty > got * (1 + pol.qty_tolerance_pct / 100) + EPS:
            c.add("invoice_po_match", "invoice_po_mismatch", "medium",
                  f"Invoice(s) {numbers} bill {qty:g} of {item} but only {got:g} were received on PO {invs[0].po_number}.",
                  sources=_srcs(*invs, *receipt_rows[(po_key, item)]))
    return None


def missing_fields(c: _Ctx) -> str | None:
    if not c.inp.customer_orders:
        return "no customer_orders given"
    for o in c.inp.customer_orders:
        missing = [f for f in ("item_code", "qty", "due_date") if getattr(o, f) is None]
        if missing:
            c.add("missing_fields", "missing_field", "medium",
                  f"Customer order {o.customer_po}{' / ' + o.item_code if o.item_code else ''} has no {', '.join(missing)}.",
                  sources=_srcs(o), needs_human=True)
    return None


def order_coverage(c: _Ctx) -> str | None:
    inp, plan = c.inp, c.plan
    if not inp.customer_orders:
        return "no customer_orders given"
    tol = inp.policy.qty_tolerance_pct
    by_line: dict[tuple[str, str], list[WorkOrder]] = defaultdict(list)
    for w in plan.work_orders:
        by_line[(_k(w.customer_po), _k(w.item_code))].append(w)
    order_lines: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for o in inp.customer_orders:
        if o.item_code:
            order_lines[(_k(o.customer_po), _k(o.item_code))].append(o)
    for key, rows in order_lines.items():
        o = rows[0]
        wos = by_line.get(key, [])
        if not wos:
            c.add("order_coverage", "due_date_risk", "high",
                  f"Customer order {o.customer_po} / {o.item_code} has no work order in the plan.", sources=_srcs(*rows))
            continue
        qtys = {r.qty for r in rows if r.qty is not None}
        planned = sum(w.qty for w in wos)
        if len(qtys) == 1 and _differs(planned, qtys.pop(), tol):
            c.add("order_coverage", "conflict", "high",
                  f"Customer order {o.customer_po} / {o.item_code} is for {o.qty:g} but work orders "
                  f"{', '.join(w.wo_id for w in wos)} plan {planned:g}.",
                  wo_id=wos[0].wo_id, sources=_srcs(*rows), needs_human=False)
        dues = {r.due_date for r in rows if r.due_date}
        if len(dues) == 1:
            due = dues.pop()
            for w in wos:
                if w.due_date != due:
                    c.add("order_coverage", "conflict", "medium",
                          f"{w.wo_id} due date {w.due_date} differs from customer order {o.customer_po} due date {due}.",
                          wo_id=w.wo_id, sources=_srcs(*rows), needs_human=False)
    known = {k[0] for k in order_lines} | {_k(o.customer_po) for o in inp.customer_orders}
    for w in plan.work_orders:
        if _k(w.customer_po) not in known:
            c.add("order_coverage", "conflict", "medium",
                  f"{w.wo_id} refers to customer order {w.customer_po}, which is not in the documents.",
                  wo_id=w.wo_id, needs_human=False)
        elif (_k(w.customer_po), _k(w.item_code)) not in order_lines and order_lines:
            c.add("order_coverage", "conflict", "medium",
                  f"{w.wo_id} makes {w.item_code}, which is not on customer order {w.customer_po}.",
                  wo_id=w.wo_id, needs_human=False)
    return None


def _working_windows(cal, d: date, overtime: bool) -> tuple[datetime, datetime] | None:
    if d.weekday() not in cal.work_days or d in set(cal.holidays):
        return None
    end_t = cal.overtime_end if (overtime and cal.overtime_end and cal.overtime_end > cal.shift_end) else cal.shift_end
    return datetime.combine(d, cal.shift_start, SL_TZ), datetime.combine(d, end_t, SL_TZ)


def schedule_feasibility(c: _Ctx) -> str | None:
    inp, plan = c.inp, c.plan
    known = {_k(m) for m in inp.machines}
    by_machine: dict[str, list[tuple[datetime, datetime, str, str]]] = defaultdict(list)
    for w in plan.work_orders:
        prev = None
        for op in sorted(w.operations, key=lambda o: o.seq):
            s, e = _aware(op.start), _aware(op.end)
            label = f"{w.wo_id} seq {op.seq} ({op.operation})"
            if e <= s:
                c.add("schedule_feasibility", "capacity_overload", "high", f"{label} ends before it starts.", wo_id=w.wo_id)
            if prev is not None and s < prev:
                c.add("schedule_feasibility", "capacity_overload", "high",
                      f"{label} starts before the previous operation of {w.wo_id} ends.", wo_id=w.wo_id)
            prev = e
            if known and _k(op.machine_id) not in known:
                c.add("schedule_feasibility", "capacity_overload", "high",
                      f"{label} uses machine {op.machine_id}, which is not in the machine list.", wo_id=w.wo_id)
            if s.date() < plan.planning_start:
                c.add("schedule_feasibility", "capacity_overload", "high",
                      f"{label} starts {s:%Y-%m-%d}, before the planning start {plan.planning_start}.", wo_id=w.wo_id)
            by_machine[_k(op.machine_id)].append((s, e, label, w.wo_id))
            for d in inp.downtime:
                if _k(d.machine_id) == _k(op.machine_id) and s < _aware(d.end) and _aware(d.start) < e:
                    c.add("schedule_feasibility", "machine_down", "high",
                          f"{label} runs on {op.machine_id} during {d.reason} ({_aware(d.start):%Y-%m-%d %H:%M} "
                          f"to {_aware(d.end):%Y-%m-%d %H:%M}).", wo_id=w.wo_id, needs_human=False)
            if inp.calendar is not None:
                for t, as_end in ((s, False), (e, True)):
                    win = _working_windows(inp.calendar, t.date(), op.uses_overtime)
                    inside = win is not None and ((win[0] < t <= win[1]) if as_end else (win[0] <= t < win[1]))
                    if not inside and not (win is None and inp.policy.allow_non_working_days):
                        c.add("schedule_feasibility", "capacity_overload", "high",
                              f"{label} {'ends' if as_end else 'starts'} {t:%Y-%m-%d %H:%M}, outside working hours"
                              f"{'' if op.uses_overtime else ' (no overtime marked)'}.", wo_id=w.wo_id)
        ready = inp.material_ready.get(w.wo_id)
        if ready and w.operations:
            first = _aware(min(w.operations, key=lambda o: o.seq).start)
            if first.date() < ready:
                c.add("schedule_feasibility", "shortage", "high",
                      f"{w.wo_id} starts {first:%Y-%m-%d} but its materials are only available on {ready}.",
                      wo_id=w.wo_id, needs_human=False)
    for machine, ops in by_machine.items():
        ops.sort()
        for (s1, e1, l1, w1), (s2, e2, l2, w2) in zip(ops, ops[1:]):
            if s2 < e1:
                c.add("schedule_feasibility", "capacity_overload", "high",
                      f"Machine {machine} is double-booked: {l1} and {l2} overlap.", wo_id=w2, needs_human=False)
    return None


def overtime_policy(c: _Ctx) -> str | None:
    inp, pol = c.inp, c.inp.policy
    cal = inp.calendar
    if cal is None:
        return "no calendar given"
    if pol.max_overtime_hours_per_day is None and pol.max_overtime_hours_per_week is None and pol.allow_non_working_days:
        return "no overtime limits in policy"
    per_day: dict[tuple[str, date], float] = defaultdict(float)
    who: dict[tuple[str, date], set[str]] = defaultdict(set)
    for w in c.plan.work_orders:
        for op in w.operations:
            s, e = _aware(op.start), _aware(op.end)
            if not pol.allow_non_working_days:
                for t in (s, e):
                    if _working_windows(cal, t.date(), True) is None:
                        c.add("overtime_policy", "compliance_breach", "high",
                              f"{w.wo_id} seq {op.seq} is planned on {t:%A %Y-%m-%d}, a non-working day.",
                              wo_id=w.wo_id, needs_human=True)
                        break
            if not op.uses_overtime:
                continue
            # Overtime is the part of each working day between shift end and overtime_end, as
            # schedule_tool uses it. Without overtime_end only the last day is measurable.
            d = s.date()
            while d <= e.date():
                win = _working_windows(cal, d, False)
                if win is not None and (cal.overtime_end is not None or d == e.date()):
                    ot_end = datetime.combine(d, cal.overtime_end, SL_TZ) if cal.overtime_end else e
                    hours = (min(e, ot_end) - max(s, win[1])).total_seconds() / 3600
                    if hours > 0:
                        per_day[(_k(op.machine_id), d)] += hours
                        who[(_k(op.machine_id), d)].add(w.wo_id)
                d += timedelta(days=1)
    if pol.max_overtime_hours_per_day is not None:
        for (m, d), h in sorted(per_day.items()):
            if h > pol.max_overtime_hours_per_day + EPS:
                c.add("overtime_policy", "compliance_breach", "high",
                      f"Machine {m} has {h:.1f} h overtime on {d} (limit {pol.max_overtime_hours_per_day:g} h/day; "
                      f"{', '.join(sorted(who[(m, d)]))}).", wo_id=sorted(who[(m, d)])[0], needs_human=True)
    if pol.max_overtime_hours_per_week is not None:
        weekly: dict[tuple[str, date], float] = defaultdict(float)
        for (m, d), h in per_day.items():
            weekly[(m, d - timedelta(days=d.weekday()))] += h
        for (m, monday), h in sorted(weekly.items()):
            if h > pol.max_overtime_hours_per_week + EPS:
                c.add("overtime_policy", "compliance_breach", "high",
                      f"Machine {m} has {h:.1f} h overtime in the week of {monday} "
                      f"(limit {pol.max_overtime_hours_per_week:g} h/week).", needs_human=True)
    return None


def compliance(c: _Ctx) -> str | None:
    inp = c.inp
    if not inp.certificate_requirements:
        return "no certificate_requirements given"
    for req in inp.certificate_requirements:
        if req.item_code:
            targets = [(req.item_code, [w for w in c.plan.work_orders if _k(w.item_code) == _k(req.item_code)])]
            # A material needs the certificate by the time the products using it are made.
            users = [w for m in c.plan.material_requirements if _k(m.item_code) == _k(req.item_code)
                     for w in c.plan.work_orders if w.wo_id in m.for_work_orders]
            targets[0][1].extend(users)
        elif req.customer_po:
            wos = [w for w in c.plan.work_orders if _k(w.customer_po) == _k(req.customer_po)]
            targets = [(w.item_code, [w]) for w in wos] or [(None, [])]
        else:
            continue
        for item, wos in targets:
            certs = [x for x in inp.certificates if _k(x.certificate_type) == _k(req.certificate_type)
                     and (item is None or x.item_code is None or _k(x.item_code) == _k(item))]
            label = f"{req.certificate_type} certificate for {item or req.customer_po}"
            wo_id = wos[0].wo_id if wos else None
            if not certs:
                c.add("compliance", "compliance_breach", "high", f"No {label} was found in the documents.",
                      wo_id=wo_id, sources=_srcs(req), needs_human=True)
                continue
            needed = max((_aware(w.planned_finish).date() for w in wos), default=c.plan.planning_start)
            valid = [x for x in certs if x.valid_until is None or x.valid_until >= needed]
            if not valid:
                last = max(x.valid_until for x in certs if x.valid_until)
                c.add("compliance", "compliance_breach", "high",
                      f"The {label} expires {last}, before production finishes on {needed}.",
                      wo_id=wo_id, sources=_srcs(req, *certs), needs_human=True)
    return None


def plan_consistency(c: _Ctx) -> str | None:
    plan = c.plan
    seen: set[str] = set()
    for w in plan.work_orders:
        if w.wo_id in seen:
            c.add("plan_consistency", "conflict", "high", f"Work order id {w.wo_id} is used twice.", needs_human=False)
        seen.add(w.wo_id)
        finish = _aware(w.planned_finish)
        late = finish.date() > w.due_date
        if late != w.is_late:
            c.add("plan_consistency", "due_date_risk", "medium",
                  f"{w.wo_id} is_late is {w.is_late} but it finishes {finish:%Y-%m-%d} against due {w.due_date}.",
                  wo_id=w.wo_id, needs_human=False)
        if w.is_late != (w.days_late > 0):
            c.add("plan_consistency", "due_date_risk", "low",
                  f"{w.wo_id}: is_late {w.is_late} but days_late {w.days_late}.", wo_id=w.wo_id, needs_human=False)
        if w.operations:
            ops = sorted(w.operations, key=lambda o: o.seq)
            if _aware(w.planned_start) != _aware(ops[0].start) or finish != _aware(ops[-1].end):
                c.add("plan_consistency", "capacity_overload", "low",
                      f"{w.wo_id} planned start/finish do not match its first and last operations.",
                      wo_id=w.wo_id, needs_human=False)
        if late and not any(f.type == "due_date_risk" and w.wo_id in f.message for f in plan.flags):
            c.out.append(RuleViolation(
                rule="plan_consistency", flag_type="due_date_risk", severity="high",
                message=f"{w.wo_id} finishes {finish:%Y-%m-%d}, after its due date {w.due_date}, and the plan has no due_date_risk flag for it.",
                wo_id=w.wo_id, needs_human=True, already_flagged=False,
            ))
    shortage_items = {_k(s.item_code) for s in plan.shortages}
    for m in plan.material_requirements:
        if m.net_shortage_qty > EPS and _k(m.item_code) not in shortage_items:
            c.add("plan_consistency", "shortage", "medium",
                  f"{m.item_code} has a net shortage of {m.net_shortage_qty:g} {m.uom} but no shortage entry.",
                  needs_human=False)
    po_items = {_k(p.item_code) for p in plan.suggested_pos}

    def flag_mentions(code: str) -> bool:
        return any(
            f.type in ("shortage", "supplier_delay")
            and (_k(code) in _k(f.message) or any(_k(str(s.value)) == _k(code) for s in f.sources))
            for f in plan.flags
        )

    for s in plan.shortages:
        if _k(s.item_code) not in po_items and not flag_mentions(s.item_code):
            c.out.append(RuleViolation(
                rule="plan_consistency", flag_type="shortage", severity="high",
                message=f"Shortage of {s.shortfall_qty:g} {s.uom} {s.item_code} has neither a suggested PO nor a flag.",
                needs_human=False, already_flagged=False,
            ))
    ids: set[str] = set()
    for f in plan.flags:
        if f.flag_id in ids:
            c.add("plan_consistency", "conflict", "low", f"Flag id {f.flag_id} is used twice.", needs_human=False)
        ids.add(f.flag_id)
        if f.type in ("conflict", "compliance_breach") and not f.needs_human and not f.resolution:
            c.out.append(RuleViolation(
                rule="plan_consistency", flag_type=f.type, severity="medium",
                message=f"Flag {f.flag_id} ({f.type}) is unresolved but not marked needs_human.",
                needs_human=True, already_flagged=True,
            ))
        if not f.sources:
            c.out.append(RuleViolation(
                rule="plan_consistency", flag_type=f.type, severity="low",
                message=f"Flag {f.flag_id} cites no source document, so it cannot be traced.",
                needs_human=False, already_flagged=True,
            ))
    return None


RULES: dict[str, Callable[[_Ctx], str | None]] = {
    "document_conflicts": document_conflicts,
    "invoice_po_match": invoice_po_match,
    "missing_fields": missing_fields,
    "order_coverage": order_coverage,
    "schedule_feasibility": schedule_feasibility,
    "overtime_policy": overtime_policy,
    "compliance": compliance,
    "plan_consistency": plan_consistency,
}
_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def run_rules(inp: RuleCheckInput | dict[str, Any]) -> RuleCheckResult:
    """Run every rule (a dict is validated first)."""
    if not isinstance(inp, RuleCheckInput):
        inp = RuleCheckInput.model_validate(inp)
    ctx = _Ctx(inp)
    run, skipped = [], []
    for name, rule in RULES.items():
        why = rule(ctx)
        if why:
            skipped.append(f"{name}: {why}")
        else:
            run.append(name)
    # De-duplicate identical findings, then sort by severity (stable within a rule).
    unique, seen = [], set()
    for v in ctx.out:
        key = (v.rule, v.flag_type, v.message)
        if key not in seen:
            seen.add(key)
            unique.append(v)
    unique.sort(key=lambda v: _SEVERITY_ORDER[v.severity])
    passed = not any(v.severity == "high" and not v.already_flagged for v in unique)
    return RuleCheckResult(passed=passed, violations=unique, rules_run=run, rules_skipped=skipped)


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------

RULE_CHECK_DESCRIPTION = (
    "Deterministic checks on a draft WorkOrderPlan. Give it the plan and the facts read from the documents "
    "(customer order lines, purchase order lines, supplier invoices, goods receipts, certificate requirements "
    "and certificates, machine ids, downtime, working calendar, material-ready dates from mrp_tool, and policy "
    "limits such as maximum overtime), each with its source document where known. It returns violations shaped "
    "like plan flags (type, severity, message, sources, needs_human, already_flagged): document conflicts, "
    "invoice vs PO vs receipt mismatches, unplanned or mis-sized orders, missing fields, double-booked machines, "
    "work during downtime or outside hours, work before materials arrive, overtime and non-working-day breaches, "
    "missing or expired certificates, and plan inconsistencies. passed is true when no high-severity violation "
    "is left unflagged. Rules that lacked input are listed in rules_skipped."
)


def make_rule_check_tool() -> StructuredTool:
    def rule_check(**kwargs: Any) -> str:
        return run_rules(RuleCheckInput.model_validate(kwargs)).model_dump_json()

    return StructuredTool.from_function(
        func=rule_check,
        name="rule_check",
        description=RULE_CHECK_DESCRIPTION,
        args_schema=RuleCheckInput,
        handle_tool_error=True,
        handle_validation_error=validation_error_message,
    )
