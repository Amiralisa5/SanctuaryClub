"""Booking service package.

Layers:
- ``errors``      — typed ``BookingError`` subclasses with stable codes
- ``queries``     — read-only helpers (time math, capacity, plans, quota)
- ``validators``  — atomic rules plus per-use-case pipelines
- ``commands``    — write use cases (create/cancel/reschedule/bulk)
"""
from .commands import (
    bulk_book,
    cancel_booking,
    create_booking,
    day_slots_for_client,
    reschedule_booking,
)
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
from .queries import (
    booked_count,
    client_booking_at,
    coach_capacity,
    get_plan,
    get_setting_int,
    gym_capacity,
    is_modifiable,
    quota_used,
    section_end,
    section_start,
    set_setting,
)
from .validators import (
    validate_cancel,
    validate_create,
    validate_reschedule,
    validate_slot,
)

__all__ = [
    # errors
    "BookingError",
    "BookingNotActiveError",
    "CoachFullError",
    "CutoffPassedError",
    "DuplicateBookingError",
    "GymFullError",
    "NoCoachAssignedError",
    "NoPlanError",
    "PastSlotError",
    "QuotaExceededError",
    "SameSlotError",
    # queries
    "booked_count",
    "client_booking_at",
    "coach_capacity",
    "get_plan",
    "get_setting_int",
    "gym_capacity",
    "is_modifiable",
    "quota_used",
    "section_end",
    "section_start",
    "set_setting",
    # validators
    "validate_cancel",
    "validate_create",
    "validate_reschedule",
    "validate_slot",
    # commands
    "bulk_book",
    "cancel_booking",
    "create_booking",
    "day_slots_for_client",
    "reschedule_booking",
]
