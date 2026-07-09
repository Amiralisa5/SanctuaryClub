from datetime import date

from sqlalchemy import select

from app.models import Attendance, AttendanceStatus, Booking, TimeSection
from app.services import metrics

from .conftest import login, make_client, make_coach


def add_checkin(db, client, d, weight=None, rpe=None, completion=None):
    section = db.scalar(select(TimeSection).where(TimeSection.index == 2))
    booking = Booking(client_id=client.id, coach_id=client.coach_id, date=d,
                      section_id=section.id)
    db.add(booking)
    db.flush()
    db.add(Attendance(booking_id=booking.id, status=AttendanceStatus.PRESENT, auto=False,
                      weight_kg=weight, rpe=rpe, completion_pct=completion))
    db.commit()
    return booking


def test_series_and_tiles(db):
    coach = make_coach(db)
    client = make_client(db, coach)
    add_checkin(db, client, date(2026, 7, 1), weight=82.0, rpe=8, completion=90)
    add_checkin(db, client, date(2026, 7, 3), weight=81.2, rpe=7, completion=100)
    add_checkin(db, client, date(2026, 7, 6), weight=80.4, rpe=6, completion=95)

    context = metrics.progress_context(db, client)
    tiles = context["tiles"]
    assert tiles["weight"] == 80.4
    assert tiles["weight_delta"] == -1.6
    assert tiles["avg_rpe"] == 7.0
    assert tiles["avg_completion"] == 95
    assert tiles["rate"] == 100  # all marked sessions were PRESENT


def test_line_chart_geometry(db):
    series = [(date(2026, 7, 1), 82.0), (date(2026, 7, 3), 81.0), (date(2026, 7, 6), 80.0)]
    chart = metrics.line_chart(series)
    assert chart is not None
    # min value maps to the baseline, max to the top gridline
    ys = [p["y"] for p in chart["dots"]]
    assert ys[0] == chart["top_y"]           # 82 = max -> top
    assert ys[-1] == chart["baseline_y"]     # 80 = min -> bottom
    assert chart["last"]["value"] == 80.0
    # single point -> no chart
    assert metrics.line_chart(series[:1]) is None


def test_progress_pages_and_isolation(client_http, db):
    coach_a = make_coach(db, email="ca@test.local")
    coach_b = make_coach(db, email="cb@test.local")
    client = make_client(db, coach_a, email="client@test.local")
    add_checkin(db, client, date(2026, 7, 1), weight=82.0, rpe=8, completion=90)
    add_checkin(db, client, date(2026, 7, 3), weight=81.0, rpe=7, completion=100)

    login(client_http, "client@test.local", "client-secret")
    page = client_http.get("/client/progress")
    assert page.status_code == 200
    assert "81.0" in page.text  # current weight tile
    assert "chart-line" in page.text

    login(client_http, "ca@test.local", "coach-secret")
    assert client_http.get(f"/coach/clients/{client.id}/progress").status_code == 200

    login(client_http, "cb@test.local", "coach-secret")
    assert client_http.get(f"/coach/clients/{client.id}/progress",
                           follow_redirects=False).status_code == 403
