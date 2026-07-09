from datetime import date

from ..audit import log_action
from ..models import (
    Client,
    ProgramTemplate,
    ProgramWeek,
    TemplateDay,
    TemplateItem,
    User,
    WorkoutDay,
    WorkoutItem,
)


def save_week_as_template(db, week: ProgramWeek, title: str, actor: User) -> ProgramTemplate:
    """Deep-copy a program week into a reusable template owned by its coach."""
    template = ProgramTemplate(coach_id=week.coach_id, title=title.strip() or week.title,
                               notes=week.notes)
    db.add(template)
    db.flush()
    for day in week.days:
        template_day = TemplateDay(template_id=template.id, day_index=day.day_index,
                                   title=day.title, notes=day.notes)
        db.add(template_day)
        db.flush()
        for item in day.items:
            db.add(TemplateItem(
                template_day_id=template_day.id, exercise_id=item.exercise_id,
                position=item.position, sets=item.sets, reps=item.reps,
                target_weight=item.target_weight, rest_seconds=item.rest_seconds,
                notes=item.notes,
            ))
    log_action(db, actor, "template.create", "program_template", template.id,
               f"from week={week.id} title={template.title}")
    db.commit()
    return template


def apply_template(db, template: ProgramTemplate, client: Client, week_start: date,
                   actor: User) -> ProgramWeek:
    """Instantiate a template as a new program week for a client."""
    week = ProgramWeek(client_id=client.id, coach_id=template.coach_id,
                       week_start=week_start, title=template.title, notes=template.notes)
    db.add(week)
    db.flush()
    days_by_index = {day.day_index: day for day in template.days}
    for day_index in range(7):
        template_day = days_by_index.get(day_index)
        week_day = WorkoutDay(program_week_id=week.id, day_index=day_index,
                              title=template_day.title if template_day else "",
                              notes=template_day.notes if template_day else "")
        db.add(week_day)
        db.flush()
        if template_day:
            for item in template_day.items:
                db.add(WorkoutItem(
                    workout_day_id=week_day.id, exercise_id=item.exercise_id,
                    position=item.position, sets=item.sets, reps=item.reps,
                    target_weight=item.target_weight, rest_seconds=item.rest_seconds,
                    notes=item.notes,
                ))
    log_action(db, actor, "template.apply", "program_week", week.id,
               f"template={template.id} client={client.id} week_start={week_start}")
    from . import notifications
    notifications.notify_program_published(db, week)
    db.commit()
    return week
