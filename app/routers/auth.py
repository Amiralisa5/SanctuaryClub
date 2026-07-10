from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from ..audit import log_action
from ..models import Role, User
from ..security import get_current_user, get_db, hash_password, verify_password
from ..services import accounts as accounts_svc
from ..services import oauth as oauth_svc
from ..services import validation
from ..services.oauth import OAuthError
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
    return render(request, "login.html",
                  google_ready=oauth_svc.is_configured("google"),
                  strava_ready=oauth_svc.is_configured("strava"))


@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...),
          db=Depends(get_db)):
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        log_action(db, user, "auth.login_failed", "user", user.id if user else None, f"email={email}")
        db.commit()
        flash(request, "Invalid email or password.", "error")
        return RedirectResponse("/login", status_code=303)

    # Fresh session on every login: drops stale flashes and prevents fixation.
    request.session.clear()
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


# --- OAuth sign-in (Google, Strava) ---

@router.get("/auth/{provider}/start")
def oauth_start(request: Request, provider: str):
    try:
        state = oauth_svc.new_state()
        url = oauth_svc.authorize_url(provider, state)
    except OAuthError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/login", status_code=303)
    request.session["oauth_state"] = state
    return RedirectResponse(url, status_code=303)


@router.get("/auth/{provider}/callback")
def oauth_callback(request: Request, provider: str, code: str = "", state: str = "",
                   error: str = "", db=Depends(get_db)):
    if error or not code:
        flash(request, f"{provider.title()} sign-in was cancelled.", "error")
        return RedirectResponse("/login", status_code=303)
    if not state or state != request.session.pop("oauth_state", None):
        flash(request, "Sign-in session expired — please try again.", "error")
        return RedirectResponse("/login", status_code=303)
    try:
        token_payload = oauth_svc.exchange_code(provider, code)
        identity = oauth_svc.fetch_identity(provider, token_payload)
        user, created = accounts_svc.resolve_oauth_user(db, provider, identity)
    except OAuthError as exc:
        flash(request, str(exc), "error")
        return RedirectResponse("/login", status_code=303)
    if not user.is_active:
        flash(request, "This account has been deactivated.", "error")
        return RedirectResponse("/login", status_code=303)

    request.session.clear()
    request.session["uid"] = user.id
    log_action(db, user, "auth.login_oauth", "user", user.id, f"provider={provider}")
    db.commit()
    if created:
        flash(request, "Welcome to SanctuaryClub! An admin will assign your coach shortly.", "success")
    return RedirectResponse(home_for(user), status_code=303)


# --- Password reset ---

@router.get("/forgot-password")
def forgot_password_page(request: Request):
    return render(request, "forgot_password.html")


@router.post("/forgot-password")
def forgot_password(request: Request, email: str = Form(...), db=Depends(get_db)):
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user and user.is_active:
        raw = accounts_svc.issue_reset_token(db, user)
        accounts_svc.send_reset_email(db, user, raw)
        db.commit()
    # Same response either way: never reveal whether an email exists
    flash(request, "If that email has an account, a reset link is on its way.", "info")
    return RedirectResponse("/login", status_code=303)


@router.get("/reset-password")
def reset_password_page(request: Request, token: str = "", db=Depends(get_db)):
    user = accounts_svc.peek_reset_token(db, token) if token else None
    if user is None:
        flash(request, "That reset link is invalid or has expired — request a new one.", "error")
        return RedirectResponse("/forgot-password", status_code=303)
    return render(request, "reset_password.html", token=token)


@router.post("/reset-password")
def reset_password(request: Request, token: str = Form(...), password: str = Form(...),
                   confirm: str = Form(...), db=Depends(get_db)):
    errors = validation.validate_password(password, confirm)
    if errors:
        return render(request, "reset_password.html", token=token, errors=errors)
    user = accounts_svc.consume_reset_token(db, token)
    if user is None:
        flash(request, "That reset link is invalid or has expired — request a new one.", "error")
        return RedirectResponse("/forgot-password", status_code=303)
    user.password_hash = hash_password(password)
    log_action(db, user, "auth.password_reset", "user", user.id)
    db.commit()
    flash(request, "Password updated — sign in with your new password.", "success")
    return RedirectResponse("/login", status_code=303)
