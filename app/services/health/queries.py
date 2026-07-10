"""Read side: queries over the Activity read model."""
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select

from ... import utils
from ...models import Activity, HealthConnection


@dataclass
class ListActivities:
    client_id: int
    sport: str | None = None
    limit: int = 50


@dataclass
class ActivityStats:
    client_id: int


@dataclass
class WeeklyVolume:
    client_id: int
    weeks: int = 8


@dataclass
class Connections:
    client_id: int


def ask(db, query):
    return _HANDLERS[type(query)](db, query)


def _list(db, q: ListActivities):
    stmt = (select(Activity)
            .where(Activity.client_id == q.client_id)
            .order_by(Activity.start_time.desc())
            .limit(q.limit))
    if q.sport:
        stmt = stmt.where(Activity.sport_type == q.sport)
    return db.scalars(stmt).all()


def _stats(db, q: ActivityStats) -> dict:
    now = utils.now()
    week_start = (now - timedelta(days=(now.weekday() - 5) % 7)).replace(
        hour=0, minute=0, second=0, microsecond=0)  # Saturday, gym week
    total, total_seconds = db.execute(
        select(func.count(Activity.id), func.coalesce(func.sum(Activity.duration_seconds), 0))
        .where(Activity.client_id == q.client_id)
    ).one()
    week_count, week_seconds, week_distance = db.execute(
        select(func.count(Activity.id),
               func.coalesce(func.sum(Activity.duration_seconds), 0),
               func.coalesce(func.sum(Activity.distance_m), 0.0))
        .where(Activity.client_id == q.client_id, Activity.start_time >= week_start)
    ).one()
    return {
        "total": total,
        "total_hours": round(total_seconds / 3600, 1),
        "week_count": week_count,
        "week_minutes": round(week_seconds / 60),
        "week_km": round(week_distance / 1000, 1),
    }


def _weekly_volume(db, q: WeeklyVolume) -> list[dict]:
    """Total minutes per gym week (Saturday-start) for the last N weeks."""
    now = utils.now()
    this_week_start = (now - timedelta(days=(now.weekday() - 5) % 7)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    out = []
    for offset in range(q.weeks - 1, -1, -1):
        start = this_week_start - timedelta(weeks=offset)
        end = start + timedelta(weeks=1)
        seconds = db.scalar(
            select(func.coalesce(func.sum(Activity.duration_seconds), 0))
            .where(Activity.client_id == q.client_id,
                   Activity.start_time >= start, Activity.start_time < end)
        )
        out.append({"label": start.strftime("%b %-d"), "minutes": round(seconds / 60)})
    return out


def _connections(db, q: Connections):
    return db.scalars(select(HealthConnection)
                      .where(HealthConnection.client_id == q.client_id)
                      .order_by(HealthConnection.provider)).all()


_HANDLERS = {
    ListActivities: _list,
    ActivityStats: _stats,
    WeeklyVolume: _weekly_volume,
    Connections: _connections,
}
