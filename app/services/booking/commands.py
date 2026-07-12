"""Booking write use cases: create, cancel, reschedule, bulk book."""
from datetime import date

from sqlalchemy import select

from ... import utils
from ...audit import log_action
from ...models import Booking, BookingStatus, Client, TimeSection, User
from . import validators
from .errors import BookingError


def create_booking(db, client: Client, d: date, section: TimeSection, actor: User,
                   *, commit: bool = True) -> Booking:
    from .. import notifications

    validators.validate_create(db, client, d, section)
    booking = Booking(client_id=client.id, coach_id=client.coach_id, date=d,
                      section_id=section.id, created_at=utils.now())
    db.add(booking)
    db.flush()
    log_action(db, actor, "booking.create", "booking", booking.id,
               f"client={client.id} {d} {section.label}")
    notifications.notify_booking_created(db, booking, actor)
    if commit:
        db.commit()
    return booking


def cancel_booking(db, booking: Booking, actor: User, *, commit: bool = True) -> None:
    from .. import notifications

    validators.validate_cancel(booking)
    booking.status = BookingStatus.CANCELLED
    log_action(db, actor, "booking.cancel", "booking", booking.id,
               f"client={booking.client_id} {booking.date} {booking.section.label}")
    notifications.notify_booking_cancelled(db, booking, actor)
    if commit:
        db.commit()


def reschedule_booking(db, booking: Booking, new_date: date, new_section: TimeSection,
                       actor: User, *, commit: bool = True) -> None:
    from .. import notifications

    validators.validate_reschedule(db, booking, new_date, new_section)
    old = f"{booking.date.strftime('%A %Y-%m-%d')} {booking.section.label}"
    booking.date = new_date
    booking.section_id = new_section.id
    log_action(db, actor, "booking.reschedule", "booking", booking.id,
               f"client={booking.client_id} {old} -> {new_date} {new_section.label}")
    notifications.notify_booking_rescheduled(db, booking, old, actor)
    if commit:
        db.commit()


def bulk_book(db, client: Client, year: int, month: int, weekdays: set[int],
              section: TimeSection, actor: User) -> list[tuple[date, str]]:
    """Book every future date in the month falling on the given weekdays.

    Returns a (date, outcome) list; outcome is 'booked' or the rule violation message.
    """
    results: list[tuple[date, str]] = []
    pending = False
    for d in utils.month_dates(year, month):
        if d.weekday() not in weekdays:
            continue
        try:
            create_booking(db, client, d, section, actor, commit=False)
            pending = True
            results.append((d, "booked"))
        except BookingError as exc:
            results.append((d, str(exc)))
    if pending:
        db.commit()
    return results


def day_slots_for_client(db, client: Client, d: date, sections: list[TimeSection],
                         *, exclude_booking_id: int | None = None) -> list[dict]:
    """Per-section availability for a client on one day (for booking UI)."""
    owned = {
        b.section_id: b
        for b in db.scalars(
            select(Booking)
            .where(
                Booking.client_id == client.id,
                Booking.date == d,
                Booking.status == BookingStatus.BOOKED,
            )
        ).all()
    }
    slots = []
    for section in sections:
        owned_booking = owned.get(section.id)
        if owned_booking and (
            exclude_booking_id is None or owned_booking.id != exclude_booking_id
        ):
            slots.append({
                "section": section,
                "state": "yours",
                "reason": "You already have this slot booked.",
                "booking": owned_booking,
            })
            continue
        reason = validators.validate_slot(
            db, client, d, section, exclude_booking_id=exclude_booking_id
        )
        slots.append({
            "section": section,
            "state": "open" if reason is None else "closed",
            "reason": reason or "",
            "booking": None,
        })
    return slots
