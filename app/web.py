from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from .database import SessionLocal
from .utils import pop_flashes

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _unread_count(user_id: int) -> int:
    from .models import Notification
    with SessionLocal() as session:
        return session.scalar(
            select(func.count(Notification.id)).where(
                Notification.user_id == user_id, Notification.read.is_(False))
        ) or 0


def render(request: Request, name: str, user=None, status_code: int = 200, **context):
    context.update({
        "user": user,
        "flashes": pop_flashes(request),
        "unread_count": _unread_count(user.id) if user is not None else 0,
    })
    return templates.TemplateResponse(request, name, context, status_code=status_code)
