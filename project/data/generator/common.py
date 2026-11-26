"""Shared helpers: working calendar, time mapping, rounding, date formats."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

# Sri Lanka public holidays that fall inside our planning horizon.
# Source: Ada Derana 2026 holiday calendar (see data/EVIDENCE.md).
HOLIDAYS = {
    date(2026, 11, 24): "Il Full Moon Poya Day",
    date(2026, 12, 23): "Unduvap Full Moon Poya Day",
    date(2026, 12, 25): "Christmas Day",
}

# One 8-hour shift, Monday to Saturday = 48 regular hours a week
# (ETI Base Code clause 6.2). Overtime max 2 h/day -> 60 h/week (clause 6.4).
REG_MIN = 480
OT_MIN_POLICY = 120
DAY_START_H = 8
LUNCH_AFTER_MIN = 270  # 08:00 + 4h30 = 12:30, lunch 12:30-13:30
LUNCH_MIN = 60
TZ = "+05:30"


def is_working_day(d: date) -> bool:
    return d.weekday() < 6 and d not in HOLIDAYS


def working_days(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if is_working_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def add_wd(d: date, n: int) -> date:
    """Move n working days forward (n>=0) or back (n<0)."""
    step = 1 if n >= 0 else -1
    k = abs(n)
    while k:
        d += timedelta(days=step)
        if is_working_day(d):
            k -= 1
    return d


def next_wd(d: date) -> date:
    while not is_working_day(d):
        d += timedelta(days=1)
    return d


def wd_between(a: date, b: date) -> int:
    """Working days after a up to and including b (0 if b<=a)."""
    if b <= a:
        return 0
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        if is_working_day(d):
            n += 1
    return n


def minute_to_clock(day: date, m: float) -> datetime:
    """Working minute m of a day -> wall clock (skips the lunch hour)."""
    base = datetime(day.year, day.month, day.day, DAY_START_H, 0)
    extra = LUNCH_MIN if m > LUNCH_AFTER_MIN else 0
    return base + timedelta(minutes=round(m) + extra)


def iso_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + TZ


def ceil_to(x: float, step: float) -> float:
    return math.ceil(round(x / step, 6)) * step


def qty_round(x: float, uom: str) -> float:
    """Requirement rounding: whole units for countable UoMs, 0.01 otherwise."""
    if uom in ("pcs", "cone", "set", "box", "roll"):
        return float(math.ceil(round(x, 6)))
    return round(math.ceil(round(x * 100, 6)) / 100, 2)


def fmt_date(d: date, style: str) -> str:
    if style == "iso":
        return d.isoformat()
    if style == "dmy":
        return d.strftime("%d/%m/%Y")
    if style == "dmy_dot":
        return d.strftime("%d.%m.%Y")
    if style == "tally":  # Tally style: 1-Dec-26
        return f"{d.day}-{d.strftime('%b')}-{d.strftime('%y')}"
    if style == "long":
        return f"{d.day} {d.strftime('%B %Y')}"
    if style == "mdy":
        return d.strftime("%m/%d/%Y")
    raise ValueError(style)


def money(x: float) -> str:
    return f"{x:,.2f}"
