"""Typed error and per-use-case validator coverage for the booking service."""
from datetime import date

import pytest
from sqlalchemy import select

from app.models import Booking, CapacityOverride, TimeSection
from app.services import booking
from app.services.booking import (
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

from .conftest import FROZEN_NOW, make_client, make_coach


def get_section(db, index=2):  # index 2 = 10:00-12:00
    return db.scalar(select(TimeSection).where(TimeSection.index == index))


TOMORROW = date(2026, 7, 9)


def test_no_coach_raises_typed_error(db):
    coach = make_coach(db)
    client = make_client(db, coach)
    client.coach_id = None
    db.commit()
    with pytest.raises(NoCoachAssignedError):
        booking.validate_create(db, client, TOMORROW, get_section(db))


def test_past_slot_raises_typed_error(db):
    coach = make_coach(db)
    client = make_client(db, coach)
    with pytest.raises(PastSlotError):
        booking.validate_create(db, client, FROZEN_NOW.date(), get_section(db, 0))


def test_duplicate_raises_typed_error(db):
    coach = make_coach(db)
    client = make_client(db, coach)
    section = get_section(db)
    booking.create_booking(db, client, TOMORROW, section, coach.user)
    with pytest.raises(DuplicateBookingError):
        booking.validate_create(db, client, TOMORROW, section)


def test_no_plan_raises_typed_error(db):
    coach = make_coach(db)
    client = make_client(db, coach, quota="none")
    with pytest.raises(NoPlanError):
        booking.validate_create(db, client, TOMORROW, get_section(db))


def test_quota_exceeded_raises_typed_error(db):
    coach = make_coach(db)
    client = make_client(db, coach, quota=1)
    booking.create_booking(db, client, TOMORROW, get_section(db), coach.user)
    with pytest.raises(QuotaExceededError):
        booking.validate_create(db, client, date(2026, 7, 10), get_section(db))


def test_gym_full_raises_typed_error(db):
    coach = make_coach(db)
    client_a = make_client(db, coach, email="a@test.local")
    client_b = make_client(db, coach, email="b@test.local")
    section = get_section(db)
    db.add(CapacityOverride(date=TOMORROW, section_id=section.id, coach_id=None, capacity=1))
    db.commit()
    booking.create_booking(db, client_a, TOMORROW, section, coach.user)
    with pytest.raises(GymFullError):
        booking.validate_create(db, client_b, TOMORROW, section)


def test_coach_full_raises_typed_error(db):
    coach = make_coach(db)
    client_a = make_client(db, coach, email="a@test.local")
    client_b = make_client(db, coach, email="b@test.local")
    section = get_section(db)
    db.add(CapacityOverride(date=TOMORROW, section_id=section.id, coach_id=coach.id, capacity=1))
    db.commit()
    booking.create_booking(db, client_a, TOMORROW, section, coach.user)
    with pytest.raises(CoachFullError):
        booking.validate_create(db, client_b, TOMORROW, section)


def test_cancel_validator_rejects_inactive_booking(db):
    coach = make_coach(db)
    client = make_client(db, coach)
    record = booking.create_booking(db, client, TOMORROW, get_section(db), coach.user)
    booking.cancel_booking(db, record, coach.user)
    with pytest.raises(BookingNotActiveError):
        booking.validate_cancel(record)


def test_cancel_validator_rejects_after_cutoff(db):
    coach = make_coach(db)
    client = make_client(db, coach)
    # 10:00-12:00 today; frozen now is 09:00, i.e. only 1 hour before start
    section = get_section(db, 2)
    record = Booking(client_id=client.id, coach_id=coach.id, date=FROZEN_NOW.date(),
                     section_id=section.id)
    db.add(record)
    db.commit()
    with pytest.raises(CutoffPassedError):
        booking.validate_cancel(record)


def test_reschedule_validator_rejects_same_slot(db):
    coach = make_coach(db)
    client = make_client(db, coach)
    section = get_section(db)
    record = booking.create_booking(db, client, TOMORROW, section, coach.user)
    with pytest.raises(SameSlotError):
        booking.validate_reschedule(db, record, TOMORROW, section)


def test_error_codes_are_stable(db):
    assert NoCoachAssignedError.code == "no_coach"
    assert PastSlotError.code == "past_slot"
    assert DuplicateBookingError.code == "duplicate_booking"
    assert NoPlanError.code == "no_plan"
    assert QuotaExceededError.code == "quota_exceeded"
    assert GymFullError.code == "gym_full"
    assert CoachFullError.code == "coach_full"
    assert BookingNotActiveError.code == "not_active"
    assert CutoffPassedError.code == "cutoff_passed"
    assert SameSlotError.code == "same_slot"


def test_scheduling_facade_reexports_booking_api(db):
    from app.services import scheduling

    assert scheduling.BookingError is booking.BookingError
    assert scheduling.create_booking is booking.create_booking
    assert scheduling.validate_slot is booking.validate_slot
