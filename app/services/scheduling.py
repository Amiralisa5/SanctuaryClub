"""Compatibility facade for the booking service.

The booking domain now lives in :mod:`app.services.booking` (errors, queries,
validators, commands). This module re-exports the same public API so existing
imports (``from app.services import scheduling``) keep working.
"""
from .booking import (  # noqa: F401
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
    booked_count,
    bulk_book,
    cancel_booking,
    client_booking_at,
    coach_capacity,
    create_booking,
    day_slots_for_client,
    get_plan,
    get_setting_int,
    gym_capacity,
    is_modifiable,
    quota_used,
    reschedule_booking,
    section_end,
    section_start,
    set_setting,
    validate_cancel,
    validate_create,
    validate_reschedule,
    validate_slot,
)
