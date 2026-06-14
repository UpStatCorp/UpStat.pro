"""
Каталог тренировок для train-режима.
GET  /trainings/catalog        — страница каталога
POST /trainings/catalog/start  — создать тренировку из каталога и перейти к ней
"""
from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from database import get_db
from deps import require_user, require_capability

router = APIRouter(tags=["training_catalog"])


@router.get("/trainings/catalog", response_class=HTMLResponse,
            dependencies=[Depends(require_capability("training_catalog"))])
def catalog_page(request: Request, db: Session = Depends(get_db)):
    """Каталог этапов тренировок."""
    user = require_user(request, db)
    from services.curriculum_service import get_catalog
    stages = get_catalog()
    return request.app.state.templates.TemplateResponse(
        "train/catalog.html",
        {"request": request, "user": user, "stages": stages},
    )


@router.post("/trainings/catalog/start",
             dependencies=[Depends(require_capability("training_catalog"))])
async def catalog_start(
    request: Request,
    db: Session = Depends(get_db),
    stage_key: str = Form(...),
    level: int = Form(1),
):
    """Создать stub-тренировку и перейти на voice-training."""
    user = require_user(request, db)
    from services.curriculum_service import create_from_catalog
    try:
        plan_id, training_id = create_from_catalog(db, user, stage_key, level)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return RedirectResponse(
        url=f"/voice-training/training?training_id={training_id}",
        status_code=302,
    )
