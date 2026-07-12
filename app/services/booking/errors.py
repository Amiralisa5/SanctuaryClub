"""Typed booking errors.

Every rule violation raises a dedicated ``BookingError`` subclass carrying a
stable machine-readable ``code`` plus a user-safe message. Routers can keep
catching the ``BookingError`` base class, while tests/APIs may branch on the
specific subclass or ``code``.
"""


class BookingError(Exception):
    """A booking rule was violated; the message is safe to show the user."""

    code = "booking_error"

    def __init__(self, message: str | None = None):
        super().__init__(message or self.default_message())

    @classmethod
    def default_message(cls) -> str:
        return "This booking action is not allowed."


class NoCoachAssignedError(BookingError):
    code = "no_coach"

    @classmethod
    def default_message(cls) -> str:
        return "You don't have a coach yet — an admin will assign one shortly."


class PastSlotError(BookingError):
    code = "past_slot"

    @classmethod
    def default_message(cls) -> str:
        return "Cannot book a session in the past."


class DuplicateBookingError(BookingError):
    code = "duplicate_booking"


class NoPlanError(BookingError):
    code = "no_plan"


class QuotaExceededError(BookingError):
    code = "quota_exceeded"


class GymFullError(BookingError):
    code = "gym_full"


class CoachFullError(BookingError):
    code = "coach_full"


class BookingNotActiveError(BookingError):
    code = "not_active"

    @classmethod
    def default_message(cls) -> str:
        return "This booking is not active."


class CutoffPassedError(BookingError):
    code = "cutoff_passed"


class SameSlotError(BookingError):
    code = "same_slot"

    @classmethod
    def default_message(cls) -> str:
        return "Pick a different date or time slot to reschedule."
