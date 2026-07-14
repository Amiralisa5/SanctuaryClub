from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import Activity, HealthConnection
from app.services.health import commands as health_c
from app.services.health import queries as health_q
from app.services.health.commands import HealthError

from .conftest import FROZEN_NOW, login, make_client, make_coach


def make_items(count=2):
    return [{"sport_type": "Run", "start_time": "2026-07-06T18:30",
             "duration_seconds": 1800, "distance_m": 5000, "external_id": f"run-{i}"}
            for i in range(count)]


def test_import_validates_and_dedupes(db):
    coach = make_coach(db)
    client = make_client(db, coach)
    items = make_items(2) + [
        {"sport_type": "", "start_time": "2026-07-06T18:30", "duration_seconds": 1800},
        {"sport_type": "Run", "start_time": "not-a-date", "duration_seconds": 1800},
        {"sport_type": "Run", "start_time": "2026-07-06T18:30", "duration_seconds": 10},
        {"sport_type": "Run", "start_time": "2099-01-01T10:00", "duration_seconds": 1800},
    ]
    result = health_c.handle(db, health_c.ImportActivities(
        client_id=client.id, provider="apple_health", items=items, actor_id=client.user_id))
    assert result["imported"] == 2
    assert len(result["skipped"]) == 4
    # Re-import of the same external ids is skipped
    again = health_c.handle(db, health_c.ImportActivities(
        client_id=client.id, provider="apple_health", items=make_items(2)))
    assert again["imported"] == 0 and len(again["skipped"]) == 2
    # Connection row recorded for the provider
    connection = db.scalar(select(HealthConnection).where(
        HealthConnection.client_id == client.id, HealthConnection.provider == "apple_health"))
    assert connection is not None and connection.last_sync_at is not None


def test_import_rejects_bad_provider_and_payload(db):
    coach = make_coach(db)
    client = make_client(db, coach)
    with pytest.raises(HealthError, match="Unknown import provider"):
        health_c.handle(db, health_c.ImportActivities(client_id=client.id,
                                                      provider="fitbit", items=make_items()))
    with pytest.raises(HealthError, match="non-empty"):
        health_c.handle(db, health_c.ImportActivities(client_id=client.id,
                                                      provider="manual", items=[]))


def test_manual_add_and_delete(db):
    coach = make_coach(db)
    client = make_client(db, coach)
    activity = health_c.handle(db, health_c.AddManualActivity(
        client_id=client.id, actor_id=client.user_id,
        fields={"sport_type": "WeightTraining", "start_time": "2026-07-07T07:00",
                "duration_seconds": 3600, "name": "Leg day", "avg_hr": 120}))
    assert activity.provider == "manual" and activity.duration_label == "1h 00m"
    health_c.handle(db, health_c.DeleteActivity(client_id=client.id,
                                                activity_id=activity.id))
    assert db.get(Activity, activity.id) is None
    # Deleting someone else's activity fails
    other = make_client(db, coach, email="other@test.local")
    activity = health_c.handle(db, health_c.AddManualActivity(
        client_id=client.id, fields={"sport_type": "Run", "start_time": "2026-07-07T08:00",
                                     "duration_seconds": 1200}))
    with pytest.raises(HealthError, match="not found"):
        health_c.handle(db, health_c.DeleteActivity(client_id=other.id,
                                                    activity_id=activity.id))


def test_strava_sync_maps_and_dedupes(db, monkeypatch):
    coach = make_coach(db)
    client = make_client(db, coach)
    db.add(HealthConnection(client_id=client.id, provider="strava",
                            access_token="tok", refresh_token="ref"))
    db.commit()
    payload = [
        {"id": 111, "name": "Evening Run", "sport_type": "Run",
         "start_date": "2026-07-05T14:00:00Z", "moving_time": 2400, "distance": 8012.5,
         "average_heartrate": 152.3, "max_heartrate": 171, "total_elevation_gain": 60},
        {"id": 112, "name": "Bad row", "start_date": "garbage", "moving_time": 100},
    ]
    monkeypatch.setattr(health_c, "_strava_fetch_activities", lambda token, per_page=50: payload)
    result = health_c.handle(db, health_c.SyncStrava(client_id=client.id))
    assert result == {"imported": 1, "fetched": 2}
    activity = db.scalar(select(Activity).where(Activity.provider == "strava"))
    assert activity.external_id == "111"
    assert activity.distance_km == 8.01
    assert activity.avg_hr == 152.3
    # Z-time converted to Tehran local (+03:30 in July)
    assert activity.start_time == datetime(2026, 7, 5, 17, 30)
    # Second sync imports nothing new
    result = health_c.handle(db, health_c.SyncStrava(client_id=client.id))
    assert result["imported"] == 0


def test_strava_sync_requires_connection(db):
    coach = make_coach(db)
    client = make_client(db, coach)
    with pytest.raises(HealthError, match="not connected"):
        health_c.handle(db, health_c.SyncStrava(client_id=client.id))


def test_strava_refresh_persists_even_if_fetch_then_fails(db, monkeypatch):
    """Strava rotates the refresh_token on every use; if the new tokens aren't
    committed immediately, a later failure (e.g. the activities fetch) rolls
    back the refresh and leaves the connection stuck with an already-invalid
    refresh_token."""
    coach = make_coach(db)
    client = make_client(db, coach)
    connection = HealthConnection(client_id=client.id, provider="strava",
                                  access_token="stale", refresh_token="old-refresh",
                                  expires_at=FROZEN_NOW - timedelta(minutes=1))
    db.add(connection)
    db.commit()

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"access_token": "new-token", "refresh_token": "new-refresh"}

    monkeypatch.setattr(health_c.httpx, "post", lambda *a, **k: FakeResponse())

    def failing_fetch(token, per_page=50):
        raise HealthError("Strava API request failed — try again later.")

    monkeypatch.setattr(health_c, "_strava_fetch_activities", failing_fetch)

    with pytest.raises(HealthError, match="try again later"):
        health_c.handle(db, health_c.SyncStrava(client_id=client.id))

    db.expire_all()
    refreshed = db.scalar(select(HealthConnection).where(HealthConnection.client_id == client.id))
    assert refreshed.access_token == "new-token"
    assert refreshed.refresh_token == "new-refresh"


def test_stats_and_weekly_volume(db):
    coach = make_coach(db)
    client = make_client(db, coach)
    # Frozen now = Wed 2026-07-08; gym week starts Saturday 2026-07-04
    items = [
        {"sport_type": "Run", "start_time": "2026-07-05T10:00", "duration_seconds": 1800,
         "distance_m": 5000, "external_id": "a"},                       # this week
        {"sport_type": "Ride", "start_time": "2026-07-07T10:00", "duration_seconds": 3600,
         "distance_m": 20000, "external_id": "b"},                      # this week
        {"sport_type": "Run", "start_time": "2026-06-20T10:00", "duration_seconds": 3000,
         "distance_m": 8000, "external_id": "c"},                       # older week
    ]
    health_c.handle(db, health_c.ImportActivities(client_id=client.id,
                                                  provider="manual", items=items))
    stats = health_q.ask(db, health_q.ActivityStats(client_id=client.id))
    assert stats["total"] == 3
    assert stats["week_count"] == 2
    assert stats["week_minutes"] == 90
    assert stats["week_km"] == 25.0
    weeks = health_q.ask(db, health_q.WeeklyVolume(client_id=client.id, weeks=4))
    assert len(weeks) == 4
    assert weeks[-1]["minutes"] == 90  # current week last
    assert sum(w["minutes"] for w in weeks) == 140


def test_activity_pages_and_privacy(client_http, db):
    coach_a = make_coach(db, email="ca@test.local")
    coach_b = make_coach(db, email="cb@test.local")
    client = make_client(db, coach_a, email="client@test.local")
    health_c.handle(db, health_c.AddManualActivity(
        client_id=client.id, fields={"sport_type": "Run", "start_time": "2026-07-07T08:00",
                                     "duration_seconds": 1500, "name": "Tempo run"}))
    # Client sees their log
    login(client_http, "client@test.local", "client-secret")
    page = client_http.get("/client/activities")
    assert page.status_code == 200 and "Tempo run" in page.text
    # Their coach can view read-only
    login(client_http, "ca@test.local", "coach-secret")
    page = client_http.get(f"/coach/clients/{client.id}/activities")
    assert page.status_code == 200 and "Tempo run" in page.text
    assert "Log an activity" not in page.text  # read-only: no edit forms
    # Another coach is locked out
    login(client_http, "cb@test.local", "coach-secret")
    assert client_http.get(f"/coach/clients/{client.id}/activities",
                           follow_redirects=False).status_code == 403


def test_manual_add_via_http(client_http, db):
    coach = make_coach(db)
    make_client(db, coach)
    login(client_http, "client@test.local", "client-secret")
    response = client_http.post("/client/activities", data={
        "sport_type": "Ride", "start_time": "2026-07-06T07:15",
        "duration_minutes": "45", "distance_km": "18.5", "name": "Commute"},
        follow_redirects=False)
    assert response.status_code == 303
    activity = db.scalar(select(Activity))
    assert activity.duration_seconds == 2700
    assert activity.distance_m == 18500.0
