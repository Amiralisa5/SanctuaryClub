from sqlalchemy import select

from . import config
from .database import SessionLocal
from .models import Exercise, Role, Setting, TimeSection, User
from .security import hash_password


def seed_all() -> None:
    db = SessionLocal()
    try:
        _seed_sections(db)
        _seed_settings(db)
        _seed_admin(db)
        _seed_exercises(db)
        db.commit()
    finally:
        db.close()


def _seed_sections(db) -> None:
    if db.scalar(select(TimeSection).limit(1)):
        return
    hours_per_section = config.SECTION_HOURS
    index = 0
    for start in range(config.OPEN_HOUR, config.CLOSE_HOUR, hours_per_section):
        db.add(TimeSection(index=index, start_hour=start, end_hour=start + hours_per_section))
        index += 1


def _seed_settings(db) -> None:
    defaults = {
        "gym_default_capacity": str(config.GYM_DEFAULT_CAPACITY),
        "coach_default_capacity": str(config.COACH_DEFAULT_CAPACITY),
    }
    for key, value in defaults.items():
        if db.get(Setting, key) is None:
            db.add(Setting(key=key, value=value))


def _seed_admin(db) -> None:
    if db.scalar(select(User).where(User.role == Role.ADMIN).limit(1)):
        return
    db.add(User(
        email=config.ADMIN_EMAIL,
        password_hash=hash_password(config.ADMIN_PASSWORD),
        full_name=config.ADMIN_NAME,
        role=Role.ADMIN,
    ))


def _seed_exercises(db) -> None:
    if not config.SEED_EXERCISES or db.scalar(select(Exercise).limit(1)):
        return
    from .seed_exercises import load_exercise_library

    load_exercise_library(db)
