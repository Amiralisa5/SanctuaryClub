from datetime import date

from sqlalchemy import select

from app.models import Booking, TimeSection

from .conftest import login, make_client, make_coach


def make_booking(db, client, d, section_index=2):
    section = db.scalar(select(TimeSection).where(TimeSection.index == section_index))
    booking = Booking(client_id=client.id, coach_id=client.coach_id, date=d,
                      section_id=section.id)
    db.add(booking)
    db.commit()
    return booking


def test_client_calendar_shows_own_sessions(client_http, db):
    coach = make_coach(db)
    client = make_client(db, coach)
    make_booking(db, client, date(2026, 7, 15))
    login(client_http, "client@test.local", "client-secret")
    page = client_http.get("/client/calendar")  # defaults to the current (frozen) month
    assert page.status_code == 200
    assert "July 2026" in page.text
    assert "10:00-12:00" in page.text  # agenda entry


def test_coach_calendar_shows_client_names_and_isolates_data(client_http, db):
    coach_a = make_coach(db, email="ca@test.local", name="Coach A")
    coach_b = make_coach(db, email="cb@test.local", name="Coach B")
    mine = make_client(db, coach_a, email="mine@test.local", name="Mona Lifter")
    other = make_client(db, coach_b, email="other@test.local", name="Otto Presser")
    make_booking(db, mine, date(2026, 7, 20))
    make_booking(db, other, date(2026, 7, 20), section_index=3)
    login(client_http, "ca@test.local", "coach-secret")
    page = client_http.get("/coach/calendar")
    assert page.status_code == 200
    assert "Mona" in page.text
    assert "Otto" not in page.text  # other coach's bookings are invisible


def test_calendar_month_navigation(client_http, db):
    coach = make_coach(db)
    make_client(db, coach)
    login(client_http, "client@test.local", "client-secret")
    page = client_http.get("/client/calendar?year=2026&month=12")
    assert page.status_code == 200
    assert "December 2026" in page.text
    # prev/next wrap across year boundaries
    assert "/client/calendar?year=2026&month=11" in page.text
    assert "/client/calendar?year=2027&month=1" in page.text


def test_calendar_requires_login(client_http):
    page = client_http.get("/client/calendar", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == "/login"
