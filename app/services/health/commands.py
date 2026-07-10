"""Write side: commands that mutate the activity store."""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select

from ... import config, utils
from ...audit import log_action
from ...models import Activity, Client, HealthConnection, User
from .. import validation

PUSH_PROVIDERS = {"apple_health", "samsung_health", "manual"}


class HealthError(Exception):
    """Command failure; the message is safe to show the user."""


@dataclass
class ImportActivities:
    """Push-based ingest: Apple Health / Samsung Health bridges or JSON export."""
    client_id: int
    provider: str
    items: list = field(default_factory=list)
    actor_id: int | None = None


@dataclass
class AddManualActivity:
    client_id: int
    fields: dict = field(default_factory=dict)
    actor_id: int | None = None


@dataclass
class SyncStrava:
    client_id: int
    actor_id: int | None = None


@dataclass
class DeleteActivity:
    client_id: int
    activity_id: int
    actor_id: int | None = None


def handle(db, command):
    handler = _HANDLERS[type(command)]
    return handler(db, command)


def _import_activities(db, cmd: ImportActivities) -> dict:
    if cmd.provider not in PUSH_PROVIDERS:
        raise HealthError(f"Unknown import provider '{cmd.provider}'.")
    if not isinstance(cmd.items, list) or not cmd.items:
        raise HealthError("Import payload must be a non-empty JSON list of activities.")
    if len(cmd.items) > 500:
        raise HealthError("Import at most 500 activities per request.")

    imported, skipped = 0, []
    for index, item in enumerate(cmd.items):
        if not isinstance(item, dict):
            skipped.append(f"item {index + 1}: not an object")
            continue
        clean, error = validation.validate_activity_item(item)
        if error:
            skipped.append(f"item {index + 1}: {error}")
            continue
        external_id = str(item.get("external_id") or "").strip() or uuid.uuid4().hex
        exists = db.scalar(select(Activity).where(
            Activity.client_id == cmd.client_id,
            Activity.provider == cmd.provider,
            Activity.external_id == external_id,
        ))
        if exists:
            skipped.append(f"item {index + 1}: duplicate of an already-recorded activity")
            continue
        db.add(Activity(client_id=cmd.client_id, provider=cmd.provider,
                        external_id=external_id, **clean))
        imported += 1

    _touch_connection(db, cmd.client_id, cmd.provider)
    log_action(db, _actor(db, cmd.actor_id), "health.import", "client", cmd.client_id,
               f"provider={cmd.provider} imported={imported} skipped={len(skipped)}")
    db.commit()
    return {"imported": imported, "skipped": skipped}


def _add_manual(db, cmd: AddManualActivity) -> Activity:
    clean, error = validation.validate_activity_item(cmd.fields)
    if error:
        raise HealthError(error)
    activity = Activity(client_id=cmd.client_id, provider="manual",
                        external_id=uuid.uuid4().hex, **clean)
    db.add(activity)
    db.flush()
    log_action(db, _actor(db, cmd.actor_id), "health.add_manual", "activity", activity.id,
               f"client={cmd.client_id} {clean['sport_type']}")
    db.commit()
    return activity


def _delete(db, cmd: DeleteActivity) -> None:
    activity = db.get(Activity, cmd.activity_id)
    if activity is None or activity.client_id != cmd.client_id:
        raise HealthError("Activity not found.")
    log_action(db, _actor(db, cmd.actor_id), "health.delete", "activity", activity.id,
               f"client={cmd.client_id}")
    db.delete(activity)
    db.commit()


def _sync_strava(db, cmd: SyncStrava) -> dict:
    connection = db.scalar(select(HealthConnection).where(
        HealthConnection.client_id == cmd.client_id,
        HealthConnection.provider == "strava",
    ))
    if connection is None or not connection.access_token:
        raise HealthError("Strava is not connected — sign in with Strava first.")

    _refresh_strava_token(db, connection)
    raw_activities = _strava_fetch_activities(connection.access_token)

    imported = 0
    for raw in raw_activities:
        external_id = str(raw.get("id", ""))
        if not external_id:
            continue
        exists = db.scalar(select(Activity).where(
            Activity.client_id == cmd.client_id,
            Activity.provider == "strava",
            Activity.external_id == external_id,
        ))
        if exists:
            continue
        mapped = _map_strava(raw)
        if mapped is None:
            continue
        db.add(Activity(client_id=cmd.client_id, provider="strava",
                        external_id=external_id, **mapped))
        imported += 1

    connection.last_sync_at = utils.now()
    connection.status = "connected"
    log_action(db, _actor(db, cmd.actor_id), "health.sync_strava", "client", cmd.client_id,
               f"imported={imported}")
    db.commit()
    return {"imported": imported, "fetched": len(raw_activities)}


_HANDLERS = {
    ImportActivities: _import_activities,
    AddManualActivity: _add_manual,
    SyncStrava: _sync_strava,
    DeleteActivity: _delete,
}


def sync_all_strava(db) -> dict:
    """Nightly job: sync every connected Strava account; failures don't stop the run."""
    connections = db.scalars(select(HealthConnection).where(
        HealthConnection.provider == "strava",
        HealthConnection.status == "connected",
        HealthConnection.access_token != "",
    )).all()
    synced, imported, failed = 0, 0, 0
    for connection in connections:
        try:
            result = handle(db, SyncStrava(client_id=connection.client_id))
            imported += result["imported"]
            synced += 1
        except HealthError:
            failed += 1
            db.rollback()
    return {"synced": synced, "imported": imported, "failed": failed}


def _actor(db, actor_id: int | None) -> User | None:
    return db.get(User, actor_id) if actor_id else None


def _touch_connection(db, client_id: int, provider: str) -> None:
    connection = db.scalar(select(HealthConnection).where(
        HealthConnection.client_id == client_id, HealthConnection.provider == provider))
    if connection is None:
        connection = HealthConnection(client_id=client_id, provider=provider,
                                      status="connected")
        db.add(connection)
    connection.last_sync_at = utils.now()


def _refresh_strava_token(db, connection: HealthConnection) -> None:
    if connection.expires_at is None or connection.expires_at > utils.now() + timedelta(minutes=5):
        return
    if not connection.refresh_token:
        raise HealthError("Strava session expired — sign in with Strava again.")
    response = httpx.post("https://www.strava.com/oauth/token", data={
        "client_id": config.STRAVA_CLIENT_ID,
        "client_secret": config.STRAVA_CLIENT_SECRET,
        "grant_type": "refresh_token",
        "refresh_token": connection.refresh_token,
    }, timeout=15)
    if response.status_code != 200:
        connection.status = "error"
        raise HealthError("Could not refresh the Strava connection — sign in with Strava again.")
    payload = response.json()
    connection.access_token = payload.get("access_token", "")
    connection.refresh_token = payload.get("refresh_token", connection.refresh_token)
    if payload.get("expires_at"):
        connection.expires_at = datetime.fromtimestamp(
            int(payload["expires_at"]), tz=utils.TZ).replace(tzinfo=None)


def _strava_fetch_activities(access_token: str, per_page: int = 50) -> list[dict]:
    """Pull recent activities from the Strava API. Stubbed in tests."""
    response = httpx.get(
        "https://www.strava.com/api/v3/athlete/activities",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"per_page": per_page},
        timeout=20,
    )
    if response.status_code != 200:
        raise HealthError("Strava API request failed — try again later.")
    return response.json()


def _map_strava(raw: dict) -> dict | None:
    try:
        start = datetime.fromisoformat(str(raw["start_date"]).replace("Z", "+00:00"))
        start = start.astimezone(utils.TZ).replace(tzinfo=None)
    except (KeyError, ValueError):
        return None
    duration = int(raw.get("moving_time") or raw.get("elapsed_time") or 0)
    if duration <= 0:
        return None
    return {
        "name": str(raw.get("name") or "")[:200],
        "sport_type": str(raw.get("sport_type") or raw.get("type") or "Workout")[:50],
        "start_time": start,
        "duration_seconds": duration,
        "distance_m": float(raw["distance"]) if raw.get("distance") else None,
        "calories": float(raw["calories"]) if raw.get("calories") else None,
        "avg_hr": float(raw["average_heartrate"]) if raw.get("average_heartrate") else None,
        "max_hr": float(raw["max_heartrate"]) if raw.get("max_heartrate") else None,
        "elevation_gain_m": float(raw["total_elevation_gain"]) if raw.get("total_elevation_gain") else None,
        "notes": "",
    }
