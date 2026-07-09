from .models import AuditLog, User
from .utils import now


def log_action(db, actor: User | None, action: str, entity: str = "",
               entity_id: int | None = None, detail: str = "") -> None:
    db.add(AuditLog(
        user_id=actor.id if actor else None,
        action=action,
        entity=entity,
        entity_id=entity_id,
        detail=detail,
        created_at=now(),
    ))
