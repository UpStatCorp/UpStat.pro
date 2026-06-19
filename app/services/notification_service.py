"""
Сервис уведомлений пользователю.

Хранилище — таблица `notifications` в Postgres (модель `Notification` в models.py),
а НЕ память процесса. Это обязательно: анализы исполняются в отдельном
worker-процессе, и уведомление, созданное воркером, должно быть видно вебу.
Сервис stateless — создавать `NotificationService()` напрямую безопасно.
"""
from typing import Dict, List, Optional, Any
from datetime import datetime
from enum import Enum
import json
import logging

logger = logging.getLogger(__name__)


class NotificationType(Enum):
    """Типы уведомлений"""
    SUCCESS = "success"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    PROGRESS = "progress"


class NotificationPriority(Enum):
    """Приоритеты уведомлений"""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4


_ICONS = {
    NotificationType.SUCCESS: "✅",
    NotificationType.ERROR: "❌",
    NotificationType.WARNING: "⚠️",
    NotificationType.INFO: "ℹ️",
    NotificationType.PROGRESS: "⏳",
}

MAX_NOTIFICATIONS_PER_USER = 100


class _Created:
    """Лёгкий результат add_notification (id строкой — совместимо с прежним API)."""
    def __init__(self, id_: int):
        self.id = str(id_)


def _row_to_dict(n) -> Dict[str, Any]:
    """DB-строка `notifications` → словарь прежней формы (для роутера/фронта)."""
    meta = {}
    if n.metadata_json:
        try:
            meta = json.loads(n.metadata_json)
        except (json.JSONDecodeError, TypeError):
            meta = {}
    return {
        "id": str(n.id),  # снаружи id остаётся строкой — схема роутера не меняется
        "type": n.type,
        "title": n.title,
        "message": n.message,
        "priority": int(meta.get("priority", NotificationPriority.NORMAL.value)),
        "duration": meta.get("duration"),
        "action_label": n.link_text,
        "action_url": n.link,
        "dismissible": bool(meta.get("dismissible", True)),
        "created_at": n.created_at.isoformat() if n.created_at else datetime.utcnow().isoformat(),
        "read": bool(n.is_read),
        "metadata": meta.get("metadata", {}),
    }


class NotificationService:
    """Сервис управления уведомлениями (поверх таблицы notifications)."""

    def add_notification(
        self,
        user_id: int,
        type: NotificationType,
        title: str,
        message: str,
        priority: NotificationPriority = NotificationPriority.NORMAL,
        duration: Optional[int] = None,
        action_label: Optional[str] = None,
        action_url: Optional[str] = None,
        dismissible: bool = True,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> _Created:
        """Создать уведомление в БД для пользователя."""
        from database import SessionLocal
        from models import Notification as NotificationModel

        meta_blob = json.dumps(
            {
                "priority": priority.value,
                "duration": duration,
                "dismissible": dismissible,
                "metadata": metadata or {},
            },
            ensure_ascii=False,
        )
        db = SessionLocal()
        try:
            n = NotificationModel(
                user_id=user_id,
                type=type.value,
                title=title,
                message=message,
                icon=_ICONS.get(type, "🔔"),
                link=action_url,
                link_text=action_label,
                is_read=False,
                metadata_json=meta_blob,
            )
            db.add(n)
            db.commit()
            db.refresh(n)
            new_id = n.id
            self._trim(db, user_id)
            logger.info(f"Added {type.value} notification for user {user_id}: {title}")
            return _Created(new_id)
        except Exception as e:  # noqa: BLE001
            logger.error(f"add_notification failed for user {user_id}: {e}")
            try:
                db.rollback()
            except Exception:
                pass
            return _Created(0)
        finally:
            db.close()

    def _trim(self, db, user_id: int) -> None:
        """Оставляем не больше MAX_NOTIFICATIONS_PER_USER последних — удаляем старые."""
        from models import Notification as NotificationModel
        old_ids = [
            row[0]
            for row in db.query(NotificationModel.id)
            .filter(NotificationModel.user_id == user_id)
            .order_by(NotificationModel.created_at.desc())
            .offset(MAX_NOTIFICATIONS_PER_USER)
            .all()
        ]
        if old_ids:
            db.query(NotificationModel).filter(NotificationModel.id.in_(old_ids)).delete(
                synchronize_session=False
            )
            db.commit()

    def get_notifications(
        self,
        user_id: int,
        unread_only: bool = False,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Получить уведомления пользователя (новые сверху)."""
        from database import SessionLocal
        from models import Notification as NotificationModel
        db = SessionLocal()
        try:
            q = db.query(NotificationModel).filter(NotificationModel.user_id == user_id)
            if unread_only:
                q = q.filter(NotificationModel.is_read == False)  # noqa: E712
            q = q.order_by(NotificationModel.created_at.desc())
            if limit:
                q = q.limit(limit)
            return [_row_to_dict(n) for n in q.all()]
        finally:
            db.close()

    def mark_as_read(self, user_id: int, notification_id: str) -> bool:
        """Отметить уведомление прочитанным."""
        from database import SessionLocal
        from models import Notification as NotificationModel
        try:
            nid = int(notification_id)
        except (TypeError, ValueError):
            return False
        db = SessionLocal()
        try:
            n = db.query(NotificationModel).filter(
                NotificationModel.id == nid, NotificationModel.user_id == user_id
            ).first()
            if not n:
                return False
            if not n.is_read:
                n.is_read = True
                n.read_at = datetime.utcnow()
                db.commit()
            return True
        finally:
            db.close()

    def mark_all_as_read(self, user_id: int) -> int:
        """Отметить все уведомления прочитанными."""
        from database import SessionLocal
        from models import Notification as NotificationModel
        db = SessionLocal()
        try:
            count = db.query(NotificationModel).filter(
                NotificationModel.user_id == user_id, NotificationModel.is_read == False  # noqa: E712
            ).update({"is_read": True, "read_at": datetime.utcnow()}, synchronize_session=False)
            db.commit()
            return int(count or 0)
        finally:
            db.close()

    def dismiss_notification(self, user_id: int, notification_id: str) -> bool:
        """Удалить уведомление."""
        from database import SessionLocal
        from models import Notification as NotificationModel
        try:
            nid = int(notification_id)
        except (TypeError, ValueError):
            return False
        db = SessionLocal()
        try:
            deleted = db.query(NotificationModel).filter(
                NotificationModel.id == nid, NotificationModel.user_id == user_id
            ).delete(synchronize_session=False)
            db.commit()
            return bool(deleted)
        finally:
            db.close()

    def get_unread_count(self, user_id: int) -> int:
        """Количество непрочитанных."""
        from database import SessionLocal
        from models import Notification as NotificationModel
        db = SessionLocal()
        try:
            return int(
                db.query(NotificationModel).filter(
                    NotificationModel.user_id == user_id, NotificationModel.is_read == False  # noqa: E712
                ).count()
            )
        finally:
            db.close()

    def clear_all(self, user_id: int) -> int:
        """Удалить все уведомления пользователя."""
        from database import SessionLocal
        from models import Notification as NotificationModel
        db = SessionLocal()
        try:
            count = db.query(NotificationModel).filter(
                NotificationModel.user_id == user_id
            ).delete(synchronize_session=False)
            db.commit()
            return int(count or 0)
        finally:
            db.close()
    
    # Хелперы для быстрого создания типичных уведомлений
    
    def success(
        self,
        user_id: int,
        title: str,
        message: str,
        duration: int = 5000,
        **kwargs
    ) -> "_Created":
        """Уведомление об успехе"""
        return self.add_notification(
            user_id=user_id,
            type=NotificationType.SUCCESS,
            title=title,
            message=message,
            duration=duration,
            **kwargs
        )
    
    def error(
        self,
        user_id: int,
        title: str,
        message: str,
        duration: Optional[int] = None,
        **kwargs
    ) -> "_Created":
        """Уведомление об ошибке"""
        return self.add_notification(
            user_id=user_id,
            type=NotificationType.ERROR,
            title=title,
            message=message,
            priority=NotificationPriority.HIGH,
            duration=duration,
            **kwargs
        )
    
    def warning(
        self,
        user_id: int,
        title: str,
        message: str,
        duration: int = 8000,
        **kwargs
    ) -> "_Created":
        """Предупреждение"""
        return self.add_notification(
            user_id=user_id,
            type=NotificationType.WARNING,
            title=title,
            message=message,
            priority=NotificationPriority.NORMAL,
            duration=duration,
            **kwargs
        )
    
    def info(
        self,
        user_id: int,
        title: str,
        message: str,
        duration: int = 5000,
        **kwargs
    ) -> "_Created":
        """Информационное уведомление"""
        return self.add_notification(
            user_id=user_id,
            type=NotificationType.INFO,
            title=title,
            message=message,
            duration=duration,
            **kwargs
        )


# Глобальный экземпляр сервиса
_notification_service: Optional[NotificationService] = None


def get_notification_service() -> NotificationService:
    """Получить глобальный экземпляр сервиса уведомлений"""
    global _notification_service
    if _notification_service is None:
        _notification_service = NotificationService()
    return _notification_service
