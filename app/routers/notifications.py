"""
API для работы с уведомлениями
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel

from database import get_db
from deps import require_user
from models import User
from services.notification_service import (
    get_notification_service,
    NotificationType,
    NotificationPriority
)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class NotificationResponse(BaseModel):
    """Ответ с уведомлением"""
    id: str
    type: str
    title: str
    message: str
    priority: int
    duration: Optional[int]
    action_label: Optional[str]
    action_url: Optional[str]
    dismissible: bool
    created_at: str
    read: bool


class NotificationsListResponse(BaseModel):
    """Список уведомлений"""
    notifications: List[NotificationResponse]
    unread_count: int
    total_count: int


@router.get("/unread", response_model=NotificationsListResponse)
async def get_unread_notifications(
    limit: int = 10,
    user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Получить непрочитанные уведомления"""
    service = get_notification_service()
    
    notifications = service.get_notifications(
        user_id=user.id,
        unread_only=True,
        limit=limit
    )
    
    unread_count = service.get_unread_count(user.id)
    
    return {
        "notifications": notifications,
        "unread_count": unread_count,
        "total_count": len(notifications)
    }


@router.get("/all", response_model=NotificationsListResponse)
async def get_all_notifications(
    limit: int = 50,
    user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Получить все уведомления"""
    service = get_notification_service()
    
    notifications = service.get_notifications(
        user_id=user.id,
        unread_only=False,
        limit=limit
    )
    
    unread_count = service.get_unread_count(user.id)
    
    return {
        "notifications": notifications,
        "unread_count": unread_count,
        "total_count": len(notifications)
    }


@router.post("/{notification_id}/read")
async def mark_notification_as_read(
    notification_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Отметить уведомление как прочитанное"""
    service = get_notification_service()
    
    success = service.mark_as_read(user.id, notification_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Уведомление не найдено")
    
    return {"success": True}


@router.post("/read-all")
async def mark_all_as_read(
    user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Отметить все уведомления как прочитанные"""
    service = get_notification_service()
    
    count = service.mark_all_as_read(user.id)
    
    return {"success": True, "count": count}


@router.delete("/{notification_id}")
async def dismiss_notification(
    notification_id: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Удалить уведомление"""
    service = get_notification_service()
    
    success = service.dismiss_notification(user.id, notification_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Уведомление не найдено")
    
    return {"success": True}


@router.delete("/clear-all")
async def clear_all_notifications(
    user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Очистить все уведомления"""
    service = get_notification_service()
    
    count = service.clear_all(user.id)
    
    return {"success": True, "count": count}


@router.get("/count")
async def get_unread_count(
    user: User = Depends(require_user),
    db: Session = Depends(get_db)
):
    """Получить количество непрочитанных уведомлений"""
    service = get_notification_service()
    
    count = service.get_unread_count(user.id)
    
    return {"unread_count": count}
