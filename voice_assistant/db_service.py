"""
Сервис для работы с базой данных для голосовых тренировок.

Важно для масштабирования (100+ одновременных сессий):
- Методы — ПЛАИН-СИНХРОННЫЕ функции, принимающие свою сессию `db`.
- Вызывать их из async-кода нужно через `run_db(...)`, который открывает
  короткоживущую сессию на каждую операцию и выполняет её в выделенном
  пуле потоков. Так соединение БД не «прибивается» к WebSocket на всю
  тренировку и не блокирует event loop, а одна Session не шарится между
  параллельными задачами.
"""

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional, List, Dict
from sqlalchemy.orm import Session
import logging

logger = logging.getLogger(__name__)

# Выделенный ОГРАНИЧЕННЫЙ пул потоков для записей в БД из голосового пути.
# Не используем дефолтный пул asyncio.to_thread (min(32, cpu+4), общий на весь
# процесс) — иначе под нагрузкой запись в БД конкурирует со всеми остальными
# to_thread/run_in_executor вызовами и бутылочное горлышко просто переезжает.
# Размер согласуется с бюджетом соединений пула (см. DB_WRITE_WORKERS / pool_size).
_DB_WRITE_WORKERS = int(os.getenv("DB_WRITE_WORKERS", "16"))
_DB_EXECUTOR = ThreadPoolExecutor(
    max_workers=_DB_WRITE_WORKERS, thread_name_prefix="voice-db"
)


def _get_session_local():
    """Резолвим SessionLocal независимо от способа запуска (root / app.*)."""
    try:
        from database import SessionLocal
    except ImportError:
        from app.database import SessionLocal
    return SessionLocal


async def run_db(fn, *args, **kwargs):
    """Выполняет синхронную единицу работы с БД в собственной короткоживущей
    сессии, вне event loop (в выделенном пуле потоков).

    `fn` получает первым аргументом свежую `Session`, затем *args/**kwargs.
    Соединение возвращается в пул сразу после операции (миллисекунды), а не
    держится на всю WebSocket-сессию.
    """
    SessionLocal = _get_session_local()

    def _run():
        with SessionLocal() as s:
            return fn(s, *args, **kwargs)

    return await asyncio.get_running_loop().run_in_executor(_DB_EXECUTOR, _run)


class VoiceTrainingDBService:
    """
    Сервис для сохранения и загрузки голосовых тренировок в БД.

    Все методы — синхронные и принимают `db: Session`. Из async-кода вызывать
    через `run_db(VoiceTrainingDBService.<method>, ...)`.
    """

    @staticmethod
    def create_training_session(
        db: Session,
        user_id: int,
        training_id: int,
        websocket_session_id: str
    ) -> int:
        """
        Создаёт новую сессию тренировки в БД.

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

            logger.info("DB training session created", extra={"session_id": session.id, "user_id": user_id, "training_id": training_id})
            return session.id

        except Exception as e:
            logger.error("Failed to create training session in DB", extra={"error": str(e)}, exc_info=True)
            db.rollback()
            raise

    @staticmethod
    def save_voice_message(
        db: Session,
        session_id: int,
        role: str,
        text: str,
        audio_path: Optional[str] = None,
        duration_seconds: Optional[float] = None
    ):
        """
        Сохраняет отдельное голосовое сообщение в БД.
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
            logger.debug("Voice message saved", extra={"session_id": session_id, "role": role})

        except Exception as e:
            logger.error("Failed to save voice message", extra={"error": str(e)}, exc_info=True)
            db.rollback()

    @staticmethod
    def update_conversation_history(
        db: Session,
        session_id: int,
        conversation_history: List[Dict]
    ):
        """
        Обновляет историю диалога с GPT в БД.
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
                logger.debug("Conversation history updated", extra={"session_id": session_id})
            else:
                logger.warning("Training session not found", extra={"session_id": session_id})

        except Exception as e:
            logger.error("Failed to update conversation history", extra={"error": str(e)}, exc_info=True)
            db.rollback()

    @staticmethod
    def build_transcript(db: Session, session_id: int) -> str:
        """
        Собирает транскрипт диалога из СОХРАНЁННЫХ сообщений сессии.

        Это серверный источник правды для AI-валидатора — в отличие от текста,
        собранного в браузере, его нельзя подделать. Текст уже PII-редактирован
        на этапе сохранения. Возвращает полную строку без обрезки (усечение —
        ответственность валидатора).
        """
        try:
            try:
                from models import VoiceTrainingMessage
            except ImportError:
                from app.models import VoiceTrainingMessage

            rows = (
                db.query(VoiceTrainingMessage)
                .filter(VoiceTrainingMessage.session_id == session_id)
                .order_by(VoiceTrainingMessage.timestamp.asc())
                .all()
            )

            lines: List[str] = []
            for m in rows:
                text = (m.text or "").strip()
                if not text:
                    continue
                speaker = "Менеджер" if m.role == "user" else "Тренер"
                lines.append(f"{speaker}: {text}")

            return "\n".join(lines)

        except Exception as e:
            logger.error("Failed to build transcript", extra={"error": str(e), "session_id": session_id}, exc_info=True)
            return ""

    @staticmethod
    def complete_training_session(
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
                logger.info("Training session completed", extra={"session_id": session_id, "duration_s": duration_seconds, "responses": user_responses_count})
            else:
                logger.warning("Training session not found for completion", extra={"session_id": session_id})

        except Exception as e:
            logger.error("Failed to complete training session", extra={"error": str(e)}, exc_info=True)
            db.rollback()

    @staticmethod
    def abort_training_session(db: Session, session_id: int):
        """
        Прерывает сессию тренировки (пользователь вышел до завершения).

        ВАЖНО: прерываем ТОЛЬКО ещё не завершённые сессии. Иначе обычный разрыв
        WebSocket уже ПОСЛЕ успешного завершения (через HTTP /training/complete)
        перетёр бы статус "completed" → "aborted" и пользователь терял бы зачёт
        дня в стрике (стрик считает только status == "completed").
        """
        try:
            try:
                from models import TrainingSession
            except ImportError:
                from app.models import TrainingSession

            session = db.query(TrainingSession).filter(TrainingSession.id == session_id).first()
            if not session:
                logger.warning("Training session not found for abort", extra={"session_id": session_id})
                return

            if session.status not in ("active", "in_progress"):
                logger.info(
                    "Skip abort — session already finalized",
                    extra={"session_id": session_id, "status": session.status},
                )
                return

            session.status = "aborted"
            session.completed_at = datetime.utcnow()
            if session.started_at:
                duration = (datetime.utcnow() - session.started_at).total_seconds()
                session.duration_seconds = int(duration)

            db.commit()
            logger.info("Training session aborted", extra={"session_id": session_id})

        except Exception as e:
            logger.error("Failed to abort training session", extra={"error": str(e)}, exc_info=True)
            db.rollback()

    @staticmethod
    def get_user_training_sessions(
        db: Session,
        user_id: int,
        training_id: Optional[int] = None,
        limit: int = 10
    ) -> List:
        """
        Получает список сессий тренировок пользователя.
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
            logger.error("Failed to fetch user training sessions", extra={"error": str(e)}, exc_info=True)
            return []

    @staticmethod
    def get_session_messages(db: Session, session_id: int) -> List:
        """
        Получает все сообщения сессии.
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
            logger.error("Failed to fetch session messages", extra={"error": str(e)}, exc_info=True)
            return []
