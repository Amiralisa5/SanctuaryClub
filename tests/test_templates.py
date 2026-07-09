from datetime import date

from sqlalchemy import select

from app.models import Exercise, ProgramWeek, WorkoutDay, WorkoutItem
from app.services import programs as programs_svc

from .conftest import login, make_client, make_coach


def make_program(db, client, week_start=date(2026, 7, 4)):
    week = ProgramWeek(client_id=client.id, coach_id=client.coach_id,
                       week_start=week_start, title="Strength Block W1", notes="Deload every 4th week")
    db.add(week)
    db.flush()
    squat = Exercise(name="Back Squat")
    db.add(squat)
    db.flush()
    for day_index in range(7):
        day = WorkoutDay(program_week_id=week.id, day_index=day_index)
        db.add(day)
        db.flush()
        if day_index == 2:
            day.title = "Lower body"
            db.add(WorkoutItem(workout_day_id=day.id, exercise_id=squat.id,
                               position=0, sets=5, reps="5", target_weight="80 kg",
                               rest_seconds=180, notes="belt on"))
    db.commit()
    return week


def test_save_and_apply_template_round_trip(db):
    coach = make_coach(db)
    source_client = make_client(db, coach, email="src@test.local")
    target_client = make_client(db, coach, email="dst@test.local")
    week = make_program(db, source_client)

    template = programs_svc.save_week_as_template(db, week, "Standard strength week", coach.user)
    assert template.coach_id == coach.id
    assert template.exercise_count == 1

    new_week = programs_svc.apply_template(db, template, target_client, date(2026, 8, 1), coach.user)
    assert new_week.client_id == target_client.id
    assert new_week.title == "Standard strength week"
    assert len(new_week.days) == 7
    monday = next(d for d in new_week.days if d.day_index == 2)
    assert monday.title == "Lower body"
    assert len(monday.items) == 1
    item = monday.items[0]
    assert (item.sets, item.reps, item.target_weight, item.rest_seconds) == (5, "5", "80 kg", 180)
    # The copy is independent of the source week
    assert monday.items[0].id != week.days[2].items[0].id


def test_template_routes_and_isolation(client_http, db):
    coach_a = make_coach(db, email="ca@test.local", name="Coach A")
    coach_b = make_coach(db, email="cb@test.local", name="Coach B")
    client_a = make_client(db, coach_a, email="clienta@test.local")
    week = make_program(db, client_a)
    template = programs_svc.save_week_as_template(db, week, "A's template", coach_a.user)

    # Coach A sees the template
    login(client_http, "ca@test.local", "coach-secret")
    page = client_http.get("/coach/templates")
    assert page.status_code == 200 and "A&#39;s template" in page.text

    # Coach A applies it via HTTP
    response = client_http.post(
        f"/coach/clients/{client_a.id}/programs/from-template",
        data={"template_id": template.id, "week_start": "2026-08-08"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db.expire_all()
    weeks = db.scalars(select(ProgramWeek).where(ProgramWeek.client_id == client_a.id)).all()
    assert len(weeks) == 2

    # Coach B cannot see or use coach A's template
    login(client_http, "cb@test.local", "coach-secret")
    page = client_http.get("/coach/templates")
    assert "A&#39;s template" not in page.text
    client_b = make_client(db, coach_b, email="clientb@test.local")
    response = client_http.post(
        f"/coach/clients/{client_b.id}/programs/from-template",
        data={"template_id": template.id, "week_start": "2026-08-08"},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_delete_template(client_http, db):
    coach = make_coach(db)
    client = make_client(db, coach)
    week = make_program(db, client)
    template = programs_svc.save_week_as_template(db, week, "Doomed", coach.user)
    login(client_http, "coach@test.local", "coach-secret")
    response = client_http.post(f"/coach/templates/{template.id}/delete", follow_redirects=False)
    assert response.status_code == 303
    db.expire_all()
    page = client_http.get("/coach/templates")
    assert "Doomed" not in page.text
