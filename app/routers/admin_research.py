"""
Админская страница "Исследование" (Research Mode).

Показывает CoT-логи всех AI-вызовов pipeline анализа звонков:
- список с фильтрами и пагинацией;
- просмотр содержимого .txt в браузере;
- скачивание исходного файла;
- удаление записи + файла.

Доступ — только админам.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from admin import get_current_user, is_admin
from database import get_db
from models import Conversation, ResearchLog, User
from services.research_service import parse_research_file

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/research", tags=["admin-research"])

UPLOAD_DIR = os.path.abspath("uploads")
PAGE_SIZE = 30
MAX_VIEW_BYTES = 2 * 1024 * 1024  # 2MB — больше не показываем в браузере, только скачать


def _ensure_admin(request: Request, db: Session) -> User:
    current_user = get_current_user(request, db)
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для раздела «Исследование»",
        )
    return current_user


def _resolve_abs_path(rel_path: str) -> Optional[str]:
    """
    Безопасно резолвит относительный путь файла внутри UPLOAD_DIR.
    Возвращает None, если путь выходит за границы.
    """
    if not rel_path:
        return None
    abs_path = os.path.abspath(os.path.join(UPLOAD_DIR, rel_path))
    if not abs_path.startswith(UPLOAD_DIR + os.sep) and abs_path != UPLOAD_DIR:
        return None
    return abs_path


def _format_size(num_bytes: int) -> str:
    if num_bytes is None:
        return "—"
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.2f} MB"


def _summarize_models(models_used_json: Optional[str]) -> str:
    if not models_used_json:
        return "—"
    try:
        data = json.loads(models_used_json)
        if not isinstance(data, dict) or not data:
            return "—"
        return ", ".join(f"{m} ×{c}" for m, c in data.items())
    except (json.JSONDecodeError, ValueError):
        return "—"


@router.get("/", response_class=HTMLResponse)
def admin_research_list(
    request: Request,
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    status_filter: Optional[str] = Query(None, alias="status"),
    user_id_filter: Optional[int] = Query(None, alias="user_id"),
    conversation_id_filter: Optional[int] = Query(None, alias="conversation_id"),
):
    """Список research-логов с фильтрами и пагинацией."""
    current_user = _ensure_admin(request, db)

    query = db.query(ResearchLog)

    if status_filter and status_filter in ("running", "completed", "failed"):
        query = query.filter(ResearchLog.status == status_filter)
    if user_id_filter:
        query = query.filter(ResearchLog.user_id == user_id_filter)
    if conversation_id_filter:
        query = query.filter(ResearchLog.conversation_id == conversation_id_filter)

    total = query.count()
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, total_pages)

    logs = (
        query.order_by(ResearchLog.created_at.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
        .all()
    )

    user_ids = {log.user_id for log in logs}
    users_map = {
        u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()
    } if user_ids else {}

    # Общие метрики
    stats_total = db.query(ResearchLog).count()
    stats_completed = db.query(ResearchLog).filter(ResearchLog.status == "completed").count()
    stats_failed = db.query(ResearchLog).filter(ResearchLog.status == "failed").count()
    stats_running = db.query(ResearchLog).filter(ResearchLog.status == "running").count()

    rows = []
    for log in logs:
        user = users_map.get(log.user_id)
        rows.append({
            "id": log.id,
            "conversation_id": log.conversation_id,
            "user_id": log.user_id,
            "user_email": user.email if user else None,
            "user_name": user.name if user else None,
            "file_name": log.file_name,
            "file_size_human": _format_size(log.file_size_bytes or 0),
            "stages_count": log.stages_count or 0,
            "total_input_tokens": log.total_input_tokens or 0,
            "total_output_tokens": log.total_output_tokens or 0,
            "total_tokens": (log.total_input_tokens or 0) + (log.total_output_tokens or 0),
            "models_summary": _summarize_models(log.models_used),
            "status": log.status,
            "error_message": log.error_message,
            "created_at": log.created_at,
            "completed_at": log.completed_at,
        })

    return request.app.state.templates.TemplateResponse(
        "admin/research_list.html",
        {
            "request": request,
            "current_user": current_user,
            "rows": rows,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "stats": {
                "total": stats_total,
                "completed": stats_completed,
                "failed": stats_failed,
                "running": stats_running,
            },
            "filters": {
                "status": status_filter or "",
                "user_id": user_id_filter or "",
                "conversation_id": conversation_id_filter or "",
            },
        },
    )


@router.get("/{log_id}", response_class=HTMLResponse)
def admin_research_view(
    log_id: int,
    request: Request,
    db: Session = Depends(get_db),
    mode: str = Query("cards", regex="^(cards|raw)$"),
):
    """
    Просмотр research-файла.

    mode=cards (по умолчанию) — структурированный HTML с карточками этапов,
                                раскрывающимися reasoning-блоками и фильтрами.
    mode=raw                  — оригинальный .txt в <pre> для копирования.
    """
    current_user = _ensure_admin(request, db)

    log = db.query(ResearchLog).filter(ResearchLog.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Research-лог не найден")

    user = db.query(User).filter(User.id == log.user_id).first()
    conversation = db.query(Conversation).filter(Conversation.id == log.conversation_id).first()

    abs_path = _resolve_abs_path(log.file_path)
    file_content: Optional[str] = None
    file_missing = False
    too_large = False
    parsed: Optional[dict] = None

    if not abs_path or not os.path.isfile(abs_path):
        file_missing = True
    else:
        try:
            size = os.path.getsize(abs_path)
            if size > MAX_VIEW_BYTES:
                too_large = True
            else:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    file_content = f.read()
                if mode == "cards" and file_content:
                    try:
                        parsed = parse_research_file(file_content)
                    except Exception as e:  # noqa: BLE001
                        logger.warning(f"Failed to parse research file {abs_path}: {e}")
                        parsed = None
        except OSError as e:
            logger.warning(f"Failed to read research file {abs_path}: {e}")
            file_missing = True

    return request.app.state.templates.TemplateResponse(
        "admin/research_view.html",
        {
            "request": request,
            "current_user": current_user,
            "mode": mode,
            "log": {
                "id": log.id,
                "conversation_id": log.conversation_id,
                "user_id": log.user_id,
                "user_email": user.email if user else None,
                "user_name": user.name if user else None,
                "conv_title": conversation.title if conversation else None,
                "file_name": log.file_name,
                "file_size_human": _format_size(log.file_size_bytes or 0),
                "stages_count": log.stages_count or 0,
                "total_input_tokens": log.total_input_tokens or 0,
                "total_output_tokens": log.total_output_tokens or 0,
                "models_summary": _summarize_models(log.models_used),
                "status": log.status,
                "error_message": log.error_message,
                "created_at": log.created_at,
                "completed_at": log.completed_at,
            },
            "file_content": file_content,
            "file_missing": file_missing,
            "too_large": too_large,
            "parsed": parsed,
        },
    )


@router.get("/{log_id}/download")
def admin_research_download(
    log_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Скачивание .txt файла."""
    _ensure_admin(request, db)

    log = db.query(ResearchLog).filter(ResearchLog.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Research-лог не найден")

    abs_path = _resolve_abs_path(log.file_path)
    if not abs_path or not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="Файл research-лога не найден на диске")

    return FileResponse(
        abs_path,
        media_type="text/plain; charset=utf-8",
        filename=log.file_name,
    )


@router.post("/{log_id}/delete")
def admin_research_delete(
    log_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Удаляет запись + файл с диска."""
    _ensure_admin(request, db)

    log = db.query(ResearchLog).filter(ResearchLog.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Research-лог не найден")

    abs_path = _resolve_abs_path(log.file_path)
    if abs_path and os.path.isfile(abs_path):
        try:
            os.remove(abs_path)
        except OSError as e:
            logger.warning(f"Failed to delete research file {abs_path}: {e}")

    db.delete(log)
    db.commit()
    return RedirectResponse(url="/admin/research", status_code=status.HTTP_302_FOUND)
