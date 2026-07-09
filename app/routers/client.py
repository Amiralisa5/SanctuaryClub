from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from ..models import (
    Booking,
    BookingStatus,
    Client,
    ProgramWeek,
    Role,
    TimeSection,
    User,
)
from ..security import get_db, require_role
from ..services import attendance as attendance_svc
from ..services import scheduling
from ..services.attendance import AttendanceError
from ..services.scheduling import BookingError
from ..utils import flash, now
from ..web import render

router = APIRouter(prefix="/client")


def current_client(user: User = Depends(require_role(Role.CLIENT)), db=Depends(get_db)) -> Client:
    client = db.scalar(select(Client).where(Client.user_id == user.id))
    if client is None:
        raise HTTPException(403, "No client profile linked to this account.")
    return client


def owned_booking(db, client: Client, booking_id: int) -> Booking:
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(404)
    if booking.client_id != client.id:
        raise HTTPException(403, "This booking is not yours.")
    return booking


def _sections(db) -> list[TimeSection]:
    return db.scalars(select(TimeSection).order_by(TimeSection.index)).all()


def _current_program(db, client: Client) -> ProgramWeek | None:
    today = now().date()
    week = db.scalar(
        select(ProgramWeek)
        .where(ProgramWeek.client_id == client.id, ProgramWeek.week_start <= today)
        .order_by(ProgramWeek.week_start.desc())
        .limit(1)
    )
    if week and week.week_start + timedelta(days=6) >= today:
        return week
    return week  # fall back to the most recent past program if none covers today


@router.get("")
def dashboard(request: Request, client: Client = Depends(current_client), db=Depends(get_db)):
    current = now()
    upcoming = db.scalars(
        select(Booking)
        .join(TimeSection)
        .where(Booking.client_id == client.id, Booking.status == BookingStatus.BOOKED,
               Booking.date >= current.date())
        .order_by(Booking.date, TimeSection.index)
        .limit(5)
    ).all()
    checkin_ready = [b for b in upcoming if attendance_svc.can_check_in(b)]
    summary = attendance_svc.monthly_summary(db, client.id, current.year, current.month)
    plan = scheduling.get_plan(db, client.id, current.year, current.month)
    program = _current_program(db, client)
    return render(request, "client/dashboard.html", user=client.user, client=client,
                  upcoming=upcoming, checkin_ready=checkin_ready, summary=summary,
                  plan=plan, program=program, current=current)


@router.get("/bookings")
def bookings_page(request: Request, client: Client = Depends(current_client), db=Depends(get_db)):
    current = now()
    bookings = db.scalars(
        select(Booking).where(Booking.client_id == client.id)
        .order_by(Booking.date.desc()).limit(60)
    ).all()
    plan = scheduling.get_plan(db, client.id, current.year, current.month)
    used = scheduling.quota_used(db, client.id, current.year, current.month)
    modifiable_ids = set()
    for b in bookings:
        if b.status == BookingStatus.BOOKED:
            cutoff = scheduling.section_start(b.date, b.section) - timedelta(hours=2)
            if current <= cutoff:
                modifiable_ids.add(b.id)
    return render(request, "client/bookings.html", user=client.user, client=client,
                  bookings=bookings, sections=_sections(db), plan=plan, used=used,
                  current=current, modifiable_ids=modifiable_ids)


@router.post("/bookings")
def create_booking(request: Request, booking_date: date = Form(...), section_id: int = Form(...),
                   client: Client = Depends(current_client), db=Depends(get_db)):
    section = db.get(TimeSection, section_id)
    if section is None:
        raise HTTPException(404)
    try:
        scheduling.create_booking(db, client, booking_date, section, client.user)
        flash(request, f"Booked {booking_date} {section.label}.", "success")
    except BookingError as exc:
        flash(request, str(exc), "error")
    return RedirectResponse("/client/bookings", status_code=303)


@router.post("/bookings/wizard")
def bulk_wizard(request: Request, year: int = Form(...), month: int = Form(...),
                section_id: int = Form(...), weekdays: list[int] = Form([]),
                client: Client = Depends(current_client), db=Depends(get_db)):
    section = db.get(TimeSection, section_id)
    if section is None:
        raise HTTPException(404)
    if not weekdays:
        flash(request, "Select at least one weekday.", "error")
        return RedirectResponse("/client/bookings", status_code=303)
    results = scheduling.bulk_book(db, client, year, month, set(weekdays), section, client.user)
    booked = sum(1 for _, outcome in results if outcome == "booked")
    skipped = [f"{d}: {outcome}" for d, outcome in results if outcome != "booked"]
    flash(request, f"Wizard booked {booked} session(s) for {year}-{month:02d}.", "success")
    for line in skipped[:8]:
        flash(request, f"Skipped {line}", "error")
    return RedirectResponse("/client/bookings", status_code=303)


@router.post("/bookings/{booking_id}/cancel")
def cancel_booking(request: Request, booking_id: int,
                   client: Client = Depends(current_client), db=Depends(get_db)):
    booking = owned_booking(db, client, booking_id)
    try:
        scheduling.cancel_booking(db, booking, client.user)
        flash(request, "Booking cancelled.", "success")
    except BookingError as exc:
        flash(request, str(exc), "error")
    return RedirectResponse("/client/bookings", status_code=303)


@router.post("/bookings/{booking_id}/reschedule")
def reschedule_booking(request: Request, booking_id: int, new_date: date = Form(...),
                       new_section_id: int = Form(...),
                       client: Client = Depends(current_client), db=Depends(get_db)):
    booking = owned_booking(db, client, booking_id)
    section = db.get(TimeSection, new_section_id)
    if section is None:
        raise HTTPException(404)
    try:
        scheduling.reschedule_booking(db, booking, new_date, section, client.user)
        flash(request, f"Rescheduled to {new_date} {section.label}.", "success")
    except BookingError as exc:
        flash(request, str(exc), "error")
    return RedirectResponse("/client/bookings", status_code=303)


@router.get("/checkin/{booking_id}")
def checkin_page(request: Request, booking_id: int,
                 client: Client = Depends(current_client), db=Depends(get_db)):
    booking = owned_booking(db, client, booking_id)
    opens, closes = attendance_svc.checkin_window(booking)
    return render(request, "client/checkin.html", user=client.user, booking=booking,
                  can_check_in=attendance_svc.can_check_in(booking),
                  opens=opens, closes=closes)


@router.post("/checkin/{booking_id}")
def check_in(request: Request, booking_id: int, weight_kg: str = Form(""),
             rpe: str = Form(""), completion_pct: str = Form(""), notes: str = Form(""),
             client: Client = Depends(current_client), db=Depends(get_db)):
    booking = owned_booking(db, client, booking_id)
    try:
        attendance_svc.check_in(
            db, booking, client.user,
            weight_kg=float(weight_kg) if weight_kg else None,
            rpe=int(rpe) if rpe else None,
            completion_pct=int(completion_pct) if completion_pct else None,
            notes=notes.strip(),
        )
        flash(request, "Checked in — you are marked present. Have a great session!", "success")
        return RedirectResponse("/client", status_code=303)
    except (AttendanceError, ValueError) as exc:
        flash(request, str(exc), "error")
        return RedirectResponse(f"/client/checkin/{booking.id}", status_code=303)


@router.get("/program")
def program_page(request: Request, client: Client = Depends(current_client), db=Depends(get_db)):
    weeks = db.scalars(
        select(ProgramWeek).where(ProgramWeek.client_id == client.id)
        .order_by(ProgramWeek.week_start.desc()).limit(8)
    ).all()
    return render(request, "client/program.html", user=client.user, weeks=weeks)
