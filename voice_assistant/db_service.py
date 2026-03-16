"""
Сервис для работы с базой данных для голосовых тренировок.
"""

import json
from datetime import datetime
from typing import Optional, List, Dict
from sqlalchemy.orm import Session
import logging

logger = logging.getLogger(__name__)


class VoiceTrainingDBService:
    """
    Сервис для сохранения и загрузки голосовых тренировок в БД.
    """
    
    @staticmethod
    async def create_training_session(
        db: Session,
        user_id: int,
        training_id: int,
        websocket_session_id: str
    ) -> int:
        """
        Создаёт новую сессию тренировки в БД.
        
        Args:
            db: Сессия БД
            user_id: ID пользователя
            training_id: ID тренировки
            websocket_session_id: UUID WebSocket сессии
            
        Returns:
            ID созданной сессии
        """
        try:
            # Импортируем здесь чтобы избежать циклических импортов
            try:
                from models import TrainingSession
            except ImportError:
                from app.models import TrainingSession
            
            session = TrainingSession(
                user_id=user_id,
                training_id=training_id,
                websocket_session_id=websocket_session_id,
                session_type="voice",
                status="active",
                started_at=datetime.utcnow()
            )
            
            db.add(session)
            db.commit()
            db.refresh(session)
            
            logger.info(f"✅ Создана DB сессия #{session.id} для user_id={user_id}, training_id={training_id}")
            return session.id
            
        except Exception as e:
            logger.error(f"❌ Ошибка создания сессии в БД: {e}")
            db.rollback()
            raise
    
    @staticmethod
    async def save_voice_message(
        db: Session,
        session_id: int,
        role: str,
        text: str,
        audio_path: Optional[str] = None,
        duration_seconds: Optional[float] = None
    ):
        """
        Сохраняет отдельное голосовое сообщение в БД.
        Оптимизировано для быстрого выполнения.
        
        Args:
            db: Сессия БД
            session_id: ID сессии тренировки
            role: Роль (user или assistant)
            text: Текст сообщения
            audio_path: Путь к аудио файлу (опционально)
            duration_seconds: Длительность аудио
        """
        try:
            try:
                from models import VoiceTrainingMessage
            except ImportError:
                from app.models import VoiceTrainingMessage
            
            message = VoiceTrainingMessage(
                session_id=session_id,
                role=role,
                text=text,
                audio_path=audio_path,
                duration_seconds=duration_seconds,
                timestamp=datetime.utcnow()
            )
            
            db.add(message)
            db.commit()
            logger.debug(f"💾 Сохранено сообщение для session_id={session_id}, role={role}")
            
        except Exception as e:
            logger.error(f"❌ Ошибка сохранения сообщения: {e}")
            db.rollback()
    
    @staticmethod
    async def update_conversation_history(
        db: Session,
        session_id: int,
        conversation_history: List[Dict]
    ):
        """
        Обновляет историю диалога с GPT в БД.
        Оптимизировано для быстрого выполнения.
        
        Args:
            db: Сессия БД
            session_id: ID сессии тренировки
            conversation_history: История диалога (список сообщений GPT)
        """
        try:
            try:
                from models import TrainingSession
            except ImportError:
                from app.models import TrainingSession
            
            session = db.query(TrainingSession).filter(TrainingSession.id == session_id).first()
            if session:
                session.conversation_history_json = json.dumps(conversation_history, ensure_ascii=False)
                db.commit()
                logger.debug(f"💾 Обновлена история диалога для session_id={session_id}")
            else:
                logger.warning(f"⚠️ Сессия {session_id} не найдена")
                
        except Exception as e:
            logger.error(f"❌ Ошибка обновления истории: {e}")
            db.rollback()
    
    @staticmethod
    async def complete_training_session(
        db: Session,
        session_id: int,
        duration_seconds: int,
        user_responses_count: int,
        ai_questions_count: int,
        score: Optional[int] = None,
        feedback: Optional[str] = None
    ):
        """
        Завершает сессию тренировки.
        
        Args:
            db: Сессия БД
            session_id: ID сессии
            duration_seconds: Длительность тренировки
            user_responses_count: Количество ответов пользователя
            ai_questions_count: Количество вопросов ИИ
            score: Оценка (опционально)
            feedback: Обратная связь (опционально)
        """
        try:
            try:
                from models import TrainingSession
            except ImportError:
                from app.models import TrainingSession
            
            session = db.query(TrainingSession).filter(TrainingSession.id == session_id).first()
            if session:
                session.status = "completed"
                session.completed_at = datetime.utcnow()
                session.duration_seconds = duration_seconds
                session.user_responses_count = user_responses_count
                session.ai_questions_count = ai_questions_count
                
                if score is not None:
                    session.score = score
                if feedback:
                    session.feedback = feedback
                
                db.commit()
                logger.info(f"✅ Сессия {session_id} завершена: {duration_seconds}s, {user_responses_count} ответов")
            else:
                logger.warning(f"⚠️ Сессия {session_id} не найдена")
                
        except Exception as e:
            logger.error(f"❌ Ошибка завершения сессии: {e}")
            db.rollback()
    
    @staticmethod
    async def abort_training_session(db: Session, session_id: int):
        """
        Прерывает сессию тренировки (пользователь вышел до завершения).
        
        Args:
            db: Сессия БД
            session_id: ID сессии
        """
        try:
            try:
                from models import TrainingSession
            except ImportError:
                from app.models import TrainingSession
            
            session = db.query(TrainingSession).filter(TrainingSession.id == session_id).first()
            if session:
                session.status = "aborted"
                session.completed_at = datetime.utcnow()
                
                if session.started_at:
                    duration = (datetime.utcnow() - session.started_at).total_seconds()
                    session.duration_seconds = int(duration)
                
                db.commit()
                logger.info(f"⚠️ Сессия {session_id} прервана")
            else:
                logger.warning(f"⚠️ Сессия {session_id} не найдена")
                
        except Exception as e:
            logger.error(f"❌ Ошибка прерывания сессии: {e}")
            db.rollback()
    
    @staticmethod
    async def get_user_training_sessions(
        db: Session,
        user_id: int,
        training_id: Optional[int] = None,
        limit: int = 10
    ) -> List:
        """
        Получает список сессий тренировок пользователя.
        
        Args:
            db: Сессия БД
            user_id: ID пользователя
            training_id: Фильтр по ID тренировки (опционально)
            limit: Максимальное количество записей
            
        Returns:
            Список сессий
        """
        try:
            try:
                from models import TrainingSession
            except ImportError:
                from app.models import TrainingSession
            
            query = db.query(TrainingSession).filter(TrainingSession.user_id == user_id)
            
            if training_id:
                query = query.filter(TrainingSession.training_id == training_id)
            
            sessions = query.order_by(TrainingSession.started_at.desc()).limit(limit).all()
            
            return sessions
            
        except Exception as e:
            logger.error(f"❌ Ошибка получения сессий: {e}")
            return []
    
    @staticmethod
    async def get_session_messages(db: Session, session_id: int) -> List:
        """
        Получает все сообщения сессии.
        
        Args:
            db: Сессия БД
            session_id: ID сессии
            
        Returns:
            Список сообщений
        """
        try:
            try:
                from models import VoiceTrainingMessage
            except ImportError:
                from app.models import VoiceTrainingMessage
            
            messages = db.query(VoiceTrainingMessage).filter(
                VoiceTrainingMessage.session_id == session_id
            ).order_by(VoiceTrainingMessage.timestamp.asc()).all()
            
            return messages
            
        except Exception as e:
            logger.error(f"❌ Ошибка получения сообщений: {e}")
            return []

