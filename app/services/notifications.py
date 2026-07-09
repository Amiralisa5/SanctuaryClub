import logging
import smtplib
from email.message import EmailMessage

from .. import config
from ..models import Booking, EmailLog, ProgramWeek
from ..utils import now

logger = logging.getLogger("sanctuaryclub.email")


def send_email(db, to_email: str, subject: str, body: str) -> EmailLog:
    """Queue/send a notification. Uses SMTP when configured, otherwise logs to the
    console and records the message in the email log. The caller commits."""
    log = EmailLog(to_email=to_email, subject=subject, body=body, created_at=now())
    if config.SMTP_HOST:
        log.backend = "smtp"
        try:
            message = EmailMessage()
            message["From"] = config.EMAIL_FROM
            message["To"] = to_email
            message["Subject"] = subject
            message.set_content(body)
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as smtp:
                if config.SMTP_STARTTLS:
                    smtp.starttls()
                if config.SMTP_USER:
                    smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
                smtp.send_message(message)
            log.sent = True
        except Exception as exc:  # record delivery failure, never break the request
            log.error = str(exc)
            logger.warning("Email to %s failed: %s", to_email, exc)
    else:
        log.backend = "console"
        logger.info("EMAIL (console) to=%s subject=%r\n%s", to_email, subject, body)
    db.add(log)
    return log


def _session_line(booking: Booking) -> str:
    return f"{booking.date.strftime('%A %Y-%m-%d')} {booking.section.label}"


def notify_booking_created(db, booking: Booking) -> None:
    send_email(
        db, booking.client.user.email,
        "Session booked — SanctuaryClub",
        f"Hi {booking.client.user.full_name},\n\n"
        f"Your session is booked: {_session_line(booking)}.\n"
        f"Check-in opens 10 minutes before start.\n\nSanctuaryClub",
    )


def notify_booking_cancelled(db, booking: Booking) -> None:
    send_email(
        db, booking.client.user.email,
        "Session cancelled — SanctuaryClub",
        f"Hi {booking.client.user.full_name},\n\n"
        f"Your session on {_session_line(booking)} has been cancelled.\n\nSanctuaryClub",
    )


def notify_booking_rescheduled(db, booking: Booking, old_desc: str) -> None:
    send_email(
        db, booking.client.user.email,
        "Session rescheduled — SanctuaryClub",
        f"Hi {booking.client.user.full_name},\n\n"
        f"Your session moved from {old_desc} to {_session_line(booking)}.\n\nSanctuaryClub",
    )


def notify_auto_absent(db, booking: Booking) -> None:
    send_email(
        db, booking.client.user.email,
        "Missed session — SanctuaryClub",
        f"Hi {booking.client.user.full_name},\n\n"
        f"You were marked absent for {_session_line(booking)}.\n"
        f"If this is a mistake, contact your coach — they can excuse the absence.\n\nSanctuaryClub",
    )


def notify_program_published(db, week: ProgramWeek) -> None:
    send_email(
        db, week.client.user.email,
        "New training program — SanctuaryClub",
        f"Hi {week.client.user.full_name},\n\n"
        f"Your coach published '{week.title}' for the week of {week.week_start}.\n"
        f"Open your program page to see the workouts.\n\nSanctuaryClub",
    )
