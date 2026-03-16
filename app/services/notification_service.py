"""
Сервис уведомлений пользователю
"""
from typing import Dict, List, Optional, Any
from datetime import datetime
from enum import Enum
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


class Notification:
    """Уведомление"""
    
    def __init__(
        self,
        id: str,
        type: NotificationType,
        title: str,
        message: str,
        priority: NotificationPriority = NotificationPriority.NORMAL,
        duration: Optional[int] = None,  # Длительность показа в мс (None = до закрытия)
        action_label: Optional[str] = None,
        action_url: Optional[str] = None,
        dismissible: bool = True,
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.id = id
        self.type = type
        self.title = title
        self.message = message
        self.priority = priority
        self.duration = duration
        self.action_label = action_label
        self.action_url = action_url
        self.dismissible = dismissible
        self.created_at = datetime.utcnow()
        self.read = False
        self.metadata = metadata or {}
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертация в словарь"""
        return {
            "id": self.id,
            "type": self.type.value,
            "title": self.title,
            "message": self.message,
            "priority": self.priority.value,
            "duration": self.duration,
            "action_label": self.action_label,
            "action_url": self.action_url,
            "dismissible": self.dismissible,
            "created_at": self.created_at.isoformat(),
            "read": self.read,
            "metadata": self.metadata
        }


class NotificationService:
    """Сервис управления уведомлениями"""
    
    def __init__(self):
        # Хранилище уведомлений по пользователям
        self.user_notifications: Dict[int, List[Notification]] = {}
        self.max_notifications_per_user = 100
        self.notification_counter = 0
    
    def _generate_id(self) -> str:
        """Генерация уникального ID уведомления"""
        self.notification_counter += 1
        return f"notif_{int(datetime.utcnow().timestamp())}_{self.notification_counter}"
    
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
        metadata: Optional[Dict[str, Any]] = None
    ) -> Notification:
        """Добавить уведомление для пользователя"""
        
        notification = Notification(
            id=self._generate_id(),
            type=type,
            title=title,
            message=message,
            priority=priority,
            duration=duration,
            action_label=action_label,
            action_url=action_url,
            dismissible=dismissible,
            metadata=metadata
        )
        
        if user_id not in self.user_notifications:
            self.user_notifications[user_id] = []
        
        # Добавляем в начало списка
        self.user_notifications[user_id].insert(0, notification)
        
        # Ограничиваем количество уведомлений
        if len(self.user_notifications[user_id]) > self.max_notifications_per_user:
            self.user_notifications[user_id] = self.user_notifications[user_id][:self.max_notifications_per_user]
        
        logger.info(f"Added {type.value} notification for user {user_id}: {title}")
        return notification
    
    def get_notifications(
        self,
        user_id: int,
        unread_only: bool = False,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Получить уведомления пользователя"""
        if user_id not in self.user_notifications:
            return []
        
        notifications = self.user_notifications[user_id]
        
        if unread_only:
            notifications = [n for n in notifications if not n.read]
        
        if limit:
            notifications = notifications[:limit]
        
        return [n.to_dict() for n in notifications]
    
    def mark_as_read(self, user_id: int, notification_id: str) -> bool:
        """Отметить уведомление как прочитанное"""
        if user_id not in self.user_notifications:
            return False
        
        for notification in self.user_notifications[user_id]:
            if notification.id == notification_id:
                notification.read = True
                logger.debug(f"Marked notification {notification_id} as read for user {user_id}")
                return True
        
        return False
    
    def mark_all_as_read(self, user_id: int) -> int:
        """Отметить все уведомления как прочитанные"""
        if user_id not in self.user_notifications:
            return 0
        
        count = 0
        for notification in self.user_notifications[user_id]:
            if not notification.read:
                notification.read = True
                count += 1
        
        logger.info(f"Marked {count} notifications as read for user {user_id}")
        return count
    
    def dismiss_notification(self, user_id: int, notification_id: str) -> bool:
        """Удалить уведомление"""
        if user_id not in self.user_notifications:
            return False
        
        notifications = self.user_notifications[user_id]
        for i, notification in enumerate(notifications):
            if notification.id == notification_id:
                notifications.pop(i)
                logger.debug(f"Dismissed notification {notification_id} for user {user_id}")
                return True
        
        return False
    
    def get_unread_count(self, user_id: int) -> int:
        """Получить количество непрочитанных уведомлений"""
        if user_id not in self.user_notifications:
            return 0
        
        return sum(1 for n in self.user_notifications[user_id] if not n.read)
    
    def clear_all(self, user_id: int) -> int:
        """Очистить все уведомления пользователя"""
        if user_id not in self.user_notifications:
            return 0
        
        count = len(self.user_notifications[user_id])
        self.user_notifications[user_id] = []
        logger.info(f"Cleared {count} notifications for user {user_id}")
        return count
    
    # Хелперы для быстрого создания типичных уведомлений
    
    def success(
        self,
        user_id: int,
        title: str,
        message: str,
        duration: int = 5000,
        **kwargs
    ) -> Notification:
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
    ) -> Notification:
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
    ) -> Notification:
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
    ) -> Notification:
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
