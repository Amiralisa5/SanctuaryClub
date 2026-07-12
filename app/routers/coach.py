import uuid
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from .. import config
from ..audit import log_action
from ..models import (
    AttendanceStatus,
    Booking,
    Client,
    Coach,
    Exercise,
    PlanMonth,
    ProgramTemplate,
    ProgramWeek,
    Role,
    TimeSection,
    User,
    WorkoutDay,
    WorkoutItem,
)
from ..security import get_db, require_role
from ..services import attendance as attendance_svc
from ..services import notifications as notifications_svc
from ..services import programs as programs_svc
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


@router.get("/classes")
def classes_page(request: Request, year: int | None = None, month: int | None = None,
                 day: int | None = None, coach: Coach = Depends(current_coach),
                 db=Depends(get_db)):
    import calendar as pycal

    from ..services import classes as classes_svc
    from ..utils import shift_month

    current = now()
    year = year or current.year
    month = month if month and 1 <= month <= 12 else current.month
    overview = classes_svc.month_overview(db, year, month, coach_id=coach.id)
    day_date = roster = None
    if day and 1 <= day <= pycal.monthrange(year, month)[1]:
        day_date = date(year, month, day)
        roster = classes_svc.day_roster(db, day_date, coach_id=coach.id)
    return render(request, "coach/classes.html", user=coach.user, overview=overview,
                  year=year, month=month, day=day, day_date=day_date, roster=roster,
                  today=current.date(), base_url="/coach/classes", show_coach=False,
                  month_name=pycal.month_name[month],
                  prev_ym=shift_month(year, month, -1), next_ym=shift_month(year, month, 1))


@router.get("/availability")
def availability_page(request: Request, year: int | None = None, month: int | None = None,
                      coach: Coach = Depends(current_coach), db=Depends(get_db)):
    from ..services import availability as avail_svc
    from ..utils import WEEKDAY_NAMES, month_grid, shift_month

    current = now()
    year = year or current.year
    month = month if month and 1 <= month <= 12 else current.month
    return render(request, "coach/availability.html", user=coach.user,
                  grid=month_grid(year, month),
                  availability=avail_svc.month_availability(db, coach, year, month),
                  sections=_sections(db), year=year, month=month, today=current.date(),
                  weekday_names=WEEKDAY_NAMES,
                  prev_ym=shift_month(year, month, -1), next_ym=shift_month(year, month, 1))


@router.post("/availability/block")
def block_slot(request: Request, block_date: date = Form(...), section_id: int = Form(...),
               coach: Coach = Depends(current_coach), db=Depends(get_db)):
    from ..services import availability as avail_svc
    section = db.get(TimeSection, section_id)
    if section is None:
        raise HTTPException(404)
    avail_svc.block_slot(db, coach, block_date, section_id, coach.user)
    flash(request, f"Blocked {block_date} {section.label} — clients can no longer book it.", "success")
    return RedirectResponse(f"/coach/availability?year={block_date.year}&month={block_date.month}",
                            status_code=303)


@router.post("/availability/unblock")
def unblock_slot(request: Request, block_date: date = Form(...), section_id: int = Form(...),
                 coach: Coach = Depends(current_coach), db=Depends(get_db)):
    from ..services import availability as avail_svc
    avail_svc.unblock_slot(db, coach, block_date, section_id, coach.user)
    flash(request, f"Unblocked {block_date} — the default capacity applies again.", "success")
    return RedirectResponse(f"/coach/availability?year={block_date.year}&month={block_date.month}",
                            status_code=303)


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
    templates = db.scalars(
        select(ProgramTemplate).where(ProgramTemplate.coach_id == coach.id)
        .order_by(ProgramTemplate.title)
    ).all()
    return render(request, "coach/client_detail.html", user=coach.user, client=client,
                  plan=plan, summary=summary, bookings=bookings, programs=programs,
                  templates=templates, sections=_sections(db), current=current,
                  attendance_statuses=list(AttendanceStatus))


@router.get("/clients/{client_id}/activities")
def client_activities(request: Request, client_id: int, coach: Coach = Depends(current_coach),
                      db=Depends(get_db)):
    from ..services.health import queries as health_q
    client = owned_client(db, coach, client_id)
    return render(request, "client/activities.html", user=coach.user, client=client,
                  can_edit=False,
                  activities=health_q.ask(db, health_q.ListActivities(client_id=client.id)),
                  stats=health_q.ask(db, health_q.ActivityStats(client_id=client.id)),
                  weeks=health_q.ask(db, health_q.WeeklyVolume(client_id=client.id)),
                  connections=health_q.ask(db, health_q.Connections(client_id=client.id)))


@router.get("/clients/{client_id}/progress")
def client_progress(request: Request, client_id: int, coach: Coach = Depends(current_coach),
                    db=Depends(get_db)):
    from ..services import metrics
    client = owned_client(db, coach, client_id)
    return render(request, "coach/client_progress.html", user=coach.user, client=client,
                  **metrics.progress_context(db, client))


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

def _save_media(upload: UploadFile) -> str:
    """Validate and store an uploaded demo file; returns the stored filename."""
    ext = Path(upload.filename).suffix.lower()
    if ext not in config.ALLOWED_MEDIA_EXTENSIONS:
        allowed = ", ".join(sorted(config.ALLOWED_MEDIA_EXTENSIONS))
        raise ValueError(f"File type '{ext or 'unknown'}' is not allowed. Use one of: {allowed}.")
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    filename = f"{uuid.uuid4().hex}{ext}"
    target = Path(config.UPLOAD_DIR) / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with target.open("wb") as out:
        while chunk := upload.file.read(1024 * 1024):
            written += len(chunk)
            if written > limit:
                out.close()
                target.unlink(missing_ok=True)
                raise ValueError(f"File is larger than the {config.MAX_UPLOAD_MB} MB limit.")
            out.write(chunk)
    return filename

EXERCISE_FILTERS = {
    "Muscles": ["abdominals", "biceps", "calves", "chest", "forearms", "glutes",
                "hamstrings", "lats", "lower back", "middle back", "quadriceps",
                "shoulders", "traps", "triceps"],
    "Equipment": ["barbell", "dumbbell", "cable", "machine", "kettlebells",
                  "bands", "body only"],
    "Level": ["beginner", "intermediate", "expert"],
    "Type": ["strength", "stretching", "cardio", "plyometrics", "powerlifting"],
}


@router.get("/exercises")
def exercises_page(request: Request, q: str = "", tag: str = "",
                   coach: Coach = Depends(current_coach), db=Depends(get_db)):
    from sqlalchemy import func, or_

    stmt = select(Exercise)
    if q.strip():
        needle = f"%{q.strip()}%"
        stmt = stmt.where(or_(Exercise.name.ilike(needle), Exercise.tags.ilike(needle)))
    if tag.strip():
        stmt = stmt.where(Exercise.tags.ilike(f"%{tag.strip()}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    exercises = db.scalars(stmt.order_by(Exercise.name).limit(100)).all()
    return render(request, "coach/exercises.html", user=coach.user, exercises=exercises,
                  total=total, q=q, tag=tag, filters=EXERCISE_FILTERS)


@router.post("/exercises")
def create_exercise(request: Request, name: str = Form(...), description: str = Form(""),
                    tags: str = Form(""), video_url: str = Form(""),
                    media: UploadFile | None = File(None),
                    coach: Coach = Depends(current_coach), db=Depends(get_db)):
    name = name.strip()
    if db.scalar(select(Exercise).where(Exercise.name == name)):
        flash(request, f"Exercise '{name}' already exists.", "error")
        return RedirectResponse("/coach/exercises", status_code=303)
    exercise = Exercise(name=name, description=description.strip(), tags=tags.strip(),
                        video_url=video_url.strip())
    try:
        if media and media.filename:
            exercise.media_path = _save_media(media)
    except ValueError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/coach/exercises", status_code=303)
    db.add(exercise)
    log_action(db, coach.user, "exercise.create", detail=name)
    db.commit()
    flash(request, f"Exercise '{name}' added.", "success")
    return RedirectResponse("/coach/exercises", status_code=303)


@router.post("/exercises/{exercise_id}/media")
def update_exercise_media(request: Request, exercise_id: int, video_url: str = Form(""),
                          media: UploadFile | None = File(None),
                          coach: Coach = Depends(current_coach), db=Depends(get_db)):
    exercise = db.get(Exercise, exercise_id)
    if exercise is None:
        raise HTTPException(404)
    exercise.video_url = video_url.strip()
    try:
        if media and media.filename:
            exercise.media_path = _save_media(media)
    except ValueError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/coach/exercises", status_code=303)
    log_action(db, coach.user, "exercise.media", "exercise", exercise.id,
               f"url={exercise.video_url or '-'} file={exercise.media_path or '-'}")
    db.commit()
    flash(request, f"Demo media updated for '{exercise.name}'.", "success")
    return RedirectResponse("/coach/exercises", status_code=303)


# --- Program templates ---

def owned_template(db, coach: Coach, template_id: int) -> ProgramTemplate:
    template = db.get(ProgramTemplate, template_id)
    if template is None:
        raise HTTPException(404)
    if template.coach_id != coach.id:
        raise HTTPException(403, "This template belongs to another coach.")
    return template


@router.get("/templates")
def templates_page(request: Request, coach: Coach = Depends(current_coach), db=Depends(get_db)):
    templates = db.scalars(
        select(ProgramTemplate).where(ProgramTemplate.coach_id == coach.id)
        .order_by(ProgramTemplate.title)
    ).all()
    clients = db.scalars(
        select(Client).join(User, Client.user_id == User.id)
        .where(Client.coach_id == coach.id).order_by(User.full_name)
    ).all()
    return render(request, "coach/templates.html", user=coach.user,
                  templates=templates, clients=clients)


@router.post("/programs/{week_id}/template")
def save_as_template(request: Request, week_id: int, title: str = Form(""),
                     coach: Coach = Depends(current_coach), db=Depends(get_db)):
    week = owned_program(db, coach, week_id)
    template = programs_svc.save_week_as_template(db, week, title, coach.user)
    flash(request, f"Template '{template.title}' saved — apply it to any client.", "success")
    return RedirectResponse("/coach/templates", status_code=303)


@router.post("/clients/{client_id}/programs/from-template")
def apply_template(request: Request, client_id: int, template_id: int = Form(...),
                   week_start: date = Form(...), coach: Coach = Depends(current_coach),
                   db=Depends(get_db)):
    client = owned_client(db, coach, client_id)
    template = owned_template(db, coach, template_id)
    week = programs_svc.apply_template(db, template, client, week_start, coach.user)
    flash(request, f"Applied '{template.title}' to {client.user.full_name}.", "success")
    return RedirectResponse(f"/coach/programs/{week.id}", status_code=303)


@router.post("/templates/{template_id}/delete")
def delete_template(request: Request, template_id: int,
                    coach: Coach = Depends(current_coach), db=Depends(get_db)):
    template = owned_template(db, coach, template_id)
    log_action(db, coach.user, "template.delete", "program_template", template.id, template.title)
    db.delete(template)
    db.commit()
    flash(request, "Template deleted.", "success")
    return RedirectResponse("/coach/templates", status_code=303)


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
    notifications_svc.notify_program_published(db, week)
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
