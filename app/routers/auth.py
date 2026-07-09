from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from ..audit import log_action
from ..models import Role, User
from ..security import get_current_user, get_db, verify_password
from ..utils import flash
from ..web import render

router = APIRouter()

ROLE_HOME = {Role.ADMIN: "/admin", Role.COACH: "/coach", Role.CLIENT: "/client"}


def home_for(user: User) -> str:
    return ROLE_HOME[user.role]


@router.get("/")
def root(user=Depends(get_current_user)):
    return RedirectResponse(home_for(user) if user else "/login", status_code=303)


@router.get("/login")
def login_page(request: Request, user=Depends(get_current_user)):
    if user:
        return RedirectResponse(home_for(user), status_code=303)
    return render(request, "login.html")


@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...),
          db=Depends(get_db)):
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        log_action(db, user, "auth.login_failed", "user", user.id if user else None, f"email={email}")
        db.commit()
        flash(request, "Invalid email or password.", "error")
        return RedirectResponse("/login", status_code=303)

    request.session["uid"] = user.id
    log_action(db, user, "auth.login", "user", user.id)
    db.commit()
    return RedirectResponse(home_for(user), status_code=303)


@router.post("/logout")
def logout(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    if user:
        log_action(db, user, "auth.logout", "user", user.id)
        db.commit()
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
