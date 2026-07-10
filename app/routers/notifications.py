from fastapi import APIRouter, Depends, Request
from sqlalchemy import select, update

from ..models import Notification, User
from ..security import get_db, require_user
from ..web import render

router = APIRouter(prefix="/notifications")


@router.get("")
def notifications_page(request: Request, user: User = Depends(require_user), db=Depends(get_db)):
    items = db.scalars(
        select(Notification).where(Notification.user_id == user.id)
        .order_by(Notification.created_at.desc()).limit(50)
    ).all()
    unread_ids = [n.id for n in items if not n.read]
    # Capture read-state for rendering, then mark everything as seen
    seen = [(n, n.id in unread_ids) for n in items]
    if unread_ids:
        db.execute(update(Notification).where(Notification.id.in_(unread_ids))
                   .values(read=True))
        db.commit()
    return render(request, "notifications.html", user=user, seen=seen)
