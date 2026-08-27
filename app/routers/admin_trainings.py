"""
Голосовые тренировки в админке — запуск без анализа звонка.

Обычный путь к тренировке идёт через анализ звонка: загрузили запись, GPT
разобрал её, из рекомендаций собрался план тренировок. Для проверки промптов
это слишком долго, поэтому здесь список этапов продаж и кнопка «Начать»:
тренировка создаётся сразу, на stub-звонке, без анализа и без GPT.

GET  /admin/trainings        — список этапов
POST /admin/trainings/start  — создать тренировку и перейти к ней
"""
from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from admin import get_current_user, is_admin
from database import get_db

import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/trainings", tags=["admin-trainings"])


def _require_admin(request: Request, db: Session):
    """Пускает только администраторов — страница создаёт данные в БД."""
    current_user = get_current_user(request, db)
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для доступа к тренировкам",
        )
    return current_user


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def admin_trainings_page(request: Request, db: Session = Depends(get_db)):
    """Список этапов продаж с кнопкой запуска голосовой тренировки."""
    current_user = _require_admin(request, db)

    from services.curriculum_service import get_stage_overview

    stages = get_stage_overview()
    return request.app.state.templates.TemplateResponse(
        "admin/trainings.html",
        {"request": request, "current_user": current_user, "stages": stages},
    )


@router.post("/start")
def admin_training_start(
    request: Request,
    db: Session = Depends(get_db),
    stage_key: str = Form(...),
):
    """
    Создаёт тренировку по выбранному этапу и уводит на голосовую сессию.

    plan_source="admin" — чтобы эти запуски не смешивались с планами из
    анализа звонков и с программой команды в отчётах.
    """
    current_user = _require_admin(request, db)

    from services.curriculum_service import create_from_catalog

    try:
        plan_id, training_id = create_from_catalog(
            db, current_user, stage_key, level=1, source="admin"
        )
    except ValueError as e:
        logger.warning(f"Админ-тренировка не создана (stage={stage_key}): {e}")
        raise HTTPException(status_code=400, detail=str(e))

    logger.info(
        f"🎓 Админ {current_user.email} запустил тренировку '{stage_key}' "
        f"(plan={plan_id}, training={training_id})"
    )
    return RedirectResponse(
        url=f"/voice-training/training?training_id={training_id}",
        status_code=302,
    )
