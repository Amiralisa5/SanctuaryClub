import os

from sqlalchemy import select

from app import config
from app.models import Exercise

from .conftest import login, make_client, make_coach


def coach_session(client_http, db):
    coach = make_coach(db)
    make_client(db, coach)
    login(client_http, "coach@test.local", "coach-secret")
    return coach


def test_create_exercise_with_video_url(client_http, db):
    coach_session(client_http, db)
    response = client_http.post(
        "/coach/exercises",
        data={"name": "Goblet Squat", "tags": "legs", "video_url": "https://youtu.be/demo123"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    exercise = db.scalar(select(Exercise).where(Exercise.name == "Goblet Squat"))
    assert exercise.video_url == "https://youtu.be/demo123"
    assert exercise.demo_url == "https://youtu.be/demo123"


def test_upload_demo_file_and_serve_it(client_http, db):
    coach_session(client_http, db)
    response = client_http.post(
        "/coach/exercises",
        data={"name": "Deadlift"},
        files={"media": ("demo.mp4", b"fake-mp4-bytes", "video/mp4")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    exercise = db.scalar(select(Exercise).where(Exercise.name == "Deadlift"))
    assert exercise.media_path.endswith(".mp4")
    assert os.path.exists(os.path.join(config.UPLOAD_DIR, exercise.media_path))
    # Uploaded file is served from /media
    served = client_http.get(exercise.demo_url)
    assert served.status_code == 200
    assert served.content == b"fake-mp4-bytes"


def test_disallowed_extension_rejected(client_http, db):
    coach_session(client_http, db)
    response = client_http.post(
        "/coach/exercises",
        data={"name": "Sketchy"},
        files={"media": ("payload.exe", b"MZ...", "application/octet-stream")},
        follow_redirects=False,
    )
    assert response.status_code == 303  # redirect with error flash, nothing stored
    assert db.scalar(select(Exercise).where(Exercise.name == "Sketchy")) is None


def test_update_media_on_existing_exercise(client_http, db):
    coach_session(client_http, db)
    client_http.post("/coach/exercises", data={"name": "Row"}, follow_redirects=False)
    exercise = db.scalar(select(Exercise).where(Exercise.name == "Row"))
    response = client_http.post(
        f"/coach/exercises/{exercise.id}/media",
        data={"video_url": "https://vimeo.com/row-demo"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db.expire_all()
    assert db.get(Exercise, exercise.id).video_url == "https://vimeo.com/row-demo"
