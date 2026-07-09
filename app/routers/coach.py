from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from ..audit import log_action
from ..models import (
    AttendanceStatus,
    Booking,
    Client,
    Coach,
    Exercise,
    PlanMonth,
    ProgramWeek,
    Role,
    TimeSection,
    User,
    WorkoutDay,
    WorkoutItem,
)
from ..security import get_db, require_role
from ..services import attendance as attendance_svc
from ..services import scheduling
from ..services.scheduling import BookingError
from ..utils import flash, now
from ..web import render

router = APIRouter(prefix="/coach")


def current_coach(user: User = Depends(require_role(Role.COACH)), db=Depends(get_db)) -> Coach:
    coach = db.scalar(select(Coach).where(Coach.user_id == user.id))
    if coach is None:
        raise HTTPException(403, "No coach profile linked to this account.")
    return coach


def owned_client(db, coach: Coach, client_id: int) -> Client:
    client = db.get(Client, client_id)
    if client is None:
        raise HTTPException(404)
    if client.coach_id != coach.id:
        raise HTTPException(403, "This client is not assigned to you.")
    return client


def owned_booking(db, coach: Coach, booking_id: int) -> Booking:
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise HTTPException(404)
    if booking.client.coach_id != coach.id:
        raise HTTPException(403, "This booking belongs to another coach's client.")
    return booking


def _sections(db) -> list[TimeSection]:
    return db.scalars(select(TimeSection).order_by(TimeSection.index)).all()


@router.get("")
def dashboard(request: Request, coach: Coach = Depends(current_coach), db=Depends(get_db)):
    today = now().date()
    todays_bookings = db.scalars(
        select(Booking)
        .join(TimeSection)
        .where(Booking.coach_id == coach.id, Booking.date == today)
        .order_by(TimeSection.index)
    ).all()
    return render(request, "coach/dashboard.html", user=coach.user, coach=coach,
                  todays_bookings=todays_bookings, today=today,
                  client_count=len(coach.clients))


@router.get("/calendar")
def calendar_view(request: Request, year: int | None = None, month: int | None = None,
                  coach: Coach = Depends(current_coach), db=Depends(get_db)):
    from sqlalchemy import extract

    from ..utils import WEEKDAY_NAMES, month_grid, shift_month

    current = now()
    year = year or current.year
    month = month if month and 1 <= month <= 12 else current.month
    bookings = db.scalars(
        select(Booking)
        .join(TimeSection)
        .where(Booking.coach_id == coach.id,
               extract("year", Booking.date) == year,
               extract("month", Booking.date) == month)
        .order_by(Booking.date, TimeSection.index)
    ).all()
    by_day: dict = {}
    for b in bookings:
        by_day.setdefault(b.date, []).append(b)
    return render(request, "coach/calendar.html", user=coach.user,
                  grid=month_grid(year, month), by_day=by_day, bookings=bookings,
                  year=year, month=month, today=current.date(),
                  weekday_names=WEEKDAY_NAMES,
                  prev_ym=shift_month(year, month, -1), next_ym=shift_month(year, month, 1))


@router.get("/clients")
def clients_page(request: Request, coach: Coach = Depends(current_coach), db=Depends(get_db)):
    clients = db.scalars(
        select(Client).join(User).where(Client.coach_id == coach.id).order_by(User.full_name)
    ).all()
    return render(request, "coach/clients.html", user=coach.user, clients=clients)


@router.get("/clients/{client_id}")
def client_detail(request: Request, client_id: int, coach: Coach = Depends(current_coach),
                  db=Depends(get_db)):
    client = owned_client(db, coach, client_id)
    current = now()
    plan = scheduling.get_plan(db, client.id, current.year, current.month)
    summary = attendance_svc.monthly_summary(db, client.id, current.year, current.month)
    bookings = db.scalars(
        select(Booking).where(Booking.client_id == client.id)
        .order_by(Booking.date.desc()).limit(40)
    ).all()
    programs = db.scalars(
        select(ProgramWeek).where(ProgramWeek.client_id == client.id)
        .order_by(ProgramWeek.week_start.desc())
    ).all()
    return render(request, "coach/client_detail.html", user=coach.user, client=client,
                  plan=plan, summary=summary, bookings=bookings, programs=programs,
                  sections=_sections(db), current=current,
                  attendance_statuses=list(AttendanceStatus))


@router.post("/clients/{client_id}/plan")
def set_plan(request: Request, client_id: int, year: int = Form(...), month: int = Form(...),
             quota: str = Form(...), coach: Coach = Depends(current_coach), db=Depends(get_db)):
    client = owned_client(db, coach, client_id)
    quota_value = None if quota == "unlimited" else int(quota)
    if not (1 <= month <= 12):
        flash(request, "Month must be between 1 and 12.", "error")
        return RedirectResponse(f"/coach/clients/{client.id}", status_code=303)
    plan = scheduling.get_plan(db, client.id, year, month)
    if plan is None:
        plan = PlanMonth(client_id=client.id, year=year, month=month, quota=quota_value)
        db.add(plan)
    else:
        plan.quota = quota_value
    log_action(db, coach.user, "plan.set", "client", client.id,
               f"{year}-{month:02d} quota={quota_value or 'unlimited'}")
    db.commit()
    flash(request, f"Plan for {year}-{month:02d} set to {plan.quota_label} sessions.", "success")
    return RedirectResponse(f"/coach/clients/{client.id}", status_code=303)


@router.post("/clients/{client_id}/bookings")
def book_for_client(request: Request, client_id: int, booking_date: date = Form(...),
                    section_id: int = Form(...), coach: Coach = Depends(current_coach),
                    db=Depends(get_db)):
    client = owned_client(db, coach, client_id)
    section = db.get(TimeSection, section_id)
    if section is None:
        raise HTTPException(404)
    try:
        scheduling.create_booking(db, client, booking_date, section, coach.user)
        flash(request, f"Booked {booking_date} {section.label}.", "success")
    except BookingError as exc:
        flash(request, str(exc), "error")
    return RedirectResponse(f"/coach/clients/{client.id}", status_code=303)


@router.post("/clients/{client_id}/wizard")
def bulk_wizard(request: Request, client_id: int, year: int = Form(...), month: int = Form(...),
                section_id: int = Form(...), weekdays: list[int] = Form([]),
                coach: Coach = Depends(current_coach), db=Depends(get_db)):
    client = owned_client(db, coach, client_id)
    section = db.get(TimeSection, section_id)
    if section is None:
        raise HTTPException(404)
    if not weekdays:
        flash(request, "Select at least one weekday.", "error")
        return RedirectResponse(f"/coach/clients/{client.id}", status_code=303)
    results = scheduling.bulk_book(db, client, year, month, set(weekdays), section, coach.user)
    booked = sum(1 for _, outcome in results if outcome == "booked")
    skipped = [f"{d}: {outcome}" for d, outcome in results if outcome != "booked"]
    flash(request, f"Wizard booked {booked} session(s) for {year}-{month:02d}.", "success")
    for line in skipped[:8]:
        flash(request, f"Skipped {line}", "error")
    return RedirectResponse(f"/coach/clients/{client.id}", status_code=303)


@router.post("/bookings/{booking_id}/cancel")
def cancel_booking(request: Request, booking_id: int, coach: Coach = Depends(current_coach),
                   db=Depends(get_db)):
    booking = owned_booking(db, coach, booking_id)
    try:
        scheduling.cancel_booking(db, booking, coach.user)
        flash(request, "Booking cancelled.", "success")
    except BookingError as exc:
        flash(request, str(exc), "error")
    return RedirectResponse(f"/coach/clients/{booking.client_id}", status_code=303)


@router.post("/attendance/{booking_id}")
def mark_attendance(request: Request, booking_id: int, status: str = Form(...),
                    coach: Coach = Depends(current_coach), db=Depends(get_db)):
    booking = owned_booking(db, coach, booking_id)
    try:
        status_value = AttendanceStatus(status)
    except ValueError:
        raise HTTPException(400, "Invalid attendance status.")
    attendance_svc.set_attendance(db, booking, status_value, coach.user)
    flash(request, f"Attendance set to {status_value.value}.", "success")
    return RedirectResponse(f"/coach/clients/{booking.client_id}", status_code=303)


# --- Exercise library ---

@router.get("/exercises")
def exercises_page(request: Request, coach: Coach = Depends(current_coach), db=Depends(get_db)):
    exercises = db.scalars(select(Exercise).order_by(Exercise.name)).all()
    return render(request, "coach/exercises.html", user=coach.user, exercises=exercises)


@router.post("/exercises")
def create_exercise(request: Request, name: str = Form(...), description: str = Form(""),
                    tags: str = Form(""), coach: Coach = Depends(current_coach),
                    db=Depends(get_db)):
    name = name.strip()
    if db.scalar(select(Exercise).where(Exercise.name == name)):
        flash(request, f"Exercise '{name}' already exists.", "error")
    else:
        db.add(Exercise(name=name, description=description.strip(), tags=tags.strip()))
        log_action(db, coach.user, "exercise.create", detail=name)
        db.commit()
        flash(request, f"Exercise '{name}' added.", "success")
    return RedirectResponse("/coach/exercises", status_code=303)


# --- Program builder ---

def owned_program(db, coach: Coach, week_id: int) -> ProgramWeek:
    week = db.get(ProgramWeek, week_id)
    if week is None:
        raise HTTPException(404)
    if week.client.coach_id != coach.id:
        raise HTTPException(403, "This program belongs to another coach's client.")
    return week


@router.post("/clients/{client_id}/programs")
def create_program(request: Request, client_id: int, week_start: date = Form(...),
                   title: str = Form(""), coach: Coach = Depends(current_coach),
                   db=Depends(get_db)):
    client = owned_client(db, coach, client_id)
    week = ProgramWeek(client_id=client.id, coach_id=coach.id, week_start=week_start,
                       title=title.strip() or f"Week of {week_start}")
    db.add(week)
    db.flush()
    for day_index in range(7):
        db.add(WorkoutDay(program_week_id=week.id, day_index=day_index))
    log_action(db, coach.user, "program.create", "program_week", week.id,
               f"client={client.id} week_start={week_start}")
    db.commit()
    return RedirectResponse(f"/coach/programs/{week.id}", status_code=303)


@router.get("/programs/{week_id}")
def program_edit(request: Request, week_id: int, coach: Coach = Depends(current_coach),
                 db=Depends(get_db)):
    week = owned_program(db, coach, week_id)
    exercises = db.scalars(select(Exercise).order_by(Exercise.name)).all()
    return render(request, "coach/program_edit.html", user=coach.user, week=week,
                  exercises=exercises)


@router.post("/programs/{week_id}/days/{day_index}/items")
def add_item(request: Request, week_id: int, day_index: int,
             exercise_name: str = Form(...), sets: int = Form(3), reps: str = Form("10"),
             target_weight: str = Form(""), rest_seconds: int = Form(90),
             notes: str = Form(""), coach: Coach = Depends(current_coach), db=Depends(get_db)):
    week = owned_program(db, coach, week_id)
    day = next((d for d in week.days if d.day_index == day_index), None)
    if day is None:
        raise HTTPException(404)
    name = exercise_name.strip()
    exercise = db.scalar(select(Exercise).where(Exercise.name == name))
    if exercise is None:
        exercise = Exercise(name=name)
        db.add(exercise)
        db.flush()
    db.add(WorkoutItem(workout_day_id=day.id, exercise_id=exercise.id,
                       position=len(day.items), sets=sets, reps=reps.strip(),
                       target_weight=target_weight.strip(), rest_seconds=rest_seconds,
                       notes=notes.strip()))
    log_action(db, coach.user, "program.add_item", "program_week", week.id,
               f"day={day_index} exercise={name}")
    db.commit()
    return RedirectResponse(f"/coach/programs/{week.id}", status_code=303)


@router.post("/items/{item_id}/delete")
def delete_item(request: Request, item_id: int, coach: Coach = Depends(current_coach),
                db=Depends(get_db)):
    item = db.get(WorkoutItem, item_id)
    if item is None:
        raise HTTPException(404)
    week = item.day.week
    if week.client.coach_id != coach.id:
        raise HTTPException(403)
    db.delete(item)
    log_action(db, coach.user, "program.delete_item", "program_week", week.id)
    db.commit()
    return RedirectResponse(f"/coach/programs/{week.id}", status_code=303)


@router.post("/programs/{week_id}/days/{day_index}")
def update_day(request: Request, week_id: int, day_index: int, title: str = Form(""),
               notes: str = Form(""), coach: Coach = Depends(current_coach), db=Depends(get_db)):
    week = owned_program(db, coach, week_id)
    day = next((d for d in week.days if d.day_index == day_index), None)
    if day is None:
        raise HTTPException(404)
    day.title = title.strip()
    day.notes = notes.strip()
    db.commit()
    return RedirectResponse(f"/coach/programs/{week.id}", status_code=303)
