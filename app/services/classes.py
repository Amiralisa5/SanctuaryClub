"""Class monitoring: every date+section with bookings is a 'class' (group session).

Provides the month overview (per-day, per-section counts with attendance
breakdown), week summaries, and the per-day roster drilldown.
"""
from datetime import date, timedelta

from sqlalchemy import case, func, select

from ..models import (
    Attendance,
    AttendanceStatus,
    Booking,
    BookingStatus,
    TimeSection,
)
from ..utils import month_dates


def _counts_query(db, first: date, last: date, coach_id: int | None):
    present = func.sum(case((Attendance.status == AttendanceStatus.PRESENT, 1), else_=0))
    absent = func.sum(case((Attendance.status == AttendanceStatus.ABSENT, 1), else_=0))
    excused = func.sum(case((Attendance.status == AttendanceStatus.EXCUSED, 1), else_=0))
    stmt = (
        select(Booking.date, Booking.section_id, func.count(Booking.id),
               present, absent, excused)
        .outerjoin(Attendance, Attendance.booking_id == Booking.id)
        .where(Booking.date >= first, Booking.date <= last,
               Booking.status == BookingStatus.BOOKED)
        .group_by(Booking.date, Booking.section_id)
    )
    if coach_id is not None:
        stmt = stmt.where(Booking.coach_id == coach_id)
    return db.execute(stmt).all()


def month_overview(db, year: int, month: int, coach_id: int | None = None) -> dict:
    """Returns {'days': [...], 'weeks': [...], 'sections': [...], 'totals': {...}}."""
    days = month_dates(year, month)
    sections = db.scalars(select(TimeSection).order_by(TimeSection.index)).all()
    cells = {
        (row[0], row[1]): {"booked": row[2], "present": row[3] or 0,
                           "absent": row[4] or 0, "excused": row[5] or 0}
        for row in _counts_query(db, days[0], days[-1], coach_id)
    }

    day_rows = []
    for d in days:
        row = {"date": d, "cells": [cells.get((d, s.id)) for s in sections]}
        row["booked"] = sum(c["booked"] for c in row["cells"] if c)
        row["classes"] = sum(1 for c in row["cells"] if c)
        day_rows.append(row)

    weeks = []
    current_week: list = []
    for row in day_rows:
        current_week.append(row)
        # Gym weeks end on Friday (weekday 4)
        if row["date"].weekday() == 4 or row["date"] == days[-1]:
            weeks.append(_summarize_week(current_week))
            current_week = []

    totals = _summarize_week(day_rows)
    totals.pop("start", None)
    totals.pop("end", None)
    return {"days": day_rows, "weeks": weeks, "sections": sections, "totals": totals}


def _summarize_week(rows: list) -> dict:
    booked = present = absent = excused = classes = 0
    for row in rows:
        classes += row["classes"]
        for cell in row["cells"]:
            if cell:
                booked += cell["booked"]
                present += cell["present"]
                absent += cell["absent"]
                excused += cell["excused"]
    marked = present + absent + excused
    return {
        "start": rows[0]["date"], "end": rows[-1]["date"],
        "classes": classes, "booked": booked,
        "present": present, "absent": absent, "excused": excused,
        "rate": round(present / marked * 100) if marked else None,
    }


def day_roster(db, d: date, coach_id: int | None = None) -> list[dict]:
    """Each class on the given day with its full participant roster."""
    stmt = (
        select(Booking)
        .join(TimeSection, Booking.section_id == TimeSection.id)
        .where(Booking.date == d, Booking.status == BookingStatus.BOOKED)
        .order_by(TimeSection.index, Booking.id)
    )
    if coach_id is not None:
        stmt = stmt.where(Booking.coach_id == coach_id)
    bookings = db.scalars(stmt).all()

    by_section: dict = {}
    for booking in bookings:
        by_section.setdefault(booking.section_id, {"section": booking.section,
                                                   "bookings": []})["bookings"].append(booking)
    out = []
    for entry in sorted(by_section.values(), key=lambda e: e["section"].index):
        counts = {"present": 0, "absent": 0, "excused": 0, "pending": 0}
        for booking in entry["bookings"]:
            if booking.attendance is None:
                counts["pending"] += 1
            else:
                counts[booking.attendance.status.value.lower()] += 1
        entry["counts"] = counts
        out.append(entry)
    return out
