"""
API для отслеживания прогресса операций
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel

from database import get_db
from deps import require_user
from models import User
from services.progress_tracker import get_progress_tracker, format_time_remaining

router = APIRouter(prefix="/api/progress", tags=["progress"])


class ProgressResponse(BaseModel):
    """Ответ с информацией о прогрессе"""
    operation_id: str
    title: str
    status: str
    current_stage: int
    total_stages: int
    stage_name: str
    stage_message: str
    percentage: int
    started_at: str
    updated_at: str
    completed_at: Optional[str]
    error_message: Optional[str]
    estimated_time_remaining: Optional[int]
    estimated_time_formatted: Optional[str]
    can_cancel: bool


@router.get("/{operation_id}", response_model=ProgressResponse)
async def get_operation_progress(
    operation_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Получить информацию о прогрессе операции"""
    tracker = get_progress_tracker()
    
    progress = tracker.get_operation(operation_id)
    
    if not progress:
        raise HTTPException(status_code=404, detail="Операция не найдена")
    
    data = progress.to_dict()
    data["estimated_time_formatted"] = format_time_remaining(
        progress.estimated_time_remaining
    )
    
    return data


@router.get("/active/list")
async def get_active_operations(
    limit: int = 10,
    user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Получить список активных операций"""
    tracker = get_progress_tracker()
    
    operations = tracker.list_active_operations(limit=limit)
    
    # Добавляем форматированное время
    for op in operations:
        op["estimated_time_formatted"] = format_time_remaining(
            op.get("estimated_time_remaining")
        )
    
    return {
        "operations": operations,
        "count": len(operations)
    }


@router.post("/{operation_id}/cancel")
async def cancel_operation(
    operation_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Отменить операцию"""
    tracker = get_progress_tracker()
    
    success = tracker.cancel_operation(operation_id)
    
    if not success:
        raise HTTPException(
            status_code=400,
            detail="Операцию невозможно отменить или она не найдена"
        )
    
    return {"success": True, "message": "Операция отменена"}

