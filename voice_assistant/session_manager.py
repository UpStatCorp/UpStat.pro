"""
Менеджер сессий для изоляции голосовых тренировок пользователей.
Обеспечивает масштабируемость до 100+ одновременных пользователей.
"""

import asyncio
import uuid
from datetime import datetime
from typing import Optional, Dict
from concurrent.futures import ThreadPoolExecutor
import logging

# Импорты компонентов только если не используется Azure Voice Live
# При использовании Azure Voice Live эти компоненты не нужны
try:
    from .config import USE_AZURE_VOICE_LIVE
    if not USE_AZURE_VOICE_LIVE:
        from .vad import VAD
        from .stt_reactive import STTEngine
        from .gpt_logic import GPTDialogue
        from .tts_response import TTSEngine
    else:
        # Заглушки для Azure Voice Live режима
        VAD = None
        STTEngine = None
        GPTDialogue = None
        TTSEngine = None
except ImportError:
    # Если config не найден, импортируем все (для обратной совместимости)
    from .vad import VAD
    from .stt_reactive import STTEngine
    from .gpt_logic import GPTDialogue
    from .tts_response import TTSEngine

logger = logging.getLogger(__name__)


class UserSession:
    """
    Изолированная сессия одного пользователя с собственными компонентами.
    """
    
    def __init__(self, user_id: int, training_id: Optional[int] = None, db_session_id: Optional[int] = None):
        """
        Создаёт новую изолированную сессию пользователя.
        
        Args:
            user_id: ID пользователя из БД
            training_id: ID тренировки (опционально)
            db_session_id: ID сессии тренировки в БД (опционально)
        """
        self.session_id = str(uuid.uuid4())
        self.user_id = user_id
        self.training_id = training_id
        self.db_session_id = db_session_id
        
        # Создаём ОТДЕЛЬНЫЕ экземпляры компонентов для этого пользователя
        # Только если не используется Azure Voice Live (там свои компоненты)
        from .config import USE_AZURE_VOICE_LIVE
        if USE_AZURE_VOICE_LIVE:
            # При использовании Azure Voice Live эти компоненты не нужны
            self.vad = None
            self.stt = None
            self.gpt = None
            self.tts = None
        else:
            # Используем локальные компоненты
            self.vad = VAD() if VAD else None
            self.stt = STTEngine() if STTEngine else None
            self.gpt = GPTDialogue() if GPTDialogue else None  # Отдельная история диалога!
            self.tts = TTSEngine() if TTSEngine else None
        
        # Состояние сессии
        self.created_at = datetime.utcnow()
        self.last_activity = datetime.utcnow()
        self.is_processing = False
        self.websocket = None
        
        # Аудио буфер
        self.current_audio = []
        self.is_speaking = False
        self.silence_start = None
        
        # Многоэтапная тренировка (если применимо).
        # stages: список объектов TrainingStage из training_stages_service.
        # current_stage_index: индекс активного этапа в stages (0-based).
        # is_switching_stage: защита от повторного переключения, пока ИИ
        #   ещё доигрывает прощальную фразу прошлого этапа.
        self.stages = []
        self.current_stage_index = 0
        self.is_switching_stage = False
        
        logger.info(f"✨ Создана новая сессия: {self.session_id} для user_id={user_id}")
    
    def update_activity(self):
        """Обновляет время последней активности"""
        self.last_activity = datetime.utcnow()
    
    def get_conversation_history(self) -> list:
        """Получает историю диалога GPT"""
        if self.gpt:
            return self.gpt.get_history()
        return []
    
    def clear_conversation(self):
        """Очищает историю диалога"""
        if self.gpt:
            self.gpt.clear_history()
            logger.info(f"🗑️ Очищена история диалога для сессии {self.session_id}")
    
    def cleanup(self):
        """Очистка ресурсов при закрытии сессии"""
        logger.info(f"🧹 Очистка сессии {self.session_id}")
        self.current_audio.clear()
        # Дополнительная очистка при необходимости


class SessionManager:
    """
    Глобальный менеджер сессий для управления изолированными пользовательскими сессиями.
    Обеспечивает масштабируемость и ограничения ресурсов.
    """
    
    def __init__(self, max_concurrent_sessions: int = 100, max_workers: int = 10):
        """
        Инициализирует менеджер сессий.
        
        Args:
            max_concurrent_sessions: Максимальное количество одновременных сессий
            max_workers: Количество воркеров для пула обработки STT
        """
        self.max_concurrent_sessions = max_concurrent_sessions
        self.sessions: Dict[str, UserSession] = {}  # session_id -> UserSession
        self.user_sessions: Dict[int, str] = {}  # user_id -> session_id (один пользователь = одна активная сессия)
        
        # Пул воркеров для блокирующих операций (STT)
        self.stt_executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="STT")
        
        # Блокировка для потокобезопасности
        self._lock = asyncio.Lock()
        
        logger.info(f"🚀 SessionManager инициализирован: max_sessions={max_concurrent_sessions}, max_workers={max_workers}")
    
    async def create_session(
        self, 
        user_id: int, 
        training_id: Optional[int] = None,
        db_session_id: Optional[int] = None
    ) -> Optional[UserSession]:
        """
        Создаёт новую сессию для пользователя.
        
        Args:
            user_id: ID пользователя
            training_id: ID тренировки (опционально)
            db_session_id: ID сессии в БД (опционально)
            
        Returns:
            UserSession или None если достигнут лимит
        """
        async with self._lock:
            # Проверяем лимит
            if len(self.sessions) >= self.max_concurrent_sessions:
                logger.warning(f"⚠️ Достигнут лимит сессий ({self.max_concurrent_sessions})")
                return None
            
            # Закрываем существующую сессию пользователя если есть
            if user_id in self.user_sessions:
                old_session_id = self.user_sessions[user_id]
                logger.info(f"🔄 Закрываем старую сессию {old_session_id} для user_id={user_id}")
                await self.close_session(old_session_id)
            
            # Создаём новую сессию
            session = UserSession(user_id, training_id, db_session_id)
            self.sessions[session.session_id] = session
            self.user_sessions[user_id] = session.session_id
            
            logger.info(f"✅ Создана сессия {session.session_id} (всего: {len(self.sessions)})")
            return session
    
    async def get_session(self, session_id: str) -> Optional[UserSession]:
        """
        Получает сессию по ID.
        
        Args:
            session_id: ID сессии
            
        Returns:
            UserSession или None если не найдена
        """
        session = self.sessions.get(session_id)
        if session:
            session.update_activity()
        return session
    
    async def get_user_session(self, user_id: int) -> Optional[UserSession]:
        """
        Получает активную сессию пользователя.
        
        Args:
            user_id: ID пользователя
            
        Returns:
            UserSession или None
        """
        session_id = self.user_sessions.get(user_id)
        if session_id:
            return await self.get_session(session_id)
        return None
    
    async def close_session(self, session_id: str):
        """
        Закрывает и удаляет сессию.
        
        Args:
            session_id: ID сессии
        """
        async with self._lock:
            session = self.sessions.get(session_id)
            if session:
                # Очищаем ресурсы
                session.cleanup()
                
                # Удаляем из маппингов
                if session.user_id in self.user_sessions:
                    del self.user_sessions[session.user_id]
                
                del self.sessions[session_id]
                
                logger.info(f"🗑️ Сессия {session_id} закрыта (осталось: {len(self.sessions)})")
    
    async def cleanup_inactive_sessions(self, timeout_seconds: int = 3600):
        """
        Очищает неактивные сессии (например, > 1 часа бездействия).
        
        Args:
            timeout_seconds: Таймаут бездействия в секундах
        """
        now = datetime.utcnow()
        to_close = []
        
        for session_id, session in self.sessions.items():
            inactive_time = (now - session.last_activity).total_seconds()
            if inactive_time > timeout_seconds:
                to_close.append(session_id)
        
        for session_id in to_close:
            logger.info(f"⏰ Закрываем неактивную сессию {session_id}")
            await self.close_session(session_id)
    
    def get_stats(self) -> dict:
        """
        Возвращает статистику менеджера сессий.
        
        Returns:
            Словарь со статистикой
        """
        return {
            "total_sessions": len(self.sessions),
            "max_sessions": self.max_concurrent_sessions,
            "capacity_percent": int((len(self.sessions) / self.max_concurrent_sessions) * 100),
            "stt_workers": self.stt_executor._max_workers,
            "active_users": len(self.user_sessions)
        }
    
    async def shutdown(self):
        """Завершает работу менеджера и закрывает все сессии"""
        logger.info("🛑 Завершение работы SessionManager...")
        
        # Закрываем все сессии
        session_ids = list(self.sessions.keys())
        for session_id in session_ids:
            await self.close_session(session_id)
        
        # Останавливаем пул воркеров
        self.stt_executor.shutdown(wait=True)
        
        logger.info("✅ SessionManager остановлен")


# Глобальный экземпляр менеджера сессий
_session_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """
    Получает глобальный экземпляр менеджера сессий (синглтон).
    
    Returns:
        SessionManager
    """
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager(
            max_concurrent_sessions=100,  # Настраивается
            max_workers=10  # Настраивается
        )
    return _session_manager

