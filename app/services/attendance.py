from datetime import timedelta

from sqlalchemy import extract, func, select

from .. import config, utils
from ..audit import log_action
from ..models import (
    Attendance,
    AttendanceStatus,
    Booking,
    BookingStatus,
    User,
)
from .scheduling import section_end, section_start


class AttendanceError(Exception):
    """An attendance rule was violated; the message is safe to show the user."""


def checkin_window(booking: Booking) -> tuple:
    start = section_start(booking.date, booking.section)
    return (
        start - timedelta(minutes=config.CHECKIN_MINUTES_BEFORE),
        start + timedelta(minutes=config.CHECKIN_MINUTES_AFTER),
    )


def can_check_in(booking: Booking) -> bool:
    if booking.status != BookingStatus.BOOKED or booking.attendance is not None:
        return False
    opens, closes = checkin_window(booking)
    return opens <= utils.now() <= closes


def _existing_attendance(db, booking: Booking) -> Attendance | None:
    # Query directly rather than trusting the relationship cache, which can be
    # stale on a session with expire_on_commit=False.
    return db.scalar(select(Attendance).where(Attendance.booking_id == booking.id))


def check_in(db, booking: Booking, actor: User, weight_kg: float | None = None,
             rpe: int | None = None, completion_pct: int | None = None,
             notes: str = "") -> Attendance:
    if booking.status != BookingStatus.BOOKED:
        raise AttendanceError("This booking is not active.")
    if _existing_attendance(db, booking) is not None:
        raise AttendanceError("Attendance is already recorded for this session.")
    opens, closes = checkin_window(booking)
    if not (opens <= utils.now() <= closes):
        raise AttendanceError(
            f"Check-in is open from {opens.strftime('%H:%M')} to {closes.strftime('%H:%M')} on {booking.date}."
        )
    if rpe is not None and not (1 <= rpe <= 10):
        raise AttendanceError("RPE must be between 1 and 10.")
    if completion_pct is not None and not (0 <= completion_pct <= 100):
        raise AttendanceError("Completion must be between 0 and 100 percent.")

    attendance = Attendance(
        booking_id=booking.id,
        status=AttendanceStatus.PRESENT,
        auto=False,
        marked_at=utils.now(),
        weight_kg=weight_kg,
        rpe=rpe,
        completion_pct=completion_pct,
        notes=notes or "",
    )
    db.add(attendance)
    db.flush()
    log_action(db, actor, "attendance.checkin", "booking", booking.id,
               f"client={booking.client_id} {booking.date} {booking.section.label}")
    db.commit()
    return attendance


def set_attendance(db, booking: Booking, status: AttendanceStatus, actor: User) -> Attendance:
    """Manual coach/admin override; allowed at any time."""
    attendance = _existing_attendance(db, booking)
    if attendance is None:
        attendance = Attendance(booking_id=booking.id, status=status, auto=False,
                                marked_at=utils.now())
        db.add(attendance)
    else:
        attendance.status = status
        attendance.auto = False
        attendance.marked_at = utils.now()
    db.flush()
    log_action(db, actor, "attendance.set", "booking", booking.id,
               f"client={booking.client_id} status={status.value}")
    db.commit()
    return attendance


def auto_mark_absent(db) -> int:
    """Mark BOOKED sessions with no attendance as ABSENT once the grace period after
    section end has passed. Run by the background scheduler every 10 minutes."""
    current = utils.now()
    candidates = db.scalars(
        select(Booking)
        .outerjoin(Attendance)
        .where(
            Booking.status == BookingStatus.BOOKED,
            Booking.date <= current.date(),
            Attendance.id.is_(None),
        )
    ).all()

    from . import notifications

    marked = 0
    for booking in candidates:
        deadline = section_end(booking.date, booking.section) + timedelta(
            minutes=config.AUTO_ABSENT_MINUTES_AFTER_END
        )
        if current >= deadline:
            db.add(Attendance(booking_id=booking.id, status=AttendanceStatus.ABSENT,
                              auto=True, marked_at=current))
            log_action(db, None, "attendance.auto_absent", "booking", booking.id,
                       f"client={booking.client_id} {booking.date} {booking.section.label}")
            notifications.notify_auto_absent(db, booking)
            marked += 1
    if marked:
        db.commit()
    return marked


def monthly_summary(db, client_id: int, year: int, month: int) -> dict:
    """Aggregate attendance for a client's month."""
    base = (
        select(Attendance.status, func.count(Attendance.id))
        .join(Booking)
        .where(
            Booking.client_id == client_id,
            extract("year", Booking.date) == year,
            extract("month", Booking.date) == month,
        )
        .group_by(Attendance.status)
    )
    counts = {status: 0 for status in AttendanceStatus}
    for status, count in db.execute(base):
        counts[status] = count

    booked = db.scalar(
        select(func.count(Booking.id)).where(
            Booking.client_id == client_id,
            Booking.status == BookingStatus.BOOKED,
            extract("year", Booking.date) == year,
            extract("month", Booking.date) == month,
        )
    ) or 0

    return {
        "booked": booked,
        "present": counts[AttendanceStatus.PRESENT],
        "absent": counts[AttendanceStatus.ABSENT],
        "excused": counts[AttendanceStatus.EXCUSED],
        "pending": booked - sum(counts.values()),
    }
