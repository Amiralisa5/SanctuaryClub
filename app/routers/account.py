from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from ..audit import log_action
from ..models import User
from ..security import get_db, hash_password, require_user, verify_password
from ..services import validation
from ..utils import flash
from ..web import render

router = APIRouter(prefix="/account")


def _context(user: User) -> dict:
    return {
        "client": user.client_profile,
        "has_password": bool(user.password_hash),
        "providers": [a.provider for a in user.oauth_accounts],
    }


@router.get("")
def account_page(request: Request, user: User = Depends(require_user), db=Depends(get_db)):
    return render(request, "account.html", user=user, errors={}, **_context(user))


@router.post("")
async def update_profile(request: Request, user: User = Depends(require_user), db=Depends(get_db)):
    form = dict(await request.form())
    clean, errors = validation.validate_profile(form)
    if errors:
        return render(request, "account.html", user=user, errors=errors,
                      values=form, **_context(user))
    user.full_name = clean["full_name"]
    user.phone = clean["phone"]
    client = user.client_profile
    if client is not None:
        client.birth_date = clean["birth_date"]
        client.gender = clean["gender"]
        client.height_cm = clean["height_cm"]
        client.goal = clean["goal"]
    log_action(db, user, "account.update_profile", "user", user.id)
    db.commit()
    flash(request, "Profile saved.", "success")
    return RedirectResponse("/account", status_code=303)


@router.post("/password")
async def change_password(request: Request, user: User = Depends(require_user),
                          db=Depends(get_db)):
    form = await request.form()
    current = form.get("current", "")
    password = form.get("password", "")
    confirm = form.get("confirm", "")
    errors = validation.validate_password(password, confirm)
    # OAuth-only accounts (no password yet) may set one without a current password
    if user.password_hash and not verify_password(current, user.password_hash):
        errors["current"] = "Current password is incorrect."
    if errors:
        return render(request, "account.html", user=user, errors=errors, **_context(user))
    user.password_hash = hash_password(password)
    log_action(db, user, "account.change_password", "user", user.id)
    db.commit()
    flash(request, "Password updated.", "success")
    return RedirectResponse("/account", status_code=303)
