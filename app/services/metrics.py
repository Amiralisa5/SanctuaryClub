"""Progress metrics: check-in series and server-computed SVG chart geometry."""
from datetime import date

from sqlalchemy import select

from .. import utils
from ..models import Attendance, Booking, Client
from ..utils import shift_month
from . import attendance as attendance_svc


def checkin_history(db, client_id: int, limit: int = 60) -> list[Attendance]:
    """PRESENT check-ins with self-reported data, oldest first."""
    rows = db.scalars(
        select(Attendance)
        .join(Booking)
        .where(Booking.client_id == client_id, Attendance.auto.is_(False))
        .order_by(Booking.date.desc())
        .limit(limit)
    ).all()
    return list(reversed(rows))


def series_from(history: list[Attendance], field: str) -> list[tuple[date, float]]:
    return [(a.booking.date, getattr(a, field)) for a in history
            if getattr(a, field) is not None]


def attendance_history(db, client_id: int, months: int = 6) -> list[dict]:
    """Attendance rate per month for the last `months` months, oldest first."""
    current = utils.now()
    out = []
    for delta in range(-(months - 1), 1):
        year, month = shift_month(current.year, current.month, delta)
        summary = attendance_svc.monthly_summary(db, client_id, year, month)
        marked = summary["present"] + summary["absent"] + summary["excused"]
        rate = round(summary["present"] / marked * 100) if marked else None
        out.append({
            "label": date(year, month, 1).strftime("%b"),
            "year": year, "month": month, "rate": rate, **summary,
        })
    return out


def line_chart(series: list[tuple[date, float]], width: int = 600, height: int = 190) -> dict | None:
    """Geometry for a single-series SVG line chart; None when under 2 points."""
    if len(series) < 2:
        return None
    values = [v for _, v in series]
    lo, hi = min(values), max(values)
    if hi == lo:  # flat series still needs a vertical range
        lo, hi = lo - 1, hi + 1
    pad_l, pad_r, pad_t, pad_b = 44, 18, 16, 26
    inner_w, inner_h = width - pad_l - pad_r, height - pad_t - pad_b
    dots = []
    for i, (d, v) in enumerate(series):
        x = pad_l + inner_w * i / (len(series) - 1)
        y = pad_t + inner_h * (1 - (v - lo) / (hi - lo))
        dots.append({"x": round(x, 1), "y": round(y, 1), "date": d, "value": v})
    points = " ".join(f"{p['x']},{p['y']}" for p in dots)
    baseline = pad_t + inner_h
    return {
        "width": width, "height": height,
        "points": points,
        "area": f"{pad_l},{baseline} {points} {dots[-1]['x']},{baseline}",
        "dots": dots, "last": dots[-1],
        "y_min": lo, "y_max": hi,
        "pad_l": pad_l, "top_y": pad_t, "baseline_y": baseline,
        "x_first": series[0][0], "x_last": series[-1][0],
    }


def _rounded_top_path(x: float, y: float, w: float, h: float, r: float = 4) -> str:
    """Bar with a 4px rounded data-end and a square baseline."""
    if h <= r:
        return f"M{x},{y + h} L{x},{y} L{x + w},{y} L{x + w},{y + h} Z"
    return (f"M{x},{y + h} L{x},{y + r} Q{x},{y} {x + r},{y} "
            f"L{x + w - r},{y} Q{x + w},{y} {x + w},{y + r} L{x + w},{y + h} Z")


def bar_chart(months: list[dict], width: int = 600, height: int = 170) -> dict:
    """Geometry for the monthly attendance-rate column chart."""
    pad_l, pad_r, pad_t, pad_b = 16, 16, 26, 24
    inner_w, inner_h = width - pad_l - pad_r, height - pad_t - pad_b
    slot = inner_w / len(months)
    bar_w = min(24.0, slot * 0.5)
    baseline = pad_t + inner_h
    bars = []
    for i, m in enumerate(months):
        h = 0.0 if m["rate"] is None else inner_h * m["rate"] / 100
        x = round(pad_l + slot * i + (slot - bar_w) / 2, 1)
        y = round(baseline - h, 1)
        bars.append({**m, "x": x, "y": y, "w": bar_w, "h": round(h, 1),
                     "cx": round(x + bar_w / 2, 1),
                     "path": _rounded_top_path(x, y, bar_w, h) if h else ""})
    return {"width": width, "height": height, "bars": bars, "baseline_y": baseline}


def progress_context(db, client: Client) -> dict:
    history = checkin_history(db, client.id)
    weight_series = series_from(history, "weight_kg")
    rpe_series = series_from(history, "rpe")
    completion_values = [v for _, v in series_from(history, "completion_pct")]
    months = attendance_history(db, client.id)
    current_month = months[-1] if months else None
    return {
        "history": history,
        "weight_chart": line_chart(weight_series),
        "rpe_chart": line_chart(rpe_series),
        "months_chart": bar_chart(months),
        "tiles": {
            "weight": weight_series[-1][1] if weight_series else None,
            "weight_delta": round(weight_series[-1][1] - weight_series[0][1], 1)
            if len(weight_series) >= 2 else None,
            "avg_rpe": round(sum(v for _, v in rpe_series) / len(rpe_series), 1)
            if rpe_series else None,
            "avg_completion": round(sum(completion_values) / len(completion_values))
            if completion_values else None,
            "rate": current_month["rate"] if current_month else None,
        },
    }
