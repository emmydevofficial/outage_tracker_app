"""Reporting periods for the SLA outage reports: weeks run Sunday to
Saturday, cut at month boundaries -- the first week of a month starts on
the 1st (even if that's mid-week) and the last week ends on the last day
of the month (even if that's mid-week), rather than spilling into the
neighbouring month.
"""
from __future__ import annotations

import calendar
import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True)
class Period:
    start: dt.date
    end: dt.date  # inclusive
    label: str

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


def week_periods(year: int, month: int) -> list[Period]:
    """Every Sunday-Saturday week touching this month, clipped to the
    month's own start/end. For September 2026: Week 1 is 1-5 (partial,
    since 6 Sept is the first Sunday inside the month), Week 2 is 6-12,
    Week 3 is 13-19, ... the last week is clipped the same way at the
    month's last day."""
    first_day = dt.date(year, month, 1)
    last_day = dt.date(year, month, calendar.monthrange(year, month)[1])

    weeks = []
    cursor = first_day
    n = 1
    while cursor <= last_day:
        # Python weekday(): Monday=0 ... Sunday=6. Saturday=5.
        days_to_saturday = (5 - cursor.weekday()) % 7
        week_end = min(cursor + dt.timedelta(days=days_to_saturday), last_day)
        weeks.append(Period(cursor, week_end, f"Week {n}"))
        cursor = week_end + dt.timedelta(days=1)
        n += 1
    return weeks


def month_period(year: int, month: int) -> Period:
    first_day = dt.date(year, month, 1)
    last_day = dt.date(year, month, calendar.monthrange(year, month)[1])
    return Period(first_day, last_day, f"{calendar.month_name[month]} {year}")


def month_to_date(end_date: dt.date) -> Period:
    """One allocation for every day so far in end_date's month -- never
    the sum of the weekly periods, per the spec's month-to-date rule."""
    first_day = end_date.replace(day=1)
    return Period(first_day, end_date, f"{calendar.month_name[end_date.month]} {end_date.year} (to date)")
