from datetime import date

from sqlalchemy import select

from app.models import (
    Attendance,
    AttendanceStatus,
    Booking,
    CapacityOverride,
    TimeSection,
)
from app.services import availability as avail_svc
from app.services import classes as classes_svc

from .conftest import login, make_client, make_coach


def get_section(db, index=2):
    return db.scalar(select(TimeSection).where(TimeSection.index == index))


def make_booking(db, client, d, section_index=2, attendance=None):
    booking = Booking(client_id=client.id, coach_id=client.coach_id, date=d,
                      section_id=get_section(db, section_index).id)
    db.add(booking)
    db.flush()
    if attendance:
        db.add(Attendance(booking_id=booking.id, status=AttendanceStatus(attendance)))
    db.commit()
    return booking


# --- Availability / shared calendar ---

def test_block_and_unblock_slot(db):
    coach = make_coach(db)
    d = date(2026, 7, 15)
    section = get_section(db)
    avail_svc.block_slot(db, coach, d, section.id, coach.user)
    grid = avail_svc.month_availability(db, coach, 2026, 7)
    slot = next(s for s in grid[d] if s["section"].id == section.id)
    assert slot["status"] == "blocked"
    avail_svc.unblock_slot(db, coach, d, section.id, coach.user)
    grid = avail_svc.month_availability(db, coach, 2026, 7)
    slot = next(s for s in grid[d] if s["section"].id == section.id)
    assert slot["status"] == "open" and slot["left"] == 6  # coach default


def test_availability_reflects_bookings_and_limits(db):
    coach = make_coach(db)
    d = date(2026, 7, 16)
    section = get_section(db)
    # Coach capacity 3 for that slot; 2 bookings -> 'limited' with 1 left
    db.add(CapacityOverride(date=d, section_id=section.id, coach_id=coach.id, capacity=3))
    db.commit()
    for i in range(2):
        client = make_client(db, coach, email=f"c{i}@test.local")
        make_booking(db, client, d)
    grid = avail_svc.month_availability(db, coach, 2026, 7)
    slot = next(s for s in grid[d] if s["section"].id == section.id)
    assert (slot["status"], slot["left"], slot["booked"]) == ("limited", 1, 2)


def test_blocked_slot_rejects_booking(db):
    from app.services import scheduling
    from app.services.scheduling import BookingError
    import pytest

    coach = make_coach(db)
    client = make_client(db, coach)
    d = date(2026, 7, 17)
    section = get_section(db)
    avail_svc.block_slot(db, coach, d, section.id, coach.user)
    with pytest.raises(BookingError, match="full"):
        scheduling.create_booking(db, client, d, section, coach.user)


def test_client_sees_coach_calendar_and_coach_manages_it(client_http, db):
    coach = make_coach(db)
    make_client(db, coach)
    login(client_http, "coach@test.local", "coach-secret")
    response = client_http.post("/coach/availability/block",
                                data={"block_date": "2026-07-20", "section_id": get_section(db).id},
                                follow_redirects=False)
    assert response.status_code == 303
    page = client_http.get("/coach/availability?year=2026&month=7")
    assert page.status_code == 200 and "blocked" in page.text

    login(client_http, "client@test.local", "client-secret")
    page = client_http.get("/client/coach-calendar?year=2026&month=7")
    assert page.status_code == 200
    assert "blocked" in page.text and "left" in page.text


def test_coachless_client_redirected_from_coach_calendar(client_http, db):
    coach = make_coach(db)
    client = make_client(db, coach)
    client.coach_id = None
    db.commit()
    login(client_http, "client@test.local", "client-secret")
    response = client_http.get("/client/coach-calendar", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/client"


# --- Class monitoring ---

def seed_classes(db):
    coach_a = make_coach(db, email="ca@test.local", name="Coach A")
    coach_b = make_coach(db, email="cb@test.local", name="Coach B")
    a1 = make_client(db, coach_a, email="a1@test.local", name="Amy Client")
    a2 = make_client(db, coach_a, email="a2@test.local", name="Ann Client")
    b1 = make_client(db, coach_b, email="b1@test.local", name="Bob Client")
    d = date(2026, 7, 6)  # Monday
    make_booking(db, a1, d, 2, attendance="PRESENT")
    make_booking(db, a2, d, 2, attendance="ABSENT")
    make_booking(db, a1, date(2026, 7, 7), 3, attendance="PRESENT")
    make_booking(db, b1, d, 2, attendance="PRESENT")  # same class slot, other coach
    return coach_a, coach_b


def test_month_overview_counts_and_weeks(db):
    coach_a, _ = seed_classes(db)
    overview = classes_svc.month_overview(db, 2026, 7, coach_id=coach_a.id)
    assert overview["totals"]["booked"] == 3
    assert overview["totals"]["present"] == 2
    assert overview["totals"]["absent"] == 1
    assert overview["totals"]["classes"] == 2  # two distinct date+section groups
    assert overview["totals"]["rate"] == 67
    # Weeks list covers the whole month and one week holds all the sessions
    assert sum(w["booked"] for w in overview["weeks"]) == 3
    # Gym-wide (no coach filter) includes coach B's booking
    overview_all = classes_svc.month_overview(db, 2026, 7)
    assert overview_all["totals"]["booked"] == 4


def test_day_roster_groups_by_section(db):
    coach_a, _ = seed_classes(db)
    roster = classes_svc.day_roster(db, date(2026, 7, 6), coach_id=coach_a.id)
    assert len(roster) == 1
    entry = roster[0]
    assert len(entry["bookings"]) == 2
    assert entry["counts"] == {"present": 1, "absent": 1, "excused": 0, "pending": 0}
    # Admin scope sees all three participants in the class
    roster_all = classes_svc.day_roster(db, date(2026, 7, 6))
    assert len(roster_all[0]["bookings"]) == 3


def test_class_pages_render_and_isolate(client_http, db):
    seed_classes(db)
    login(client_http, "ca@test.local", "coach-secret")
    page = client_http.get("/coach/classes?year=2026&month=7&day=6")
    assert page.status_code == 200
    assert "Amy Client" in page.text and "Bob Client" not in page.text
    assert "PRESENT" in page.text and "ABSENT" in page.text

    login(client_http, "admin@test.local", "admin-secret")
    page = client_http.get("/admin/classes?year=2026&month=7&day=6")
    assert page.status_code == 200
    assert "Amy Client" in page.text and "Bob Client" in page.text
    assert "Coach A" in page.text and "Coach B" in page.text
