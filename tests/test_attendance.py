from datetime import date, datetime

import pytest
from sqlalchemy import select

from app import utils
from app.models import (
    Attendance,
    AttendanceStatus,
    Booking,
    BookingStatus,
    TimeSection,
)
from app.services import attendance as attendance_svc
from app.services.attendance import AttendanceError

from .conftest import FROZEN_NOW, login, make_client, make_coach


def make_booking(db, client, d, section_index=2):
    section = db.scalar(select(TimeSection).where(TimeSection.index == section_index))
    booking = Booking(client_id=client.id, coach_id=client.coach_id, date=d,
                      section_id=section.id)
    db.add(booking)
    db.commit()
    return booking


def set_now(monkeypatch, dt):
    monkeypatch.setattr(utils, "now", lambda: dt)


def test_checkin_inside_window_marks_present(db, monkeypatch):
    coach = make_coach(db)
    client = make_client(db, coach)
    # Section index 2 = 10:00-12:00 today; window is 09:50 - 10:20
    booking = make_booking(db, client, FROZEN_NOW.date())
    set_now(monkeypatch, datetime(2026, 7, 8, 9, 55))
    attendance = attendance_svc.check_in(db, booking, client.user, weight_kg=80.5,
                                         rpe=7, completion_pct=90, notes="felt good")
    assert attendance.status == AttendanceStatus.PRESENT
    assert attendance.auto is False
    assert attendance.weight_kg == 80.5


def test_checkin_rejected_outside_window(db, monkeypatch):
    coach = make_coach(db)
    client = make_client(db, coach)
    booking = make_booking(db, client, FROZEN_NOW.date())  # 10:00 start
    set_now(monkeypatch, datetime(2026, 7, 8, 9, 30))  # 30 min early
    with pytest.raises(AttendanceError, match="Check-in is open"):
        attendance_svc.check_in(db, booking, client.user)
    set_now(monkeypatch, datetime(2026, 7, 8, 10, 21))  # 21 min late
    with pytest.raises(AttendanceError, match="Check-in is open"):
        attendance_svc.check_in(db, booking, client.user)


def test_double_checkin_rejected(db, monkeypatch):
    coach = make_coach(db)
    client = make_client(db, coach)
    booking = make_booking(db, client, FROZEN_NOW.date())
    set_now(monkeypatch, datetime(2026, 7, 8, 10, 0))
    attendance_svc.check_in(db, booking, client.user)
    with pytest.raises(AttendanceError, match="already recorded"):
        attendance_svc.check_in(db, booking, client.user)


def test_auto_absent_after_grace_period(db, monkeypatch):
    coach = make_coach(db)
    client = make_client(db, coach)
    # 06:00-08:00 today, never checked in
    booking = make_booking(db, client, FROZEN_NOW.date(), section_index=0)
    # 08:05: still within the 10-minute grace period
    set_now(monkeypatch, datetime(2026, 7, 8, 8, 5))
    assert attendance_svc.auto_mark_absent(db) == 0
    # 08:10: grace period elapsed
    set_now(monkeypatch, datetime(2026, 7, 8, 8, 10))
    assert attendance_svc.auto_mark_absent(db) == 1
    db.refresh(booking)
    assert booking.attendance.status == AttendanceStatus.ABSENT
    assert booking.attendance.auto is True
    # Idempotent
    assert attendance_svc.auto_mark_absent(db) == 0


def test_auto_absent_skips_checked_in_and_future(db, monkeypatch):
    coach = make_coach(db)
    client = make_client(db, coach)
    attended = make_booking(db, client, FROZEN_NOW.date(), section_index=0)
    db.add(Attendance(booking_id=attended.id, status=AttendanceStatus.PRESENT))
    make_booking(db, client, date(2026, 7, 9), section_index=0)  # future booking
    db.commit()
    set_now(monkeypatch, datetime(2026, 7, 8, 12, 0))
    assert attendance_svc.auto_mark_absent(db) == 0


def test_coach_manual_mark_overrides_auto(db):
    coach = make_coach(db)
    client = make_client(db, coach)
    booking = make_booking(db, client, FROZEN_NOW.date(), section_index=0)
    db.add(Attendance(booking_id=booking.id, status=AttendanceStatus.ABSENT, auto=True))
    db.commit()
    db.refresh(booking)
    attendance_svc.set_attendance(db, booking, AttendanceStatus.EXCUSED, coach.user)
    db.refresh(booking)
    assert booking.attendance.status == AttendanceStatus.EXCUSED
    assert booking.attendance.auto is False


def test_monthly_summary_aggregates(db):
    coach = make_coach(db)
    client = make_client(db, coach)
    b1 = make_booking(db, client, date(2026, 7, 1), section_index=0)
    b2 = make_booking(db, client, date(2026, 7, 2), section_index=0)
    b3 = make_booking(db, client, date(2026, 7, 3), section_index=0)
    make_booking(db, client, date(2026, 7, 9), section_index=0)  # pending, no attendance
    db.add_all([
        Attendance(booking_id=b1.id, status=AttendanceStatus.PRESENT),
        Attendance(booking_id=b2.id, status=AttendanceStatus.ABSENT, auto=True),
        Attendance(booking_id=b3.id, status=AttendanceStatus.EXCUSED),
    ])
    db.commit()
    summary = attendance_svc.monthly_summary(db, client.id, 2026, 7)
    assert summary == {"booked": 4, "present": 1, "absent": 1, "excused": 1, "pending": 1}
    # Other months unaffected
    empty = attendance_svc.monthly_summary(db, client.id, 2026, 8)
    assert empty["booked"] == 0


def test_checkin_via_http_flow(client_http, db, monkeypatch):
    coach = make_coach(db)
    client = make_client(db, coach)
    booking = make_booking(db, client, FROZEN_NOW.date())  # 10:00-12:00
    set_now(monkeypatch, datetime(2026, 7, 8, 10, 5))
    login(client_http, "client@test.local", "client-secret")
    response = client_http.post(
        f"/client/checkin/{booking.id}",
        data={"weight_kg": "81", "rpe": "8", "completion_pct": "95", "notes": "solid"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/client"
    db.expire_all()
    attendance = db.scalar(select(Attendance).where(Attendance.booking_id == booking.id))
    assert attendance.status == AttendanceStatus.PRESENT
    assert attendance.rpe == 8


def test_client_cannot_check_in_to_someone_elses_booking(client_http, db, monkeypatch):
    coach = make_coach(db)
    owner = make_client(db, coach, email="owner@test.local")
    intruder = make_client(db, coach, email="intruder@test.local")
    booking = make_booking(db, owner, FROZEN_NOW.date())
    set_now(monkeypatch, datetime(2026, 7, 8, 10, 5))
    login(client_http, "intruder@test.local", "client-secret")
    response = client_http.post(f"/client/checkin/{booking.id}", data={},
                                follow_redirects=False)
    assert response.status_code == 403
