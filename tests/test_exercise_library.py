from sqlalchemy import func, select

from app.models import Exercise
from app.seed_exercises import load_exercise_library

from .conftest import login, make_client, make_coach


def test_seed_loads_full_library_and_is_idempotent(db):
    result = load_exercise_library(db)
    assert result["added"] == result["total"] >= 800
    count = db.scalar(select(func.count(Exercise.id)))
    assert count == result["total"]
    # Second run adds nothing and duplicates nothing
    again = load_exercise_library(db)
    assert again["added"] == 0 and again["skipped"] == result["total"]
    assert db.scalar(select(func.count(Exercise.id))) == count


def test_seed_respects_existing_custom_exercises(db):
    db.add(Exercise(name="Barbell Squat", description="my custom cue", tags="legs"))
    db.commit()
    load_exercise_library(db)
    kept = db.scalar(select(Exercise).where(Exercise.name == "Barbell Squat"))
    assert kept.description == "my custom cue"  # never overwritten


def test_seeded_records_have_expected_fields(db):
    load_exercise_library(db)
    squat = db.scalar(select(Exercise).where(Exercise.name.ilike("%deadlift%")).limit(1))
    assert squat is not None
    assert squat.tags and squat.description
    assert squat.video_url.startswith("https://yuhonas.github.io/free-exercise-db/")
    assert len(squat.description) <= 400
    assert len(squat.tags) <= 255


def test_library_search_and_tag_filter(client_http, db):
    load_exercise_library(db)
    coach = make_coach(db)
    make_client(db, coach)
    login(client_http, "coach@test.local", "coach-secret")

    page = client_http.get("/coach/exercises?q=deadlift")
    assert page.status_code == 200
    assert "Deadlift" in page.text
    assert "Bench Press" not in page.text

    page = client_http.get("/coach/exercises?tag=kettlebells")
    assert page.status_code == 200 and "Kettlebell" in page.text

    # Cap message appears when unfiltered (873 > 100 shown)
    page = client_http.get("/coach/exercises")
    assert "showing first 100" in page.text
