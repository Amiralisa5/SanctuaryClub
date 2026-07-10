"""Per-field validation rules for account/profile forms.

Each validator returns (clean_values, errors) where errors maps field name to a
human-readable message; templates render them inline under the field.
"""
import re
from datetime import date, datetime

from .. import utils

PHONE_RE = re.compile(r"^\+?[0-9][0-9 \-]{6,14}$")
GENDERS = {"", "male", "female", "other"}


def validate_profile(form: dict) -> tuple[dict, dict]:
    clean: dict = {}
    errors: dict = {}

    full_name = (form.get("full_name") or "").strip()
    if not 2 <= len(full_name) <= 120:
        errors["full_name"] = "Full name must be between 2 and 120 characters."
    else:
        clean["full_name"] = full_name

    phone = (form.get("phone") or "").strip()
    if phone and not PHONE_RE.match(phone):
        errors["phone"] = "Phone must be 7–15 digits, optionally starting with +."
    else:
        clean["phone"] = phone

    birth_date_raw = (form.get("birth_date") or "").strip()
    if birth_date_raw:
        try:
            birth_date = date.fromisoformat(birth_date_raw)
        except ValueError:
            errors["birth_date"] = "Birth date must be a valid date (YYYY-MM-DD)."
        else:
            age = (utils.now().date() - birth_date).days / 365.25
            if not 10 <= age <= 100:
                errors["birth_date"] = "Age must be between 10 and 100 years."
            else:
                clean["birth_date"] = birth_date
    else:
        clean["birth_date"] = None

    gender = (form.get("gender") or "").strip().lower()
    if gender not in GENDERS:
        errors["gender"] = "Choose one of the listed options."
    else:
        clean["gender"] = gender

    height_raw = (form.get("height_cm") or "").strip()
    if height_raw:
        try:
            height = int(height_raw)
        except ValueError:
            errors["height_cm"] = "Height must be a whole number of centimetres."
        else:
            if not 100 <= height <= 250:
                errors["height_cm"] = "Height must be between 100 and 250 cm."
            else:
                clean["height_cm"] = height
    else:
        clean["height_cm"] = None

    goal = (form.get("goal") or "").strip()
    if len(goal) > 500:
        errors["goal"] = "Goal must be at most 500 characters."
    else:
        clean["goal"] = goal

    return clean, errors


def validate_password(password: str, confirm: str) -> dict:
    errors: dict = {}
    if len(password) < 8:
        errors["password"] = "Password must be at least 8 characters."
    elif password.isdigit() or password.isalpha():
        errors["password"] = "Use a mix of letters and numbers."
    if confirm != password:
        errors["confirm"] = "Passwords do not match."
    return errors


def validate_activity_item(item: dict) -> tuple[dict, str | None]:
    """Validate one imported/manual activity. Returns (clean, error_message)."""
    sport = str(item.get("sport_type") or "").strip()
    if not 2 <= len(sport) <= 50:
        return {}, "sport_type is required (2–50 characters)"

    start_raw = str(item.get("start_time") or "").strip()
    try:
        start_time = datetime.fromisoformat(start_raw)
        if start_time.tzinfo is not None:
            start_time = start_time.astimezone(utils.TZ).replace(tzinfo=None)
    except ValueError:
        return {}, "start_time must be an ISO datetime (e.g. 2026-07-01T18:30)"
    if start_time > utils.now():
        return {}, "start_time cannot be in the future"

    try:
        duration = int(item.get("duration_seconds") or 0)
    except (TypeError, ValueError):
        return {}, "duration_seconds must be a number"
    if not 60 <= duration <= 24 * 3600:
        return {}, "duration_seconds must be between 60 and 86400"

    clean = {
        "sport_type": sport,
        "start_time": start_time,
        "duration_seconds": duration,
        "name": str(item.get("name") or "").strip()[:200],
        "notes": str(item.get("notes") or "").strip()[:2000],
    }
    for field, low, high in (("distance_m", 0, 1_000_000), ("calories", 0, 20_000),
                             ("avg_hr", 20, 250), ("max_hr", 20, 250),
                             ("elevation_gain_m", 0, 20_000)):
        raw = item.get(field)
        if raw in (None, ""):
            clean[field] = None
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return {}, f"{field} must be a number"
        if not low <= value <= high:
            return {}, f"{field} must be between {low} and {high}"
        clean[field] = value
    return clean, None
