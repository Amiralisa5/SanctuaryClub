"""Read-only booking helpers: time math, settings, capacities, plans, quota."""
from datetime import date, datetime, time, timedelta

from sqlalchemy import extract, func, select

from ... import config, utils
from ...models import (
    Booking,
    BookingStatus,
    CapacityOverride,
    PlanMonth,
    Setting,
    TimeSection,
)


def section_start(d: date, section: TimeSection) -> datetime:
    return datetime.combine(d, time(section.start_hour))


def section_end(d: date, section: TimeSection) -> datetime:
    return datetime.combine(d, time(section.end_hour)) if section.end_hour < 24 \
        else datetime.combine(d + timedelta(days=1), time(0))


def is_modifiable(booking: Booking, at: datetime | None = None) -> bool:
    if booking.status != BookingStatus.BOOKED:
        return False
    cutoff = section_start(booking.date, booking.section) - timedelta(
        hours=config.RESCHEDULE_CUTOFF_HOURS
    )
    return (at or utils.now()) <= cutoff


def get_setting_int(db, key: str, default: int) -> int:
    row = db.get(Setting, key)
    if row is None:
        return default
    try:
        return int(row.value)
    except ValueError:
        return default


def set_setting(db, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


def gym_capacity(db, d: date, section_id: int) -> int:
    override = db.scalar(
        select(CapacityOverride).where(
            CapacityOverride.date == d,
            CapacityOverride.section_id == section_id,
            CapacityOverride.coach_id.is_(None),
        )
    )
    if override:
        return override.capacity
    return get_setting_int(db, "gym_default_capacity", config.GYM_DEFAULT_CAPACITY)


def coach_capacity(db, d: date, section_id: int, coach_id: int) -> int:
    override = db.scalar(
        select(CapacityOverride).where(
            CapacityOverride.date == d,
            CapacityOverride.section_id == section_id,
            CapacityOverride.coach_id == coach_id,
        )
    )
    if override:
        return override.capacity
    return get_setting_int(db, "coach_default_capacity", config.COACH_DEFAULT_CAPACITY)


def booked_count(db, d: date, section_id: int, coach_id: int | None = None) -> int:
    q = select(func.count(Booking.id)).where(
        Booking.date == d,
        Booking.section_id == section_id,
        Booking.status == BookingStatus.BOOKED,
    )
    if coach_id is not None:
        q = q.where(Booking.coach_id == coach_id)
    return db.scalar(q) or 0


def get_plan(db, client_id: int, year: int, month: int) -> PlanMonth | None:
    return db.scalar(
        select(PlanMonth).where(
            PlanMonth.client_id == client_id,
            PlanMonth.year == year,
            PlanMonth.month == month,
        )
    )


def quota_used(db, client_id: int, year: int, month: int) -> int:
    return db.scalar(
        select(func.count(Booking.id)).where(
            Booking.client_id == client_id,
            Booking.status == BookingStatus.BOOKED,
            extract("year", Booking.date) == year,
            extract("month", Booking.date) == month,
        )
    ) or 0


def client_booking_at(db, client_id: int, d: date, section_id: int,
                      *, exclude_booking_id: int | None = None) -> Booking | None:
    q = select(Booking).where(
        Booking.client_id == client_id,
        Booking.date == d,
        Booking.section_id == section_id,
        Booking.status == BookingStatus.BOOKED,
    )
    if exclude_booking_id is not None:
        q = q.where(Booking.id != exclude_booking_id)
    return db.scalar(q)
