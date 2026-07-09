from datetime import date

import pytest
from sqlalchemy import select

from app.models import Booking, BookingStatus, CapacityOverride, TimeSection
from app.services import scheduling
from app.services.scheduling import BookingError

from .conftest import FROZEN_NOW, make_client, make_coach


def get_section(db, index=2):  # index 2 = 10:00-12:00
    return db.scalar(select(TimeSection).where(TimeSection.index == index))


TOMORROW = date(2026, 7, 9)


def test_sections_seeded_correctly(db):
    sections = db.scalars(select(TimeSection).order_by(TimeSection.index)).all()
    assert len(sections) == 8
    assert sections[0].label == "06:00-08:00"
    assert sections[-1].label == "20:00-22:00"


def test_create_booking_happy_path(db):
    coach = make_coach(db)
    client = make_client(db, coach)
    booking = scheduling.create_booking(db, client, TOMORROW, get_section(db), coach.user)
    assert booking.status == BookingStatus.BOOKED
    assert booking.coach_id == coach.id


def test_cannot_book_in_the_past(db):
    coach = make_coach(db)
    client = make_client(db, coach)
    with pytest.raises(BookingError, match="past"):
        # 06:00-08:00 today already started (frozen now = 09:00)
        scheduling.create_booking(db, client, FROZEN_NOW.date(), get_section(db, 0), coach.user)


def test_duplicate_booking_rejected(db):
    coach = make_coach(db)
    client = make_client(db, coach)
    section = get_section(db)
    scheduling.create_booking(db, client, TOMORROW, section, coach.user)
    with pytest.raises(BookingError, match="Already booked"):
        scheduling.create_booking(db, client, TOMORROW, section, coach.user)


def test_booking_requires_plan(db):
    coach = make_coach(db)
    client = make_client(db, coach, quota="none")
    with pytest.raises(BookingError, match="No active plan"):
        scheduling.create_booking(db, client, TOMORROW, get_section(db), coach.user)


def test_quota_enforced(db):
    coach = make_coach(db)
    client = make_client(db, coach, quota=2)
    scheduling.create_booking(db, client, date(2026, 7, 9), get_section(db), coach.user)
    scheduling.create_booking(db, client, date(2026, 7, 10), get_section(db), coach.user)
    with pytest.raises(BookingError, match="quota"):
        scheduling.create_booking(db, client, date(2026, 7, 11), get_section(db), coach.user)


def test_cancelled_bookings_free_quota(db):
    coach = make_coach(db)
    client = make_client(db, coach, quota=1)
    booking = scheduling.create_booking(db, client, date(2026, 7, 9), get_section(db), coach.user)
    scheduling.cancel_booking(db, booking, coach.user)
    scheduling.create_booking(db, client, date(2026, 7, 10), get_section(db), coach.user)


def test_gym_capacity_override_enforced(db):
    coach = make_coach(db)
    client_a = make_client(db, coach, email="a@test.local")
    client_b = make_client(db, coach, email="b@test.local")
    section = get_section(db)
    db.add(CapacityOverride(date=TOMORROW, section_id=section.id, coach_id=None, capacity=1))
    db.commit()
    scheduling.create_booking(db, client_a, TOMORROW, section, coach.user)
    with pytest.raises(BookingError, match="gym is full"):
        scheduling.create_booking(db, client_b, TOMORROW, section, coach.user)


def test_coach_capacity_enforced_independently(db):
    coach_a = make_coach(db, email="ca@test.local")
    coach_b = make_coach(db, email="cb@test.local")
    section = get_section(db)
    # coach A limited to 1 in this slot; gym has room
    db.add(CapacityOverride(date=TOMORROW, section_id=section.id, coach_id=coach_a.id, capacity=1))
    db.commit()
    a1 = make_client(db, coach_a, email="a1@test.local")
    a2 = make_client(db, coach_a, email="a2@test.local")
    b1 = make_client(db, coach_b, email="b1@test.local")
    scheduling.create_booking(db, a1, TOMORROW, section, coach_a.user)
    with pytest.raises(BookingError, match="coach's slot is full"):
        scheduling.create_booking(db, a2, TOMORROW, section, coach_a.user)
    # other coach unaffected
    scheduling.create_booking(db, b1, TOMORROW, section, coach_b.user)


def test_reschedule_allowed_more_than_2h_before(db):
    coach = make_coach(db)
    client = make_client(db, coach)
    booking = scheduling.create_booking(db, client, TOMORROW, get_section(db), coach.user)
    scheduling.reschedule_booking(db, booking, date(2026, 7, 10), get_section(db, 3), coach.user)
    assert booking.date == date(2026, 7, 10)
    assert booking.section_id == get_section(db, 3).id


def test_reschedule_blocked_within_2h_of_start(db):
    coach = make_coach(db)
    client = make_client(db, coach)
    # 10:00-12:00 today; frozen now is 09:00, i.e. only 1 hour before start
    section = get_section(db, 2)
    booking = Booking(client_id=client.id, coach_id=coach.id, date=FROZEN_NOW.date(),
                      section_id=section.id)
    db.add(booking)
    db.commit()
    with pytest.raises(BookingError, match="2 hours"):
        scheduling.reschedule_booking(db, booking, TOMORROW, section, client.user)
    with pytest.raises(BookingError, match="2 hours"):
        scheduling.cancel_booking(db, booking, client.user)


def test_bulk_wizard_books_matching_weekdays(db):
    coach = make_coach(db)
    client = make_client(db, coach, quota=None)  # unlimited
    section = get_section(db)
    # Mondays and Thursdays of July 2026, only future dates (after Jul 8)
    results = scheduling.bulk_book(db, client, 2026, 7, {0, 3}, section, coach.user)
    booked = [d for d, outcome in results if outcome == "booked"]
    skipped = [d for d, outcome in results if outcome != "booked"]
    assert booked == [date(2026, 7, 9), date(2026, 7, 13), date(2026, 7, 16),
                      date(2026, 7, 20), date(2026, 7, 23), date(2026, 7, 27),
                      date(2026, 7, 30)]
    # Past Mondays/Thursdays are reported as skipped, not silently dropped
    assert date(2026, 7, 2) in skipped and date(2026, 7, 6) in skipped


def test_bulk_wizard_stops_at_quota(db):
    coach = make_coach(db)
    client = make_client(db, coach, quota=3)
    results = scheduling.bulk_book(db, client, 2026, 7, {0, 3}, get_section(db), coach.user)
    booked = [d for d, outcome in results if outcome == "booked"]
    assert len(booked) == 3
