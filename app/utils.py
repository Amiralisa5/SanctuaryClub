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


def flash(request, message: str, category: str = "info") -> None:
    request.session.setdefault("_flashes", []).append(
        {"message": message, "category": category}
    )


def pop_flashes(request) -> list[dict]:
    return request.session.pop("_flashes", [])
