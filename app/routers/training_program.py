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

    # Цикл не может быть короче числа назначенных дней: иначе последние дни
    # (day_index >= cycle_days) никогда не попали бы в расписание участникам.
    # Если цикл длиннее — лишние дни остаются выходными (это допустимо).
    if day_index > program.cycle_days:
        program.cycle_days = day_index

    db.commit()
    return RedirectResponse(url=f"/teams/{team_id}/program", status_code=302)


@router.post(
    "/trainings/program/start-today",
    dependencies=[Depends(require_capability("training_program"))],
)
def start_today_training(request: Request, db: Session = Depends(get_db)):
    """
    Запуск тренировки дня из активной программы команды (для участника).
    Этап берётся из расписания программы по сегодняшнему дню цикла, тренировка
    создаётся on-demand через curriculum_service (без хранения training_id).
    Идемпотентно в рамках дня: переиспользует уже созданную сегодня программную
    тренировку того же этапа, если она ещё не завершена.
    """
    user = require_user(request, db)

    from services import streak_service
    from services.curriculum_service import create_from_catalog
    from models import AnalysisTrainingPlan, Training
    from time_utils import today_local, local_date

    stage = streak_service.today_stage(db, user)
    if not stage:
        # сегодня выходной по программе или программа не назначена
        return RedirectResponse(url="/trainings/catalog", status_code=302)

    stage_key = stage["stage_key"]

    # Сериализуем параллельные запросы одного пользователя (двойной клик «Начать»),
    # чтобы не создать два плана за один день. Транзакционный advisory-lock
    # снимается при первом же commit (внутри create_from_catalog), но к этому
    # моменту проверка-и-создание уже атомарна относительно второго запроса.
    from sqlalchemy import text
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": int(user.id)})

    # Переиспользуем сегодняшнюю незавершённую программную тренировку этого этапа
    existing = (
        db.query(Training)
        .join(AnalysisTrainingPlan, Training.plan_id == AnalysisTrainingPlan.id)
        .filter(
            AnalysisTrainingPlan.user_id == user.id,
            AnalysisTrainingPlan.plan_source == "program",
            Training.scenario_type == stage_key,
            Training.status != "completed",
        )
        .order_by(Training.id.desc())
        .first()
    )

    training_id = None
    if existing and existing.plan and existing.plan.created_at \
            and local_date(existing.plan.created_at) == today_local():
        training_id = existing.id
    else:
        try:
            _, training_id = create_from_catalog(
                db, user, stage_key, level=1, source="program"
            )
        except ValueError:
            # промпт для этапа не найден — отправляем в каталог
            return RedirectResponse(url="/trainings/catalog", status_code=302)

    return RedirectResponse(
        url=f"/voice-training/training?training_id={training_id}", status_code=302
    )
