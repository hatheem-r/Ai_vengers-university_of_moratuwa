"""Working-calendar helpers shared by mrp_tool, schedule_tool and rule_check.

All of them take a `PlanningCalendar` (core/schemas.py): working weekdays (0 = Monday),
holidays, and the shift start time. Nothing here knows about a specific factory.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

from core.schemas import PlanningCalendar

COUNTABLE_UOMS = {"pcs", "pc", "cone", "cones", "set", "sets", "box", "boxes", "roll", "rolls", "carton",
                  "ctn", "pair", "nos", "no", "ea", "each", "unit", "units"}


def is_working_day(d: date, cal: PlanningCalendar) -> bool:
    return d.weekday() in cal.working_weekdays and d not in set(cal.holidays)


def first_working_day(d: date, cal: PlanningCalendar) -> date:
    """d itself if it is a working day, else the next one."""
    for _ in range(366):
        if is_working_day(d, cal):
            return d
        d += timedelta(days=1)
    raise ValueError("calendar has no working day within a year")


def add_working_days(d: date, n: int, cal: PlanningCalendar) -> date:
    """Move n >= 0 working days forward from d (n = 0 returns d unchanged)."""
    if n < 0:
        raise ValueError("n must be >= 0")
    while n:
        d += timedelta(days=1)
        if is_working_day(d, cal):
            n -= 1
    return d


def working_days_after(a: date, b: date, cal: PlanningCalendar) -> int:
    """Working days after a, up to and including b (0 when b <= a). This is how
    `days_late` is counted: finish date vs due date."""
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        n += is_working_day(d, cal)
    return n


def working_days_list(start: date, n: int, cal: PlanningCalendar) -> list[date]:
    """The first n working days on or after start."""
    out, d = [], start
    while len(out) < n:
        if is_working_day(d, cal):
            out.append(d)
        d += timedelta(days=1)
    return out


def round_requirement(qty: float, uom: str) -> float:
    """Whole units for countable UoMs (rounded up), else rounded up to 0.01."""
    if uom.strip().lower() in COUNTABLE_UOMS:
        return float(math.ceil(round(qty, 6)))
    return round(math.ceil(round(qty * 100, 6)) / 100, 2)
