"""Booking validators.

Atomic rule validators each check one business rule and raise a typed
``BookingError`` subclass on violation. Use-case pipelines compose them:

- ``validate_create``      — new booking (client or coach acting for a client)
- ``validate_cancel``      — cancelling an active booking
- ``validate_reschedule``  — moving an active booking to another slot
"""
from datetime import date

from ... import config, utils
from ...models import Booking, BookingStatus, Client, TimeSection
from . import queries
from .errors import (
    BookingError,
    BookingNotActiveError,
    CoachFullError,
    CutoffPassedError,
    DuplicateBookingError,
    GymFullError,
    NoCoachAssignedError,
    NoPlanError,
    PastSlotError,
    QuotaExceededError,
    SameSlotError,
)


def _slot_label(d: date, section: TimeSection) -> str:
    return f"{d.strftime('%A %b %-d')} · {section.label}"


# --- atomic rule validators -------------------------------------------------

def validate_has_coach(client: Client) -> None:
    if client.coach_id is None:
        raise NoCoachAssignedError()


def validate_not_past(d: date, section: TimeSection) -> None:
    if queries.section_start(d, section) <= utils.now():
        raise PastSlotError()


def validate_no_duplicate(db, client_id: int, d: date, section: TimeSection,
                          *, exclude_booking_id: int | None = None) -> None:
    if queries.client_booking_at(db, client_id, d, section.id,
                                 exclude_booking_id=exclude_booking_id):
        raise DuplicateBookingError(f"Already booked for {_slot_label(d, section)}.")


def validate_quota(db, client_id: int, d: date) -> None:
    plan = queries.get_plan(db, client_id, d.year, d.month)
    if plan is None:
        raise NoPlanError(
            f"No active plan for {d.year}-{d.month:02d}. Ask your coach to set one."
        )
    if plan.quota is not None and queries.quota_used(
        db, client_id, d.year, d.month
    ) >= plan.quota:
        raise QuotaExceededError(
            f"Monthly quota of {plan.quota} sessions reached for {d.year}-{d.month:02d}."
        )


def validate_gym_capacity(db, d: date, section: TimeSection) -> None:
    if queries.booked_count(db, d, section.id) >= queries.gym_capacity(db, d, section.id):
        raise GymFullError(f"The gym is full for {_slot_label(d, section)}.")


def validate_coach_capacity(db, d: date, section: TimeSection, coach_id: int) -> None:
    if queries.booked_count(db, d, section.id, coach_id) >= queries.coach_capacity(
        db, d, section.id, coach_id
    ):
        raise CoachFullError(f"Your coach's slot is full for {_slot_label(d, section)}.")


def validate_modifiable(booking: Booking) -> None:
    if not queries.is_modifiable(booking):
        if booking.status != BookingStatus.BOOKED:
            raise BookingNotActiveError()
        raise CutoffPassedError(
            f"Changes are only allowed up to {config.RESCHEDULE_CUTOFF_HOURS} hours "
            f"before the session starts."
        )


def validate_different_slot(booking: Booking, new_date: date,
                            new_section: TimeSection) -> None:
    if booking.date == new_date and booking.section_id == new_section.id:
        raise SameSlotError()


# --- use-case pipelines ------------------------------------------------------

def validate_create(db, client: Client, d: date, section: TimeSection,
                    *, check_quota: bool = True,
                    exclude_booking_id: int | None = None) -> None:
    """Validate every rule for booking the given slot; raise BookingError."""
    validate_has_coach(client)
    validate_not_past(d, section)
    validate_no_duplicate(db, client.id, d, section,
                          exclude_booking_id=exclude_booking_id)
    if check_quota:
        validate_quota(db, client.id, d)
    validate_gym_capacity(db, d, section)
    validate_coach_capacity(db, d, section, client.coach_id)


def validate_cancel(booking: Booking) -> None:
    """Validate that an existing booking may be cancelled."""
    validate_modifiable(booking)


def validate_reschedule(db, booking: Booking, new_date: date,
                        new_section: TimeSection) -> None:
    """Validate that an existing booking may be moved to the new slot."""
    validate_modifiable(booking)
    validate_different_slot(booking, new_date, new_section)
    same_month = (booking.date.year, booking.date.month) == \
        (new_date.year, new_date.month)
    validate_create(
        db, booking.client, new_date, new_section,
        check_quota=not same_month,
        exclude_booking_id=booking.id,
    )


def validate_slot(db, client: Client, d: date, section: TimeSection,
                  *, check_quota: bool = True,
                  exclude_booking_id: int | None = None) -> str | None:
    """Return a user-safe error message, or None when the slot can be booked."""
    try:
        validate_create(db, client, d, section, check_quota=check_quota,
                        exclude_booking_id=exclude_booking_id)
        return None
    except BookingError as exc:
        return str(exc)
