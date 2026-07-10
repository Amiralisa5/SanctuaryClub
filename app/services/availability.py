"""Coach availability: slot blocking and the shared client-facing calendar."""
from datetime import date

from sqlalchemy import func, select

from .. import config
from ..audit import log_action
from ..models import Booking, BookingStatus, CapacityOverride, Coach, TimeSection, User
from ..utils import month_dates
from .scheduling import get_setting_int


def block_slot(db, coach: Coach, d: date, section_id: int, actor: User) -> None:
    override = db.scalar(select(CapacityOverride).where(
        CapacityOverride.date == d,
        CapacityOverride.section_id == section_id,
        CapacityOverride.coach_id == coach.id,
    ))
    if override is None:
        override = CapacityOverride(date=d, section_id=section_id,
                                    coach_id=coach.id, capacity=0)
        db.add(override)
    else:
        override.capacity = 0
    log_action(db, actor, "availability.block", detail=f"{d} section={section_id}")
    db.commit()


def unblock_slot(db, coach: Coach, d: date, section_id: int, actor: User) -> None:
    override = db.scalar(select(CapacityOverride).where(
        CapacityOverride.date == d,
        CapacityOverride.section_id == section_id,
        CapacityOverride.coach_id == coach.id,
    ))
    if override is not None:
        db.delete(override)
        log_action(db, actor, "availability.unblock", detail=f"{d} section={section_id}")
        db.commit()


def month_availability(db, coach: Coach, year: int, month: int) -> dict:
    """Per-day, per-section availability for one coach's calendar.

    Status per slot: 'blocked' (coach capacity 0), 'full' (no spots left),
    'limited' (1-2 left) or 'open', with the number of remaining spots.
    Computed with three grouped queries, not per-cell lookups.
    """
    days = month_dates(year, month)
    first, last = days[0], days[-1]
    sections = db.scalars(select(TimeSection).order_by(TimeSection.index)).all()

    default_gym = get_setting_int(db, "gym_default_capacity", config.GYM_DEFAULT_CAPACITY)
    default_coach = get_setting_int(db, "coach_default_capacity", config.COACH_DEFAULT_CAPACITY)

    overrides = db.scalars(select(CapacityOverride).where(
        CapacityOverride.date >= first, CapacityOverride.date <= last)).all()
    gym_over = {(o.date, o.section_id): o.capacity for o in overrides if o.coach_id is None}
    coach_over = {(o.date, o.section_id): o.capacity for o in overrides
                  if o.coach_id == coach.id}

    counts = db.execute(
        select(Booking.date, Booking.section_id, Booking.coach_id, func.count(Booking.id))
        .where(Booking.date >= first, Booking.date <= last,
               Booking.status == BookingStatus.BOOKED)
        .group_by(Booking.date, Booking.section_id, Booking.coach_id)
    ).all()
    gym_booked: dict = {}
    coach_booked: dict = {}
    for booking_date, section_id, coach_id, count in counts:
        gym_booked[(booking_date, section_id)] = gym_booked.get((booking_date, section_id), 0) + count
        if coach_id == coach.id:
            coach_booked[(booking_date, section_id)] = count

    grid: dict = {}
    for d in days:
        slots = []
        for section in sections:
            coach_cap = coach_over.get((d, section.id), default_coach)
            gym_cap = gym_over.get((d, section.id), default_gym)
            left = min(coach_cap - coach_booked.get((d, section.id), 0),
                       gym_cap - gym_booked.get((d, section.id), 0))
            if coach_cap == 0:
                status = "blocked"
            elif left <= 0:
                status = "full"
            elif left <= 2:
                status = "limited"
            else:
                status = "open"
            slots.append({"section": section, "status": status, "left": max(left, 0),
                          "booked": coach_booked.get((d, section.id), 0)})
        grid[d] = slots
    return grid
