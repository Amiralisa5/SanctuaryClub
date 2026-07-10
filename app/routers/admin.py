from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from .. import config
from ..audit import log_action
from ..models import (
    AuditLog,
    Booking,
    CapacityOverride,
    Client,
    Coach,
    EmailLog,
    Role,
    TimeSection,
    User,
)
from ..security import get_db, hash_password, require_role
from ..services import scheduling
from ..utils import flash, now
from ..web import render

router = APIRouter(prefix="/admin", dependencies=[Depends(require_role(Role.ADMIN))])


def _user(user=Depends(require_role(Role.ADMIN))):
    return user


@router.get("")
def dashboard(request: Request, user=Depends(_user), db=Depends(get_db)):
    stats = {
        "coaches": db.scalar(select(func.count(Coach.id))) or 0,
        "clients": db.scalar(select(func.count(Client.id))) or 0,
        "bookings_today": db.scalar(
            select(func.count(Booking.id)).where(Booking.date == now().date())
        ) or 0,
    }
    recent = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(10)).all()
    return render(request, "admin/dashboard.html", user=user, stats=stats, recent=recent)


@router.get("/users")
def users_page(request: Request, user=Depends(_user), db=Depends(get_db)):
    coaches = db.scalars(select(Coach).join(User).order_by(User.full_name)).all()
    clients = db.scalars(select(Client).join(User).order_by(User.full_name)).all()
    return render(request, "admin/users.html", user=user, coaches=coaches, clients=clients)


def _create_user(db, email: str, password: str, full_name: str, role: Role) -> User:
    email = email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        raise ValueError(f"A user with email {email} already exists.")
    new_user = User(email=email, password_hash=hash_password(password),
                    full_name=full_name.strip(), role=role)
    db.add(new_user)
    db.flush()
    return new_user


@router.post("/coaches")
def create_coach(request: Request, full_name: str = Form(...), email: str = Form(...),
                 password: str = Form(...), user=Depends(_user), db=Depends(get_db)):
    try:
        new_user = _create_user(db, email, password, full_name, Role.COACH)
    except ValueError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/admin/users", status_code=303)
    coach = Coach(user_id=new_user.id)
    db.add(coach)
    db.flush()
    log_action(db, user, "admin.create_coach", "coach", coach.id, f"email={new_user.email}")
    db.commit()
    flash(request, f"Coach {new_user.full_name} created.", "success")
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/clients")
def create_client(request: Request, full_name: str = Form(...), email: str = Form(...),
                  password: str = Form(...), coach_id: int = Form(...),
                  user=Depends(_user), db=Depends(get_db)):
    if db.get(Coach, coach_id) is None:
        flash(request, "Selected coach does not exist.", "error")
        return RedirectResponse("/admin/users", status_code=303)
    try:
        new_user = _create_user(db, email, password, full_name, Role.CLIENT)
    except ValueError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/admin/users", status_code=303)
    client = Client(user_id=new_user.id, coach_id=coach_id)
    db.add(client)
    db.flush()
    log_action(db, user, "admin.create_client", "client", client.id,
               f"email={new_user.email} coach={coach_id}")
    db.commit()
    flash(request, f"Client {new_user.full_name} created.", "success")
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{user_id}/toggle")
def toggle_user(request: Request, user_id: int, user=Depends(_user), db=Depends(get_db)):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404)
    if target.id == user.id:
        flash(request, "You cannot deactivate your own account.", "error")
        return RedirectResponse("/admin/users", status_code=303)
    target.is_active = not target.is_active
    log_action(db, user, "admin.toggle_user", "user", target.id,
               f"active={target.is_active}")
    db.commit()
    flash(request, f"{target.full_name} is now {'active' if target.is_active else 'inactive'}.", "success")
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/clients/{client_id}/reassign")
def reassign_client(request: Request, client_id: int, coach_id: int = Form(...),
                    user=Depends(_user), db=Depends(get_db)):
    client = db.get(Client, client_id)
    coach = db.get(Coach, coach_id)
    if client is None or coach is None:
        raise HTTPException(404)
    old = client.coach_id
    client.coach_id = coach.id
    log_action(db, user, "admin.reassign_client", "client", client.id,
               f"coach {old} -> {coach.id}")
    from ..services import notifications as notif_svc
    notif_svc.notify_coach_assigned(db, client, coach)
    db.commit()
    flash(request, f"{client.user.full_name} reassigned to {coach.user.full_name}.", "success")
    return RedirectResponse("/admin/users", status_code=303)


@router.get("/capacity")
def capacity_page(request: Request, user=Depends(_user), db=Depends(get_db)):
    overrides = db.scalars(
        select(CapacityOverride).order_by(CapacityOverride.date, CapacityOverride.section_id)
    ).all()
    sections = db.scalars(select(TimeSection).order_by(TimeSection.index)).all()
    coaches = db.scalars(select(Coach).join(User).order_by(User.full_name)).all()
    defaults = {
        "gym": scheduling.get_setting_int(db, "gym_default_capacity", config.GYM_DEFAULT_CAPACITY),
        "coach": scheduling.get_setting_int(db, "coach_default_capacity", config.COACH_DEFAULT_CAPACITY),
    }
    return render(request, "admin/capacity.html", user=user, overrides=overrides,
                  sections=sections, coaches=coaches, defaults=defaults)


@router.post("/capacity/defaults")
def update_defaults(request: Request, gym_default: int = Form(...),
                    coach_default: int = Form(...), user=Depends(_user), db=Depends(get_db)):
    if gym_default < 0 or coach_default < 0:
        flash(request, "Capacities must be zero or positive.", "error")
        return RedirectResponse("/admin/capacity", status_code=303)
    scheduling.set_setting(db, "gym_default_capacity", str(gym_default))
    scheduling.set_setting(db, "coach_default_capacity", str(coach_default))
    log_action(db, user, "admin.capacity_defaults", detail=f"gym={gym_default} coach={coach_default}")
    db.commit()
    flash(request, "Default capacities updated.", "success")
    return RedirectResponse("/admin/capacity", status_code=303)


@router.post("/capacity/overrides")
def create_override(request: Request, override_date: date = Form(...),
                    section_id: int = Form(...), capacity: int = Form(...),
                    coach_id: str = Form(""), user=Depends(_user), db=Depends(get_db)):
    coach_value = int(coach_id) if coach_id else None
    if capacity < 0:
        flash(request, "Capacity must be zero or positive.", "error")
        return RedirectResponse("/admin/capacity", status_code=303)
    existing = db.scalar(
        select(CapacityOverride).where(
            CapacityOverride.date == override_date,
            CapacityOverride.section_id == section_id,
            (CapacityOverride.coach_id == coach_value) if coach_value is not None
            else CapacityOverride.coach_id.is_(None),
        )
    )
    if existing:
        existing.capacity = capacity
    else:
        db.add(CapacityOverride(date=override_date, section_id=section_id,
                                coach_id=coach_value, capacity=capacity))
    log_action(db, user, "admin.capacity_override", detail=(
        f"{override_date} section={section_id} coach={coach_value} capacity={capacity}"))
    db.commit()
    flash(request, "Capacity override saved.", "success")
    return RedirectResponse("/admin/capacity", status_code=303)


@router.post("/capacity/overrides/{override_id}/delete")
def delete_override(request: Request, override_id: int, user=Depends(_user), db=Depends(get_db)):
    override = db.get(CapacityOverride, override_id)
    if override is None:
        raise HTTPException(404)
    db.delete(override)
    log_action(db, user, "admin.capacity_override_delete", entity_id=override_id)
    db.commit()
    flash(request, "Override removed.", "success")
    return RedirectResponse("/admin/capacity", status_code=303)


@router.get("/audit")
def audit_page(request: Request, user=Depends(_user), db=Depends(get_db)):
    logs = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(200)).all()
    return render(request, "admin/audit.html", user=user, logs=logs)


@router.get("/classes")
def classes_page(request: Request, year: int | None = None, month: int | None = None,
                 day: int | None = None, user=Depends(_user), db=Depends(get_db)):
    import calendar as pycal

    from ..services import classes as classes_svc
    from ..utils import shift_month

    current = now()
    year = year or current.year
    month = month if month and 1 <= month <= 12 else current.month
    overview = classes_svc.month_overview(db, year, month)
    day_date = roster = None
    if day and 1 <= day <= pycal.monthrange(year, month)[1]:
        day_date = date(year, month, day)
        roster = classes_svc.day_roster(db, day_date)
    return render(request, "admin/classes.html", user=user, overview=overview,
                  year=year, month=month, day=day, day_date=day_date, roster=roster,
                  today=current.date(), base_url="/admin/classes", show_coach=True,
                  month_name=pycal.month_name[month],
                  prev_ym=shift_month(year, month, -1), next_ym=shift_month(year, month, 1))


@router.get("/emails")
def emails_page(request: Request, user=Depends(_user), db=Depends(get_db)):
    emails = db.scalars(select(EmailLog).order_by(EmailLog.created_at.desc()).limit(100)).all()
    smtp_configured = bool(config.SMTP_HOST)
    return render(request, "admin/emails.html", user=user, emails=emails,
                  smtp_configured=smtp_configured)
