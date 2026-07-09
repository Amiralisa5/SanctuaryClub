import calendar
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import config

TZ = ZoneInfo(config.TIMEZONE)


def now() -> datetime:
    """Current wall-clock time in the gym's timezone (naive local datetime).

    All dates/times in the system are stored as naive Tehran local time.
    Tests monkeypatch this function to control the clock.
    """
    return datetime.now(TZ).replace(tzinfo=None)


def month_dates(year: int, month: int) -> list[date]:
    days = calendar.monthrange(year, month)[1]
    return [date(year, month, d) for d in range(1, days + 1)]


# Week starts on Saturday (Iranian week)
WEEK_START = calendar.SATURDAY
WEEKDAY_NAMES = ["Sat", "Sun", "Mon", "Tue", "Wed", "Thu", "Fri"]


def month_grid(year: int, month: int) -> list[list[date]]:
    """Calendar weeks for the month (each a list of 7 dates, Saturday-first),
    padded with the neighbouring months' dates."""
    return calendar.Calendar(firstweekday=WEEK_START).monthdatescalendar(year, month)


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def flash(request, message: str, category: str = "info") -> None:
    request.session.setdefault("_flashes", []).append(
        {"message": message, "category": category}
    )


def pop_flashes(request) -> list[dict]:
    return request.session.pop("_flashes", [])
