from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app import config, utils
from app.models import Client, EmailLog, HealthConnection, OAuthAccount, Role, User
from app.services import accounts as accounts_svc
from app.services import oauth as oauth_svc
from app.services import scheduling, validation
from app.services.scheduling import BookingError

from .conftest import login, make_client, make_coach


@pytest.fixture()
def google_configured(monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CLIENT_ID", "test-google-id")
    monkeypatch.setattr(config, "GOOGLE_CLIENT_SECRET", "test-google-secret")
    monkeypatch.setattr(config, "STRAVA_CLIENT_ID", "test-strava-id")
    monkeypatch.setattr(config, "STRAVA_CLIENT_SECRET", "test-strava-secret")


def fake_oauth(monkeypatch, provider_id, email, name, provider="google"):
    monkeypatch.setattr(oauth_svc, "exchange_code", lambda p, c: {"access_token": "at-123",
                                                                  "refresh_token": "rt-456",
                                                                  "expires_in": 3600})
    monkeypatch.setattr(oauth_svc, "fetch_identity", lambda p, t: {
        "provider_id": provider_id, "email": email, "name": name,
        "access_token": "at-123", "refresh_token": "rt-456", "expires_at": None,
    })


def start_and_callback(client_http, provider, code="authcode"):
    start = client_http.get(f"/auth/{provider}/start", follow_redirects=False)
    assert start.status_code == 303
    state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
    return client_http.get(f"/auth/{provider}/callback?code={code}&state={state}",
                           follow_redirects=False)


def test_google_signup_creates_client_without_coach(client_http, db, monkeypatch, google_configured):
    fake_oauth(monkeypatch, "g-1", "newbie@gmail.com", "New Bee")
    response = start_and_callback(client_http, "google")
    assert response.headers["location"] == "/client"
    user = db.scalar(select(User).where(User.email == "newbie@gmail.com"))
    assert user.role == Role.CLIENT and user.password_hash == ""
    client = db.scalar(select(Client).where(Client.user_id == user.id))
    assert client.coach_id is None
    account = db.scalar(select(OAuthAccount).where(OAuthAccount.user_id == user.id))
    assert (account.provider, account.provider_account_id) == ("google", "g-1")
    # Second sign-in reuses the account instead of duplicating
    client_http.post("/logout")
    response = start_and_callback(client_http, "google")
    assert response.headers["location"] == "/client"
    assert db.scalar(select(User).where(User.email == "newbie@gmail.com")) is not None
    assert len(db.scalars(select(User).where(User.email == "newbie@gmail.com")).all()) == 1


def test_google_links_to_existing_email_account(client_http, db, monkeypatch, google_configured):
    coach = make_coach(db)
    make_client(db, coach, email="linked@test.local")
    fake_oauth(monkeypatch, "g-2", "linked@test.local", "Linked Person")
    response = start_and_callback(client_http, "google")
    assert response.headers["location"] == "/client"
    users = db.scalars(select(User).where(User.email == "linked@test.local")).all()
    assert len(users) == 1  # linked, not duplicated
    assert users[0].password_hash != ""  # original password kept


def test_strava_signup_creates_health_connection(client_http, db, monkeypatch, google_configured):
    fake_oauth(monkeypatch, "12345", "strava-12345@users.sanctuary.club", "Ath Lete",
               provider="strava")
    response = start_and_callback(client_http, "strava")
    assert response.headers["location"] == "/client"
    client = db.scalar(select(Client).join(User).where(User.email == "strava-12345@users.sanctuary.club"))
    connection = db.scalar(select(HealthConnection).where(HealthConnection.client_id == client.id))
    assert connection.provider == "strava"
    assert connection.access_token == "at-123"


def test_oauth_state_mismatch_rejected(client_http, db, monkeypatch, google_configured):
    fake_oauth(monkeypatch, "g-3", "evil@gmail.com", "Evil")
    client_http.get("/auth/google/start", follow_redirects=False)
    response = client_http.get("/auth/google/callback?code=x&state=WRONG", follow_redirects=False)
    assert response.headers["location"] == "/login"
    assert db.scalar(select(User).where(User.email == "evil@gmail.com")) is None


def test_unconfigured_provider_flashes_error(client_http):
    response = client_http.get("/auth/google/start", follow_redirects=False)
    assert response.headers["location"] == "/login"


def test_password_reset_flow(client_http, db):
    coach = make_coach(db)
    make_client(db, coach, email="forgetful@test.local")
    response = client_http.post("/forgot-password", data={"email": "forgetful@test.local"},
                                follow_redirects=False)
    assert response.headers["location"] == "/login"
    email = db.scalar(select(EmailLog).where(EmailLog.to_email == "forgetful@test.local"))
    assert "reset-password?token=" in email.body
    token = email.body.split("token=")[1].split()[0]

    # Form renders for a valid token
    assert client_http.get(f"/reset-password?token={token}").status_code == 200
    # Weak password rejected with a field error
    weak = client_http.post("/reset-password", data={"token": token, "password": "abc",
                                                     "confirm": "abc"})
    assert "at least 8 characters" in weak.text
    # Good password accepted
    response = client_http.post("/reset-password",
                                data={"token": token, "password": "newpass99",
                                      "confirm": "newpass99"}, follow_redirects=False)
    assert response.headers["location"] == "/login"
    login(client_http, "forgetful@test.local", "newpass99")
    # Token is single-use
    reused = client_http.get(f"/reset-password?token={token}", follow_redirects=False)
    assert reused.headers["location"] == "/forgot-password"


def test_reset_unknown_email_gives_same_response(client_http, db):
    response = client_http.post("/forgot-password", data={"email": "ghost@test.local"},
                                follow_redirects=False)
    assert response.headers["location"] == "/login"
    assert db.scalar(select(EmailLog)) is None


def test_expired_reset_token_rejected(db):
    coach = make_coach(db)
    client = make_client(db, coach)
    raw = accounts_svc.issue_reset_token(db, client.user)
    db.commit()
    token = db.scalar(select(__import__('app.models', fromlist=['PasswordResetToken']).PasswordResetToken))
    token.expires_at = utils.now() - timedelta(minutes=1)
    db.commit()
    assert accounts_svc.consume_reset_token(db, raw) is None


def test_profile_validation_rules():
    clean, errors = validation.validate_profile({
        "full_name": "A", "phone": "abc", "birth_date": "2030-01-01",
        "gender": "banana", "height_cm": "999", "goal": "x" * 501,
    })
    assert set(errors) == {"full_name", "phone", "birth_date", "gender", "height_cm", "goal"}
    clean, errors = validation.validate_profile({
        "full_name": "Valid Name", "phone": "+98 912 000 1111",
        "birth_date": "1995-05-01", "gender": "female", "height_cm": "170",
        "goal": "Get strong",
    })
    assert errors == {}
    assert clean["height_cm"] == 170 and clean["birth_date"] == date(1995, 5, 1)


def test_account_page_saves_profile_and_shows_errors(client_http, db):
    coach = make_coach(db)
    make_client(db, coach)
    login(client_http, "client@test.local", "client-secret")
    bad = client_http.post("/account", data={"full_name": "Client One", "phone": "nope"})
    assert 'class="field-error"' in bad.text
    good = client_http.post("/account", data={
        "full_name": "Client Renamed", "phone": "+98 912 000 1111",
        "birth_date": "1995-05-01", "gender": "male", "height_cm": "180",
        "goal": "Marathon"}, follow_redirects=False)
    assert good.status_code == 303
    db.expire_all()
    user = db.scalar(select(User).where(User.email == "client@test.local"))
    assert user.full_name == "Client Renamed"
    assert user.client_profile.height_cm == 180


def test_change_password_requires_current(client_http, db):
    coach = make_coach(db)
    make_client(db, coach)
    login(client_http, "client@test.local", "client-secret")
    wrong = client_http.post("/account/password", data={
        "current": "wrong", "password": "brandnew1", "confirm": "brandnew1"})
    assert "Current password is incorrect" in wrong.text
    ok = client_http.post("/account/password", data={
        "current": "client-secret", "password": "brandnew1", "confirm": "brandnew1"},
        follow_redirects=False)
    assert ok.status_code == 303
    client_http.post("/logout")
    login(client_http, "client@test.local", "brandnew1")


def test_coachless_client_cannot_book(db):
    coach = make_coach(db)
    client = make_client(db, coach)
    client.coach_id = None
    db.commit()
    from app.models import TimeSection
    section = db.scalar(select(TimeSection).where(TimeSection.index == 2))
    with pytest.raises(BookingError, match="don't have a coach"):
        scheduling.create_booking(db, client, date(2026, 7, 9), section, client.user)
