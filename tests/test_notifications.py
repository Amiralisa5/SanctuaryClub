from datetime import date, datetime

from sqlalchemy import select

from app import utils
from app.models import Booking, EmailLog, TimeSection
from app.services import attendance as attendance_svc
from app.services import scheduling

from .conftest import FROZEN_NOW, login, make_client, make_coach


def get_section(db, index=2):
    return db.scalar(select(TimeSection).where(TimeSection.index == index))


def emails(db, subject_part=""):
    rows = db.scalars(select(EmailLog).order_by(EmailLog.id)).all()
    return [e for e in rows if subject_part in e.subject]


def test_booking_lifecycle_sends_notifications(db):
    coach = make_coach(db)
    client = make_client(db, coach)
    booking = scheduling.create_booking(db, client, date(2026, 7, 9), get_section(db), coach.user)
    created = emails(db, "Session booked")
    assert len(created) == 1
    assert created[0].to_email == "client@test.local"
    assert created[0].backend == "console" and created[0].sent is False

    scheduling.reschedule_booking(db, booking, date(2026, 7, 10), get_section(db, 3), coach.user)
    assert len(emails(db, "rescheduled")) == 1

    scheduling.cancel_booking(db, booking, coach.user)
    assert len(emails(db, "cancelled")) == 1


def test_auto_absent_sends_notification(db, monkeypatch):
    coach = make_coach(db)
    client = make_client(db, coach)
    booking = Booking(client_id=client.id, coach_id=coach.id, date=FROZEN_NOW.date(),
                      section_id=get_section(db, 0).id)
    db.add(booking)
    db.commit()
    monkeypatch.setattr(utils, "now", lambda: datetime(2026, 7, 8, 8, 15))
    assert attendance_svc.auto_mark_absent(db) == 1
    missed = emails(db, "Missed session")
    assert len(missed) == 1
    assert "marked absent" in missed[0].body


def test_program_creation_notifies_client(client_http, db):
    coach = make_coach(db)
    client = make_client(db, coach)
    login(client_http, "coach@test.local", "coach-secret")
    response = client_http.post(
        f"/coach/clients/{client.id}/programs",
        data={"week_start": "2026-07-11", "title": "Block A"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db.expire_all()
    published = emails(db, "New training program")
    assert len(published) == 1
    assert "Block A" in published[0].body


def test_admin_email_log_page(client_http, db):
    coach = make_coach(db)
    client = make_client(db, coach)
    scheduling.create_booking(db, client, date(2026, 7, 9), get_section(db), coach.user)
    login(client_http, "admin@test.local", "admin-secret")
    page = client_http.get("/admin/emails")
    assert page.status_code == 200
    assert "Session booked" in page.text
    assert "LOGGED" in page.text  # console backend badge
