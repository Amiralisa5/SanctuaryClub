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


@router.get("/calendar")
def calendar_view(request: Request, year: int | None = None, month: int | None = None,
                  client: Client = Depends(current_client), db=Depends(get_db)):
    from sqlalchemy import extract

    from ..utils import WEEKDAY_NAMES, month_grid, shift_month

    current = now()
    year = year or current.year
    month = month if month and 1 <= month <= 12 else current.month
    bookings = db.scalars(
        select(Booking)
        .join(TimeSection)
        .where(Booking.client_id == client.id,
               extract("year", Booking.date) == year,
               extract("month", Booking.date) == month)
        .order_by(Booking.date, TimeSection.index)
    ).all()
    by_day: dict = {}
    for b in bookings:
        by_day.setdefault(b.date, []).append(b)
    return render(request, "client/calendar.html", user=client.user,
                  grid=month_grid(year, month), by_day=by_day, bookings=bookings,
                  year=year, month=month, today=current.date(),
                  weekday_names=WEEKDAY_NAMES,
                  prev_ym=shift_month(year, month, -1), next_ym=shift_month(year, month, 1))


@router.get("/bookings")
def bookings_page(request: Request, booking_date: date | None = None,
                  section_id: int | None = None, edit: int | None = None,
                  client: Client = Depends(current_client), db=Depends(get_db)):
    current = now()
    sections = _sections(db)
    selected_date = booking_date or current.date()
    if selected_date < current.date():
        flash(request, "Cannot book sessions in the past — showing today instead.", "error")
        selected_date = current.date()

    bookings = db.scalars(
        select(Booking).where(Booking.client_id == client.id)
        .order_by(Booking.date.desc()).limit(60)
    ).all()
    plan = scheduling.get_plan(db, client.id, current.year, current.month)
    used = scheduling.quota_used(db, client.id, current.year, current.month)
    modifiable_ids = {b.id for b in bookings if scheduling.is_modifiable(b, current)}
    editing = db.get(Booking, edit) if edit else None
    if editing and editing.client_id != client.id:
        editing = None
    day_slots = scheduling.day_slots_for_client(
        db, client, selected_date, sections,
        exclude_booking_id=editing.id if editing else None,
    )
    return render(request, "client/bookings.html", user=client.user, client=client,
                  bookings=bookings, sections=sections, plan=plan, used=used,
                  current=current, modifiable_ids=modifiable_ids,
                  selected_date=selected_date, selected_section_id=section_id,
                  day_slots=day_slots, editing=editing)


@router.post("/bookings")
def create_booking(request: Request, booking_date: date = Form(...), section_id: int = Form(...),
                   client: Client = Depends(current_client), db=Depends(get_db)):
    section = db.get(TimeSection, section_id)
    if section is None:
        flash(request, "That time slot no longer exists.", "error")
        return RedirectResponse("/client/bookings", status_code=303)
    reason = scheduling.validate_slot(db, client, booking_date, section)
    if reason:
        flash(request, reason, "error")
        return RedirectResponse(
            f"/client/bookings?booking_date={booking_date}&section_id={section_id}",
            status_code=303,
        )
    try:
        scheduling.create_booking(db, client, booking_date, section, client.user)
        flash(request, f"Booked {booking_date.strftime('%A %b %-d')} · {section.label}.", "success")
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
        flash(request, "That time slot no longer exists.", "error")
        return RedirectResponse(f"/client/bookings?edit={booking_id}", status_code=303)
    reason = scheduling.validate_slot(
        db, client, new_date, section, exclude_booking_id=booking.id
    )
    if reason:
        flash(request, reason, "error")
        return RedirectResponse(
            f"/client/bookings?edit={booking_id}&booking_date={new_date}&section_id={new_section_id}",
            status_code=303,
        )
    try:
        scheduling.reschedule_booking(db, booking, new_date, section, client.user)
        flash(request, f"Moved to {new_date.strftime('%A %b %-d')} · {section.label}.", "success")
    except BookingError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse(f"/client/bookings?edit={booking_id}", status_code=303)
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


@router.get("/progress")
def progress_page(request: Request, client: Client = Depends(current_client), db=Depends(get_db)):
    from ..services import metrics
    return render(request, "client/progress.html", user=client.user,
                  **metrics.progress_context(db, client))


@router.get("/coach-calendar")
def coach_calendar(request: Request, year: int | None = None, month: int | None = None,
                   client: Client = Depends(current_client), db=Depends(get_db)):
    from ..services import availability as avail_svc
    from ..utils import WEEKDAY_NAMES, month_grid, shift_month

    if client.coach is None:
        flash(request, "You'll see your coach's calendar once an admin assigns one.", "info")
        return RedirectResponse("/client", status_code=303)
    current = now()
    year = year or current.year
    month = month if month and 1 <= month <= 12 else current.month
    return render(request, "client/coach_calendar.html", user=client.user, client=client,
                  grid=month_grid(year, month),
                  availability=avail_svc.month_availability(db, client.coach, year, month),
                  year=year, month=month, today=current.date(),
                  weekday_names=WEEKDAY_NAMES,
                  prev_ym=shift_month(year, month, -1), next_ym=shift_month(year, month, 1))


# --- Activities (health data, private to the client + their coach) ---

def _activities_context(db, client: Client) -> dict:
    from ..services.health import queries as health_q
    return {
        "activities": health_q.ask(db, health_q.ListActivities(client_id=client.id)),
        "stats": health_q.ask(db, health_q.ActivityStats(client_id=client.id)),
        "weeks": health_q.ask(db, health_q.WeeklyVolume(client_id=client.id)),
        "connections": health_q.ask(db, health_q.Connections(client_id=client.id)),
    }


@router.get("/activities")
def activities_page(request: Request, client: Client = Depends(current_client), db=Depends(get_db)):
    return render(request, "client/activities.html", user=client.user, client=client,
                  can_edit=True, **_activities_context(db, client))


@router.post("/activities")
def add_activity(request: Request, client: Client = Depends(current_client), db=Depends(get_db),
                 sport_type: str = Form(...), start_time: str = Form(...),
                 duration_minutes: str = Form(...), name: str = Form(""),
                 distance_km: str = Form(""), calories: str = Form(""),
                 avg_hr: str = Form(""), notes: str = Form("")):
    from ..services.health import commands as health_c
    try:
        fields = {
            "sport_type": sport_type, "start_time": start_time, "name": name,
            "duration_seconds": int(float(duration_minutes or 0) * 60),
            "distance_m": float(distance_km) * 1000 if distance_km else None,
            "calories": calories or None, "avg_hr": avg_hr or None, "notes": notes,
        }
        health_c.handle(db, health_c.AddManualActivity(
            client_id=client.id, fields=fields, actor_id=client.user_id))
        flash(request, "Activity recorded.", "success")
    except (health_c.HealthError, ValueError) as exc:
        flash(request, str(exc), "error")
    return RedirectResponse("/client/activities", status_code=303)


@router.post("/activities/import")
def import_activities(request: Request, client: Client = Depends(current_client),
                      db=Depends(get_db), provider: str = Form(...), payload: str = Form(...)):
    import json

    from ..services.health import commands as health_c
    try:
        items = json.loads(payload)
        result = health_c.handle(db, health_c.ImportActivities(
            client_id=client.id, provider=provider, items=items, actor_id=client.user_id))
        flash(request, f"Imported {result['imported']} activities from {provider.replace('_', ' ')}.",
              "success")
        for line in result["skipped"][:6]:
            flash(request, f"Skipped {line}", "error")
    except json.JSONDecodeError:
        flash(request, "Payload must be valid JSON (a list of activities).", "error")
    except health_c.HealthError as exc:
        flash(request, str(exc), "error")
    return RedirectResponse("/client/activities", status_code=303)


@router.post("/activities/sync-strava")
def sync_strava(request: Request, client: Client = Depends(current_client), db=Depends(get_db)):
    from ..services.health import commands as health_c
    try:
        result = health_c.handle(db, health_c.SyncStrava(client_id=client.id,
                                                         actor_id=client.user_id))
        flash(request, f"Strava sync complete — {result['imported']} new activities.", "success")
    except health_c.HealthError as exc:
        flash(request, str(exc), "error")
    return RedirectResponse("/client/activities", status_code=303)


@router.post("/activities/{activity_id}/delete")
def delete_activity(request: Request, activity_id: int,
                    client: Client = Depends(current_client), db=Depends(get_db)):
    from ..services.health import commands as health_c
    try:
        health_c.handle(db, health_c.DeleteActivity(
            client_id=client.id, activity_id=activity_id, actor_id=client.user_id))
        flash(request, "Activity deleted.", "success")
    except health_c.HealthError as exc:
        flash(request, str(exc), "error")
    return RedirectResponse("/client/activities", status_code=303)


@router.get("/program")
def program_page(request: Request, client: Client = Depends(current_client), db=Depends(get_db)):
    weeks = db.scalars(
        select(ProgramWeek).where(ProgramWeek.client_id == client.id)
        .order_by(ProgramWeek.week_start.desc()).limit(8)
    ).all()
    return render(request, "client/program.html", user=client.user, weeks=weeks)
