"""
Система отслеживания прогресса длительных операций.

Хранилище — Redis (ключ `progress:op:{id}`), чтобы прогресс, записанный
worker-процессом, был виден вебу. Если REDIS_URL не задан/недоступен —
graceful-фолбэк на память процесса (как в middleware/rate_limit.py).
"""
import asyncio
import os
import json
import time
from typing import Dict, Optional, Callable, Any, List
from datetime import datetime
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class ProgressStatus(Enum):
    """Статусы прогресса операции"""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ProgressStage(Enum):
    """Этапы обработки"""
    UPLOAD = "upload"
    CONVERSION = "conversion"
    TRANSCRIPTION = "transcription"
    ANALYSIS = "analysis"
    REPORT_GENERATION = "report_generation"
    COMPLETED = "completed"


class ProgressInfo:
    """Информация о прогрессе операции"""
    
    def __init__(
        self,
        operation_id: str,
        total_stages: int = 5,
        title: str = "Обработка..."
    ):
        self.operation_id = operation_id
        self.title = title
        self.status = ProgressStatus.PENDING
        self.current_stage = 0
        self.total_stages = total_stages
        self.stage_name = ""
        self.stage_message = ""
        self.percentage = 0
        self.started_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
        self.completed_at: Optional[datetime] = None
        self.error_message: Optional[str] = None
        self.estimated_time_remaining: Optional[int] = None  # в секундах
        self.can_cancel = False
        self.metadata: Dict[str, Any] = {}
        
        # Временные метки для расчета времени
        self.stage_start_times: Dict[int, float] = {}
        self.stage_durations: Dict[int, float] = {}
    
    def start_stage(self, stage_number: int, stage_name: str, message: str):
        """Начать новый этап"""
        self.current_stage = stage_number
        self.stage_name = stage_name
        self.stage_message = message
        self.status = ProgressStatus.IN_PROGRESS
        self.updated_at = datetime.utcnow()
        self.stage_start_times[stage_number] = time.time()
        
        # Расчет процента
        self.percentage = int((stage_number / self.total_stages) * 100)
        
        # Расчет примерного времени
        self._estimate_remaining_time()
    
    def complete_stage(self, stage_number: int):
        """Завершить этап"""
        if stage_number in self.stage_start_times:
            duration = time.time() - self.stage_start_times[stage_number]
            self.stage_durations[stage_number] = duration
        self.updated_at = datetime.utcnow()
        self._estimate_remaining_time()
    
    def update_message(self, message: str):
        """Обновить сообщение текущего этапа"""
        self.stage_message = message
        self.updated_at = datetime.utcnow()
    
    def complete(self, message: str = "Готово!"):
        """Завершить операцию успешно"""
        self.status = ProgressStatus.COMPLETED
        self.stage_message = message
        self.percentage = 100
        self.completed_at = datetime.utcnow()
        self.estimated_time_remaining = 0
        self.updated_at = datetime.utcnow()
    
    def fail(self, error_message: str):
        """Отметить операцию как неудачную"""
        self.status = ProgressStatus.FAILED
        self.error_message = error_message
        self.completed_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
    
    def cancel(self):
        """Отменить операцию"""
        if self.can_cancel:
            self.status = ProgressStatus.CANCELLED
            self.completed_at = datetime.utcnow()
            self.updated_at = datetime.utcnow()
    
    def _estimate_remaining_time(self):
        """Расчет примерного оставшегося времени"""
        if len(self.stage_durations) == 0:
            self.estimated_time_remaining = None
            return
        
        # Среднее время на этап
        avg_stage_time = sum(self.stage_durations.values()) / len(self.stage_durations)
        
        # Оставшиеся этапы
        remaining_stages = self.total_stages - self.current_stage
        
        # Примерное время (в секундах)
        self.estimated_time_remaining = int(avg_stage_time * remaining_stages)
    
    def to_dict(self) -> Dict[str, Any]:
        """Конвертация в словарь для JSON"""
        return {
            "operation_id": self.operation_id,
            "title": self.title,
            "status": self.status.value,
            "current_stage": self.current_stage,
            "total_stages": self.total_stages,
            "stage_name": self.stage_name,
            "stage_message": self.stage_message,
            "percentage": self.percentage,
            "started_at": self.started_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error_message": self.error_message,
            "estimated_time_remaining": self.estimated_time_remaining,
            "can_cancel": self.can_cancel,
            "metadata": self.metadata
        }

    def to_state(self) -> Dict[str, Any]:
        """Полное состояние для персистентности (Redis), включая тайминги ETA."""
        d = self.to_dict()
        d["stage_start_times"] = self.stage_start_times
        d["stage_durations"] = self.stage_durations
        return d

    @classmethod
    def from_state(cls, state: Dict[str, Any]) -> "ProgressInfo":
        """Восстановление из состояния (Redis)."""
        p = cls(state["operation_id"], state.get("total_stages", 5), state.get("title", "Обработка..."))
        p.status = ProgressStatus(state.get("status", "pending"))
        p.current_stage = state.get("current_stage", 0)
        p.stage_name = state.get("stage_name", "")
        p.stage_message = state.get("stage_message", "")
        p.percentage = state.get("percentage", 0)
        p.error_message = state.get("error_message")
        p.estimated_time_remaining = state.get("estimated_time_remaining")
        p.can_cancel = state.get("can_cancel", False)
        p.metadata = state.get("metadata", {}) or {}
        try:
            p.started_at = datetime.fromisoformat(state["started_at"]) if state.get("started_at") else p.started_at
            p.updated_at = datetime.fromisoformat(state["updated_at"]) if state.get("updated_at") else p.updated_at
            p.completed_at = datetime.fromisoformat(state["completed_at"]) if state.get("completed_at") else None
        except (ValueError, TypeError):
            pass
        # ключи тайминга приходят из JSON строками — приводим к int
        p.stage_start_times = {int(k): float(v) for k, v in (state.get("stage_start_times") or {}).items()}
        p.stage_durations = {int(k): float(v) for k, v in (state.get("stage_durations") or {}).items()}
        return p


class ProgressTracker:
    """Трекер прогресса операций (Redis-backed, с in-memory фолбэком)."""

    _KEY = "progress:op:{}"
    _ACTIVE = "progress:active"

    def __init__(self):
        self.cleanup_interval = 3600
        self.max_age = int(os.getenv("PROGRESS_TTL_SECONDS", "86400"))  # 24ч
        self._cleanup_task: Optional[asyncio.Task] = None
        self._mem: Dict[str, ProgressInfo] = {}  # фолбэк, если Redis недоступен
        self._redis = None
        self._redis_tried = False

    def _r(self):
        """Ленивый sync-redis (как в rate_limit.py). None → in-memory фолбэк."""
        if self._redis is None and not self._redis_tried:
            self._redis_tried = True
            url = os.getenv("REDIS_URL", "")
            if url:
                try:
                    import redis as _redis
                    client = _redis.from_url(url, decode_responses=True, socket_connect_timeout=2)
                    client.ping()
                    self._redis = client
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"ProgressTracker: Redis недоступен, in-memory фолбэк: {e}")
                    self._redis = None
        return self._redis

    def _save(self, p: ProgressInfo) -> None:
        r = self._r()
        if r is None:
            self._mem[p.operation_id] = p
            return
        try:
            r.set(self._KEY.format(p.operation_id), json.dumps(p.to_state(), ensure_ascii=False), ex=self.max_age)
            if p.status in (ProgressStatus.PENDING, ProgressStatus.IN_PROGRESS):
                r.sadd(self._ACTIVE, p.operation_id)
            else:
                r.srem(self._ACTIVE, p.operation_id)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"ProgressTracker save failed: {e}")
            self._mem[p.operation_id] = p

    def _load(self, operation_id: str) -> Optional[ProgressInfo]:
        r = self._r()
        if r is None:
            return self._mem.get(operation_id)
        try:
            raw = r.get(self._KEY.format(operation_id))
            if not raw:
                return self._mem.get(operation_id)
            return ProgressInfo.from_state(json.loads(raw))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"ProgressTracker load failed: {e}")
            return self._mem.get(operation_id)

    def create_operation(
        self,
        operation_id: str,
        total_stages: int = 5,
        title: str = "Обработка...",
        can_cancel: bool = False,
    ) -> ProgressInfo:
        progress = ProgressInfo(operation_id, total_stages, title)
        progress.can_cancel = can_cancel
        self._save(progress)
        logger.info(f"Created progress tracking for operation {operation_id}")
        return progress

    def get_operation(self, operation_id: str) -> Optional[ProgressInfo]:
        return self._load(operation_id)

    def update_operation(
        self, operation_id: str, stage_number: int, stage_name: str, message: str
    ) -> Optional[ProgressInfo]:
        progress = self._load(operation_id)
        if progress:
            if stage_number > progress.current_stage:
                progress.complete_stage(progress.current_stage)
            progress.start_stage(stage_number, stage_name, message)
            self._save(progress)
        return progress

    def complete_operation(self, operation_id: str, message: str = "Готово!"):
        progress = self._load(operation_id)
        if progress:
            progress.complete(message)
            self._save(progress)
            logger.info(f"Completed operation {operation_id}")

    def fail_operation(self, operation_id: str, error_message: str):
        progress = self._load(operation_id)
        if progress:
            progress.fail(error_message)
            self._save(progress)
            logger.error(f"Failed operation {operation_id}: {error_message}")

    def cancel_operation(self, operation_id: str) -> bool:
        progress = self._load(operation_id)
        if progress and progress.can_cancel:
            progress.cancel()
            self._save(progress)
            logger.info(f"Cancelled operation {operation_id}")
            return True
        return False

    def list_active_operations(self, limit: int = 100) -> List[Dict[str, Any]]:
        r = self._r()
        items: List[Dict[str, Any]] = []
        if r is None:
            items = [
                p.to_dict() for p in self._mem.values()
                if p.status in (ProgressStatus.PENDING, ProgressStatus.IN_PROGRESS)
            ]
        else:
            try:
                for op_id in list(r.smembers(self._ACTIVE)):
                    p = self._load(op_id)
                    if p is None or p.status not in (ProgressStatus.PENDING, ProgressStatus.IN_PROGRESS):
                        r.srem(self._ACTIVE, op_id)  # подчистить протухшие/завершённые
                        continue
                    items.append(p.to_dict())
            except Exception as e:  # noqa: BLE001
                logger.warning(f"ProgressTracker list_active failed: {e}")
        return sorted(items, key=lambda x: x["started_at"], reverse=True)[:limit]

    async def cleanup_old_operations(self):
        """Redis чистит по TTL сам; in-memory — периодически чистим завершённые."""
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval)
                if self._r() is not None:
                    continue
                to_remove = [
                    op_id for op_id, p in list(self._mem.items())
                    if p.completed_at and (datetime.utcnow() - p.completed_at).total_seconds() > self.max_age
                ]
                for op_id in to_remove:
                    self._mem.pop(op_id, None)
                if to_remove:
                    logger.info(f"Cleaned up {len(to_remove)} old operations (in-memory)")
            except Exception as e:  # noqa: BLE001
                logger.error(f"Error in cleanup task: {e}")

    def start_cleanup_task(self):
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self.cleanup_old_operations())
            logger.info("Started progress tracker cleanup task")


# Глобальный экземпляр трекера
_progress_tracker: Optional[ProgressTracker] = None


def get_progress_tracker() -> ProgressTracker:
    """Получить глобальный экземпляр трекера"""
    global _progress_tracker
    if _progress_tracker is None:
        _progress_tracker = ProgressTracker()
        # Запускаем cleanup task
        try:
            _progress_tracker.start_cleanup_task()
        except RuntimeError:
            # Event loop еще не запущен, это нормально
            pass
    return _progress_tracker


def format_time_remaining(seconds: Optional[int]) -> str:
    """Форматирование оставшегося времени"""
    if seconds is None:
        return "Расчет..."
    
    if seconds < 60:
        return f"~{seconds} сек"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"~{minutes} мин"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"~{hours} ч {minutes} мин"

