import logging
import smtplib
from email.message import EmailMessage

from sqlalchemy import select

from .. import config
from ..models import Booking, EmailLog, Notification, ProgramWeek, Role, User
from ..utils import now

logger = logging.getLogger("sanctuaryclub.email")


def notify_inapp(db, user_id: int, title: str, body: str = "", link: str = "") -> None:
    db.add(Notification(user_id=user_id, title=title, body=body, link=link,
                        created_at=now()))


def notify_admins(db, title: str, body: str = "", link: str = "") -> None:
    admin_ids = db.scalars(select(User.id).where(User.role == Role.ADMIN,
                                                 User.is_active.is_(True))).all()
    for admin_id in admin_ids:
        notify_inapp(db, admin_id, title, body, link)


def _notify_booking_parties(db, booking: Booking, actor: User | None,
                            title: str, body: str) -> None:
    """In-app alert to whichever side of the booking didn't perform the action."""
    actor_id = actor.id if actor else None
    client_user_id = booking.client.user_id
    coach_user_id = booking.coach.user_id if booking.coach else None
    if client_user_id != actor_id:
        notify_inapp(db, client_user_id, title, body, "/client/bookings")
    if coach_user_id and coach_user_id != actor_id:
        notify_inapp(db, coach_user_id, title, body, f"/coach/clients/{booking.client_id}")


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


def notify_booking_created(db, booking: Booking, actor: User | None = None) -> None:
    send_email(
        db, booking.client.user.email,
        "Session booked — SanctuaryClub",
        f"Hi {booking.client.user.full_name},\n\n"
        f"Your session is booked: {_session_line(booking)}.\n"
        f"Check-in opens 10 minutes before start.\n\nSanctuaryClub",
    )
    _notify_booking_parties(db, booking, actor, "Session booked",
                            f"{booking.client.user.full_name} · {_session_line(booking)}")


def notify_booking_cancelled(db, booking: Booking, actor: User | None = None) -> None:
    send_email(
        db, booking.client.user.email,
        "Session cancelled — SanctuaryClub",
        f"Hi {booking.client.user.full_name},\n\n"
        f"Your session on {_session_line(booking)} has been cancelled.\n\nSanctuaryClub",
    )
    _notify_booking_parties(db, booking, actor, "Session cancelled",
                            f"{booking.client.user.full_name} · {_session_line(booking)}")


def notify_booking_rescheduled(db, booking: Booking, old_desc: str,
                               actor: User | None = None) -> None:
    send_email(
        db, booking.client.user.email,
        "Session rescheduled — SanctuaryClub",
        f"Hi {booking.client.user.full_name},\n\n"
        f"Your session moved from {old_desc} to {_session_line(booking)}.\n\nSanctuaryClub",
    )
    _notify_booking_parties(db, booking, actor, "Session rescheduled",
                            f"{booking.client.user.full_name} · now {_session_line(booking)}")


def notify_auto_absent(db, booking: Booking) -> None:
    send_email(
        db, booking.client.user.email,
        "Missed session — SanctuaryClub",
        f"Hi {booking.client.user.full_name},\n\n"
        f"You were marked absent for {_session_line(booking)}.\n"
        f"If this is a mistake, contact your coach — they can excuse the absence.\n\nSanctuaryClub",
    )
    notify_inapp(db, booking.client.user_id, "Marked absent",
                 f"You missed {_session_line(booking)} — your coach can excuse it.",
                 "/client/bookings")


def notify_program_published(db, week: ProgramWeek) -> None:
    send_email(
        db, week.client.user.email,
        "New training program — SanctuaryClub",
        f"Hi {week.client.user.full_name},\n\n"
        f"Your coach published '{week.title}' for the week of {week.week_start}.\n"
        f"Open your program page to see the workouts.\n\nSanctuaryClub",
    )
    notify_inapp(db, week.client.user_id, "New training program",
                 f"'{week.title}' — week of {week.week_start}", "/client/program")


def notify_new_signup(db, client_user: User) -> None:
    notify_admins(db, "New client signed up",
                  f"{client_user.full_name} ({client_user.email}) needs a coach.",
                  "/admin/users")


def notify_coach_assigned(db, client, coach) -> None:
    notify_inapp(db, client.user_id, "Coach assigned",
                 f"{coach.user.full_name} is now your coach.", "/client")
    notify_inapp(db, coach.user_id, "New client assigned",
                 f"{client.user.full_name} was assigned to you.",
                 f"/coach/clients/{client.id}")
