"""
Роутер управления программой тренировок (train-режим).
РОП назначает программу для своей команды.
"""
from datetime import datetime
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from database import get_db
from deps import require_user, require_capability
from models import Team, TeamMember, TrainingProgram, TrainingProgramDay

router = APIRouter(tags=["training_program"])

_STAGES = [
    {"key": "contact",      "label": "Вступление в контакт"},
    {"key": "needs",        "label": "Работа с потребностями"},
    {"key": "presentation", "label": "Презентация"},
    {"key": "objections",   "label": "Работа с возражениями"},
    {"key": "closing",      "label": "Завершение сделки"},
]


@router.get(
    "/teams/{team_id}/program",
    response_class=HTMLResponse,
    dependencies=[Depends(require_capability("training_program"))],
)
def team_program_page(team_id: int, request: Request, db: Session = Depends(get_db)):
    """Страница управления программой тренировок команды (для РОПа)."""
    user = require_user(request, db)
    team = db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Команда не найдена")
    if team.manager_id != user.id:
        raise HTTPException(403, "Только менеджер команды может управлять программой")

    program = (
        db.query(TrainingProgram)
        .filter(TrainingProgram.team_id == team_id, TrainingProgram.is_active == True)
        .first()
    )

    return request.app.state.templates.TemplateResponse(
        "train/team_program.html",
        {
            "request": request,
            "user": user,
            "team": team,
            "program": program,
            "stages": _STAGES,
        },
    )


@router.post(
    "/teams/{team_id}/program",
    dependencies=[Depends(require_capability("training_program"))],
)
async def save_team_program(
    team_id: int,
    request: Request,
    db: Session = Depends(get_db),
    cycle_days: int = Form(5),
):
    """Сохраняет/обновляет программу тренировок. Этапы передаются как stage_key_N."""
    user = require_user(request, db)
    team = db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Команда не найдена")
    if team.manager_id != user.id:
        raise HTTPException(403, "Только менеджер команды может управлять программой")

    form = await request.form()

    # Деактивируем предыдущую программу
    (
        db.query(TrainingProgram)
        .filter(TrainingProgram.team_id == team_id)
        .update({"is_active": False})
    )

    program = TrainingProgram(
        team_id=team_id,
        created_by=user.id,
        name=str(form.get("name", "Программа тренировок")),
        start_date=datetime.utcnow(),
        cycle_days=max(1, cycle_days),
        is_active=True,
    )
    db.add(program)
    db.flush()

    # Считываем порядок дней из формы
    day_index = 0
    for i in range(1, 31):  # макс 30 дней в цикле
        stage_key = form.get(f"stage_key_{i}")
        if stage_key and stage_key in {s["key"] for s in _STAGES}:
            db.add(TrainingProgramDay(
                program_id=program.id,
                day_index=day_index,
                stage_key=stage_key,
            ))
            day_index += 1

    db.commit()
    return RedirectResponse(url=f"/teams/{team_id}/program", status_code=302)
