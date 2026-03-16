"""
Система отслеживания прогресса длительных операций
"""
import asyncio
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


class ProgressTracker:
    """Трекер прогресса операций"""
    
    def __init__(self):
        self.operations: Dict[str, ProgressInfo] = {}
        self.cleanup_interval = 3600  # Очистка каждый час
        self.max_age = 86400  # Хранить операции 24 часа
        self._cleanup_task: Optional[asyncio.Task] = None
    
    def create_operation(
        self,
        operation_id: str,
        total_stages: int = 5,
        title: str = "Обработка...",
        can_cancel: bool = False
    ) -> ProgressInfo:
        """Создать новую операцию"""
        progress = ProgressInfo(operation_id, total_stages, title)
        progress.can_cancel = can_cancel
        self.operations[operation_id] = progress
        
        logger.info(f"Created progress tracking for operation {operation_id}")
        return progress
    
    def get_operation(self, operation_id: str) -> Optional[ProgressInfo]:
        """Получить информацию об операции"""
        return self.operations.get(operation_id)
    
    def update_operation(
        self,
        operation_id: str,
        stage_number: int,
        stage_name: str,
        message: str
    ) -> Optional[ProgressInfo]:
        """Обновить прогресс операции"""
        progress = self.operations.get(operation_id)
        if progress:
            # Завершить предыдущий этап если это новый этап
            if stage_number > progress.current_stage:
                progress.complete_stage(progress.current_stage)
            
            progress.start_stage(stage_number, stage_name, message)
            logger.debug(f"Updated operation {operation_id}: stage {stage_number}, {message}")
        return progress
    
    def complete_operation(self, operation_id: str, message: str = "Готово!"):
        """Завершить операцию"""
        progress = self.operations.get(operation_id)
        if progress:
            progress.complete(message)
            logger.info(f"Completed operation {operation_id}")
    
    def fail_operation(self, operation_id: str, error_message: str):
        """Отметить операцию как неудачную"""
        progress = self.operations.get(operation_id)
        if progress:
            progress.fail(error_message)
            logger.error(f"Failed operation {operation_id}: {error_message}")
    
    def cancel_operation(self, operation_id: str) -> bool:
        """Отменить операцию"""
        progress = self.operations.get(operation_id)
        if progress and progress.can_cancel:
            progress.cancel()
            logger.info(f"Cancelled operation {operation_id}")
            return True
        return False
    
    def list_active_operations(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Получить список активных операций"""
        active = [
            p.to_dict() for p in self.operations.values()
            if p.status in (ProgressStatus.PENDING, ProgressStatus.IN_PROGRESS)
        ]
        return sorted(
            active,
            key=lambda x: x["started_at"],
            reverse=True
        )[:limit]
    
    async def cleanup_old_operations(self):
        """Очистка старых операций"""
        while True:
            try:
                await asyncio.sleep(self.cleanup_interval)
                
                current_time = time.time()
                to_remove = []
                
                for op_id, progress in self.operations.items():
                    # Удаляем завершенные операции старше max_age
                    if progress.completed_at:
                        age = (datetime.utcnow() - progress.completed_at).total_seconds()
                        if age > self.max_age:
                            to_remove.append(op_id)
                
                for op_id in to_remove:
                    del self.operations[op_id]
                    logger.debug(f"Cleaned up old operation {op_id}")
                
                if to_remove:
                    logger.info(f"Cleaned up {len(to_remove)} old operations")
                    
            except Exception as e:
                logger.error(f"Error in cleanup task: {e}")
    
    def start_cleanup_task(self):
        """Запустить фоновую задачу очистки"""
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

