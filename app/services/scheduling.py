from datetime import date, datetime, time, timedelta

from sqlalchemy import extract, func, select

from .. import config, utils
from ..audit import log_action
from ..models import (
    Booking,
    BookingStatus,
    CapacityOverride,
    Client,
    PlanMonth,
    Setting,
    TimeSection,
    User,
)


class BookingError(Exception):
    """A booking rule was violated; the message is safe to show the user."""


def section_start(d: date, section: TimeSection) -> datetime:
    return datetime.combine(d, time(section.start_hour))


def section_end(d: date, section: TimeSection) -> datetime:
    return datetime.combine(d, time(section.end_hour)) if section.end_hour < 24 \
        else datetime.combine(d + timedelta(days=1), time(0))


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


def _check_slot(db, client: Client, d: date, section: TimeSection,
                check_quota: bool = True) -> None:
    """Validate every booking rule for the given slot; raise BookingError on violation."""
    if client.coach_id is None:
        raise BookingError("You don't have a coach yet — an admin will assign one shortly.")
    if section_start(d, section) <= utils.now():
        raise BookingError("Cannot book a session in the past.")

    duplicate = db.scalar(
        select(Booking).where(
            Booking.client_id == client.id,
            Booking.date == d,
            Booking.section_id == section.id,
            Booking.status == BookingStatus.BOOKED,
        )
    )
    if duplicate:
        raise BookingError(f"Already booked for {d} {section.label}.")

    if check_quota:
        plan = get_plan(db, client.id, d.year, d.month)
        if plan is None:
            raise BookingError(f"No active plan for {d.year}-{d.month:02d}. Ask your coach to set one.")
        if plan.quota is not None and quota_used(db, client.id, d.year, d.month) >= plan.quota:
            raise BookingError(f"Monthly quota of {plan.quota} sessions reached for {d.year}-{d.month:02d}.")

    if booked_count(db, d, section.id) >= gym_capacity(db, d, section.id):
        raise BookingError(f"The gym is full for {d} {section.label}.")

    if booked_count(db, d, section.id, client.coach_id) >= coach_capacity(db, d, section.id, client.coach_id):
        raise BookingError(f"Your coach's slot is full for {d} {section.label}.")


def create_booking(db, client: Client, d: date, section: TimeSection, actor: User) -> Booking:
    from . import notifications

    _check_slot(db, client, d, section)
    booking = Booking(client_id=client.id, coach_id=client.coach_id, date=d,
                      section_id=section.id, created_at=utils.now())
    db.add(booking)
    db.flush()
    log_action(db, actor, "booking.create", "booking", booking.id,
               f"client={client.id} {d} {section.label}")
    notifications.notify_booking_created(db, booking)
    db.commit()
    return booking


def _assert_modifiable(booking: Booking) -> None:
    if booking.status != BookingStatus.BOOKED:
        raise BookingError("This booking is not active.")
    cutoff = section_start(booking.date, booking.section) - timedelta(hours=config.RESCHEDULE_CUTOFF_HOURS)
    if utils.now() > cutoff:
        raise BookingError(
            f"Changes are only allowed up to {config.RESCHEDULE_CUTOFF_HOURS} hours before the session starts."
        )


def cancel_booking(db, booking: Booking, actor: User) -> None:
    from . import notifications

    _assert_modifiable(booking)
    booking.status = BookingStatus.CANCELLED
    log_action(db, actor, "booking.cancel", "booking", booking.id,
               f"client={booking.client_id} {booking.date} {booking.section.label}")
    notifications.notify_booking_cancelled(db, booking)
    db.commit()


def reschedule_booking(db, booking: Booking, new_date: date, new_section: TimeSection, actor: User) -> None:
    _assert_modifiable(booking)
    same_month = (booking.date.year, booking.date.month) == (new_date.year, new_date.month)
    # Quota only needs re-checking when the booking moves into a different month.
    _check_slot(db, booking.client, new_date, new_section, check_quota=not same_month)
    from . import notifications

    old = f"{booking.date.strftime('%A %Y-%m-%d')} {booking.section.label}"
    booking.date = new_date
    booking.section_id = new_section.id
    log_action(db, actor, "booking.reschedule", "booking", booking.id,
               f"client={booking.client_id} {old} -> {new_date} {new_section.label}")
    notifications.notify_booking_rescheduled(db, booking, old)
    db.commit()


def bulk_book(db, client: Client, year: int, month: int, weekdays: set[int],
              section: TimeSection, actor: User) -> list[tuple[date, str]]:
    """Book every future date in the month falling on the given weekdays (Python weekday numbers).

    Returns a (date, outcome) list; outcome is 'booked' or the rule violation message.
    """
    results: list[tuple[date, str]] = []
    for d in utils.month_dates(year, month):
        if d.weekday() not in weekdays:
            continue
        try:
            create_booking(db, client, d, section, actor)
            results.append((d, "booked"))
        except BookingError as exc:
            results.append((d, str(exc)))
    return results
