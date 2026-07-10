from datetime import date

from sqlalchemy import select

from app.models import HealthConnection, Notification, TimeSection, User
from app.services import accounts as accounts_svc
from app.services import scheduling
from app.services.health import commands as health_c

from .conftest import login, make_client, make_coach


def notifications_for(db, email):
    user = db.scalar(select(User).where(User.email == email))
    return db.scalars(select(Notification).where(Notification.user_id == user.id)
                      .order_by(Notification.id)).all()


def get_section(db, index=2):
    return db.scalar(select(TimeSection).where(TimeSection.index == index))


def test_booking_notifies_the_counterpart(db):
    coach = make_coach(db)
    client = make_client(db, coach)
    # Client books -> coach gets the in-app alert, client does not
    booking = scheduling.create_booking(db, client, date(2026, 7, 9), get_section(db), client.user)
    assert len(notifications_for(db, "coach@test.local")) == 1
    assert len(notifications_for(db, "client@test.local")) == 0
    # Coach cancels -> client gets the alert
    scheduling.cancel_booking(db, booking, coach.user)
    client_notes = notifications_for(db, "client@test.local")
    assert len(client_notes) == 1
    assert client_notes[0].title == "Session cancelled"
    assert len(notifications_for(db, "coach@test.local")) == 1  # unchanged


def test_oauth_signup_notifies_admins(db):
    identity = {"provider_id": "g-9", "email": "fresh@gmail.com", "name": "Fresh Face",
                "access_token": "", "refresh_token": "", "expires_at": None}
    accounts_svc.resolve_oauth_user(db, "google", identity)
    admin_notes = notifications_for(db, "admin@test.local")
    assert len(admin_notes) == 1
    assert "needs a coach" in admin_notes[0].body
    assert admin_notes[0].link == "/admin/users"


def test_coach_assignment_notifies_both_sides(client_http, db):
    coach = make_coach(db)
    client = make_client(db, coach)
    client.coach_id = None
    db.commit()
    login(client_http, "admin@test.local", "admin-secret")
    response = client_http.post(f"/admin/clients/{client.id}/reassign",
                                data={"coach_id": coach.id}, follow_redirects=False)
    assert response.status_code == 303
    assert notifications_for(db, "client@test.local")[0].title == "Coach assigned"
    assert notifications_for(db, "coach@test.local")[0].title == "New client assigned"


def test_notifications_page_shows_badge_and_marks_read(client_http, db):
    coach = make_coach(db)
    client = make_client(db, coach)
    scheduling.create_booking(db, client, date(2026, 7, 9), get_section(db), client.user)
    login(client_http, "coach@test.local", "coach-secret")
    # Unread badge visible in the shell
    page = client_http.get("/coach")
    assert 'notif-count' in page.text
    # Opening the page shows the item and marks it read
    page = client_http.get("/notifications")
    assert "Session booked" in page.text
    db.expire_all()
    assert all(n.read for n in notifications_for(db, "coach@test.local"))
    page = client_http.get("/coach")
    assert 'notif-count' not in page.text


def test_sync_all_strava_handles_mixed_connections(db, monkeypatch):
    coach = make_coach(db)
    good = make_client(db, coach, email="good@test.local")
    bad = make_client(db, coach, email="bad@test.local")
    db.add(HealthConnection(client_id=good.id, provider="strava", access_token="ok"))
    db.add(HealthConnection(client_id=bad.id, provider="strava", access_token="broken"))
    db.commit()

    def fake_fetch(token, per_page=50):
        if token == "broken":
            raise health_c.HealthError("Strava API request failed")
        return [{"id": 900, "name": "Night run", "sport_type": "Run",
                 "start_date": "2026-07-05T14:00:00Z", "moving_time": 1800}]

    monkeypatch.setattr(health_c, "_strava_fetch_activities", fake_fetch)
    result = health_c.sync_all_strava(db)
    assert result == {"synced": 1, "imported": 1, "failed": 1}
