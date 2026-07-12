"""Load the bundled exercise library into the database.

The dataset (app/data/exercise_library.json, 873 exercises) is derived from the
public-domain (CC0) Free Exercise DB — https://github.com/yuhonas/free-exercise-db —
with names, form instructions, muscle/equipment/level tags, and demo images.

Usage: python -m app.seed_exercises   (idempotent: existing names are kept as-is)
"""
import json
from pathlib import Path

from sqlalchemy import select

from .database import Base, SessionLocal, engine
from .models import Exercise

DATA_FILE = Path(__file__).parent / "data" / "exercise_library.json"


def load_exercise_library(db) -> dict:
    items = json.loads(DATA_FILE.read_text())
    existing = {name.lower() for name in db.scalars(select(Exercise.name))}
    added = 0
    for item in items:
        if item["name"].lower() in existing:
            continue
        db.add(Exercise(name=item["name"], description=item["description"],
                        tags=item["tags"], video_url=item["video_url"]))
        added += 1
    db.commit()
    return {"added": added, "skipped": len(items) - added, "total": len(items)}


def main() -> None:
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        result = load_exercise_library(db)
        print(f"Exercise library: {result['added']} added, "
              f"{result['skipped']} already present, {result['total']} in dataset.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
