import os
import tempfile
from datetime import datetime

import pytest

# Configure the environment BEFORE importing the app so the SQLite test DB is used
# and the background scheduler stays off.
_tmpdir = tempfile.mkdtemp(prefix="sanctuary-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmpdir}/test.db"
os.environ["UPLOAD_DIR"] = f"{_tmpdir}/media"
os.environ["DISABLE_SCHEDULER"] = "1"
os.environ["SEED_EXERCISES"] = "0"
os.environ["ADMIN_EMAIL"] = "admin@test.local"
os.environ["ADMIN_PASSWORD"] = "admin-secret"

from fastapi.testclient import TestClient  # noqa: E402

from app import utils  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Client, Coach, PlanMonth, Role, User  # noqa: E402
from app.security import hash_password  # noqa: E402
from app.seed import seed_all  # noqa: E402

# A fixed "now" so booking-window rules are deterministic: Wednesday 2026-07-08 09:00 Tehran.
FROZEN_NOW = datetime(2026, 7, 8, 9, 0, 0)


@pytest.fixture(autouse=True)
def frozen_time(monkeypatch):
    monkeypatch.setattr(utils, "now", lambda: FROZEN_NOW)
    yield


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    seed_all()
    yield


@pytest.fixture()
def client_http():
    with TestClient(app) as http:
        yield http


def make_coach(db, email="coach@test.local", password="coach-secret", name="Coach One"):
    user = User(email=email, password_hash=hash_password(password), full_name=name, role=Role.COACH)
    db.add(user)
    db.flush()
    coach = Coach(user_id=user.id)
    db.add(coach)
    db.commit()
    return coach


def make_client(db, coach, email="client@test.local", password="client-secret",
                name="Client One", quota=12, plan_year=2026, plan_month=7):
    user = User(email=email, password_hash=hash_password(password), full_name=name, role=Role.CLIENT)
    db.add(user)
    db.flush()
    client = Client(user_id=user.id, coach_id=coach.id)
    db.add(client)
    db.flush()
    if quota != "none":
        db.add(PlanMonth(client_id=client.id, year=plan_year, month=plan_month, quota=quota))
    db.commit()
    return client


def login(http, email, password):
    response = http.post("/login", data={"email": email, "password": password}, follow_redirects=False)
    assert response.status_code == 303
    return response
