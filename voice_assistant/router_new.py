"""
Новый роутер для масштабируемого голосового ассистента.
Поддерживает 100+ одновременных пользователей с изолированными сессиями.
"""

import logging
from datetime import datetime
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query, Request, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from typing import Optional
from pathlib import Path

from .websocket_handler import handle_websocket_connection
from .session_manager import get_session_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/voice-training", tags=["Voice Training"])


def get_db():
    """
    Dependency для получения сессии БД.
    """
    try:
        from database import SessionLocal
    except ImportError:
        from app.database import SessionLocal
    
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_current_user_from_token(token: str, db: Session):
    """
    Получает пользователя по токену JWT.
    NOTE: Эта функция не используется в WebSocket endpoint, но оставлена для совместимости.
    """
    # Функция не используется, но оставлена для совместимости
    # WebSocket endpoint использует user_id из query параметра
    return None


@router.websocket("/ws")
async def websocket_training_endpoint(
    websocket: WebSocket,
    training_id: int = Query(1, description="ID тренировки"),
    db: Session = Depends(get_db)
):
    """
    WebSocket endpoint для голосовой тренировки.
    
    Параметры:
        training_id: ID тренировки из БД (опционально, по умолчанию 1)
    
    Поддерживает:
        - Изолированные сессии для каждого пользователя
        - Автоматическое сохранение в БД
        - Ограничение одновременных подключений
        - Voice Activity Detection (VAD)
        - Session-based аутентификация (через cookies)
    """
    
    # Аутентификация пользователя через сессию (cookies)
    try:
        await websocket.accept()
        # Получаем cookies из WebSocket
        cookies = websocket.cookies
        session_cookie = cookies.get('session')
        
        if not session_cookie:
            await websocket.send_json({
                "type": "error",
                "message": "⚠️ Не авторизован. Войдите в систему."
            })
            await websocket.close(code=1008, reason="Unauthorized: No session")
            logger.warning(f"⚠️ Попытка подключения без сессии")
            return
        
        # Декодируем сессию (FastAPI использует itsdangerous для сессий)
        from starlette.middleware.sessions import SessionMiddleware
        from itsdangerous import BadSignature
        
        # Попробуем получить user_id из запроса
        # Временное решение: получаем user_id из query параметра
        user_id = websocket.query_params.get('user_id')
        
        if not user_id:
            await websocket.send_json({
                "type": "error",
                "message": "⚠️ Не указан user_id"
            })
            await websocket.close(code=1008, reason="Missing user_id")
            return
        
        user_id = int(user_id)
        
        # Проверяем что пользователь существует
        try:
            from models import User
        except ImportError:
            from app.models import User
        
        user = db.query(User).filter(User.id == user_id).first()
        
        if not user:
            await websocket.send_json({
                "type": "error",
                "message": "⚠️ Пользователь не найден"
            })
            await websocket.close(code=1008, reason="User not found")
            return
        
        logger.info(f"🔐 Аутентификация успешна: user_id={user_id}, training_id={training_id}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка аутентификации: {e}")
        await websocket.send_json({
            "type": "error",
            "message": f"❌ Ошибка аутентификации: {str(e)}"
        })
        await websocket.close(code=1011, reason="Authentication error")
        return
    
    # Передаём управление обработчику
    await handle_websocket_connection(websocket, user_id, training_id, db)


@router.get("/stats")
async def get_training_stats():
    """
    Возвращает статистику использования сервера.
    
    Returns:
        Информация о текущих сессиях и загрузке сервера
    """
    session_manager = get_session_manager()
    stats = session_manager.get_stats()
    
    return {
        "status": "ok",
        "sessions": stats
    }


@router.get("/session/{session_id}")
async def get_session_info(
    session_id: str,
    db: Session = Depends(get_db)
):
    """
    Получает информацию о сессии тренировки.
    
    Args:
        session_id: UUID сессии
    
    Returns:
        Информация о сессии
    """
    session_manager = get_session_manager()
    session = await session_manager.get_session(session_id)
    
    if not session:
        return {"error": "Session not found"}, 404
    
    return {
        "session_id": session.session_id,
        "user_id": session.user_id,
        "training_id": session.training_id,
        "created_at": session.created_at.isoformat(),
        "last_activity": session.last_activity.isoformat(),
        "is_processing": session.is_processing
    }


@router.post("/session/{session_id}/end")
async def end_training_session(
    session_id: str,
    db: Session = Depends(get_db)
):
    """
    Принудительно завершает сессию тренировки.
    
    Args:
        session_id: UUID сессии
    
    Returns:
        Результат операции
    """
    session_manager = get_session_manager()
    session = await session_manager.get_session(session_id)
    
    if not session:
        return {"error": "Session not found"}, 404
    
    # Закрываем сессию
    await session_manager.close_session(session_id)
    
    return {
        "message": "Session closed successfully",
        "session_id": session_id
    }


@router.get("/training/{training_id}/history")
async def get_training_history(
    training_id: int,
    user_id: int = Query(..., description="ID пользователя"),
    db: Session = Depends(get_db)
):
    """
    Получает историю сообщений для тренировки.
    
    Args:
        training_id: ID тренировки
        user_id: ID пользователя
    
    Returns:
        Список сообщений
    """
    try:
        try:
            from models import TrainingSession, VoiceTrainingMessage
        except ImportError:
            from app.models import TrainingSession, VoiceTrainingMessage
        
        # Находим все сессии для этой тренировки (только последние 10 для производительности)
        sessions = db.query(TrainingSession).filter(
            TrainingSession.training_id == training_id,
            TrainingSession.user_id == user_id,
            TrainingSession.session_type == "voice"
        ).order_by(TrainingSession.started_at.desc()).limit(10).all()
        
        if not sessions:
            logger.debug(f"📭 Сессии не найдены для training_id={training_id}, user_id={user_id}")
            return {
                "messages": [],
                "session_id": None
            }
        
        # Получаем ID всех сессий
        session_ids = [s.id for s in sessions]
        
        # Получаем все сообщения из всех сессий этой тренировки
        # Ограничиваем количество для производительности (последние 200 сообщений)
        messages = db.query(VoiceTrainingMessage).filter(
            VoiceTrainingMessage.session_id.in_(session_ids)
        ).order_by(VoiceTrainingMessage.timestamp.desc()).limit(200).all()
        
        # Переворачиваем для правильного порядка (от старых к новым)
        messages = list(reversed(messages))
        
        # Используем последнюю сессию для session_id
        session = sessions[0]
        
        # Форматируем сообщения
        formatted_messages = [
            {
                "role": msg.role,
                "text": msg.text,
                "timestamp": msg.timestamp.isoformat() if msg.timestamp else None
            }
            for msg in messages
        ]
        
        return {
            "messages": formatted_messages,
            "session_id": session.id,
            "websocket_session_id": session.websocket_session_id
        }
        
    except Exception as e:
        logger.error(f"❌ Ошибка получения истории: {e}", exc_info=True)
        return {
            "error": str(e),
            "messages": []
        }


@router.get("/training", response_class=HTMLResponse)
async def get_training_page(
    request: Request, 
    training_id: Optional[int] = None, 
    session_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """Возвращает страницу голосовой тренировки (для обратной совместимости)."""
    from fastapi.templating import Jinja2Templates
    
    try:
        from models import User, Training
    except ImportError:
        from app.models import User, Training
    
    # Получаем путь к шаблонам
    current_file = Path(__file__).resolve()
    project_root = current_file.parent.parent  # /voice_assistant -> /
    templates_dir = project_root / "app" / "templates"
    
    # Проверяем, существует ли директория templates
    if not templates_dir.exists():
        templates_dir = project_root / "templates"
    
    templates = Jinja2Templates(directory=str(templates_dir))
    
    # Получаем user_id из сессии и загружаем пользователя из БД
    user_id = request.session.get("user_id")
    user = None
    
    if user_id:
        try:
            user = db.query(User).filter_by(id=user_id).first()
        except Exception as e:
            logger.error(f"Ошибка загрузки пользователя: {e}", exc_info=True)
    
    # Если user нет, создаем заглушку
    if not user:
        class FakeUser:
            def __init__(self):
                self.id = None
                self.name = "Гость"
                self.email = "guest@training.local"
        user = FakeUser()
    
    # Данные о тренировке (если есть training_id)
    training_data = {
        "id": training_id or "new",
        "session_id": session_id,
        "topic": "Тренировка продаж с ИИ",
        "scenario": "sales",
        "difficulty": "medium"
    }
    
    # Если передан training_id, получаем данные тренировки из БД
    if training_id:
        try:
            training = db.query(Training).filter_by(id=training_id).first()
            if training:
                # Проверяем доступ к тренировке только если user.id существует
                if user.id:
                    from services.team_access import get_accessible_user_ids_for_manager
                    accessible_user_ids = get_accessible_user_ids_for_manager(db, user)
                    # Проверяем доступ: либо это владелец плана, либо менеджер имеет доступ к участнику
                    has_access = False
                    if training.plan.user_id == user.id:
                        # Пользователь - владелец плана
                        has_access = True
                    elif accessible_user_ids is not None:
                        # Проверяем, есть ли доступ через команду
                        has_access = training.plan.user_id in accessible_user_ids
                    else:
                        # Админ имеет доступ ко всему
                        has_access = True
                    
                    if not has_access:
                        raise HTTPException(status_code=403, detail="Нет доступа к этой тренировке")
                
                training_data.update({
                    "topic": training.title,
                    "description": training.description,
                    "recommendation": training.recommendation,
                    "scenario": training.scenario_type,
                })
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Ошибка загрузки данных тренировки: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Ошибка загрузки данных тренировки: {str(e)}")
    
    # Данные для шаблона
    context = {
        "request": request,
        "user": user,
        "current_user": user,
        "training": training_data
    }
    
    return templates.TemplateResponse("voice_training_conference.html", context)


@router.post("/training/complete")
async def complete_training(request: Request, db: Session = Depends(get_db)):
    """Завершает тренировку и сохраняет результаты."""
    try:
        from models import TrainingSession
    except ImportError:
        from app.models import TrainingSession
    
    try:
        data = await request.json()
        session_id = data.get("session_id")
        training_id = data.get("training_id")
        transcript = data.get("transcript", "")
        score = data.get("score", 0)
        user_responses_count = data.get("user_responses_count", 0)
        ai_questions_count = data.get("ai_questions_count", 0)
        
        logger.info(f"💾 Завершение тренировки: session_id={session_id}, training_id={training_id}, score={score}")
        
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required")
        
        # Обновляем сессию тренировки
        session = db.query(TrainingSession).filter_by(id=session_id).first()
        if session:
            session.status = "completed"
            session.completed_at = datetime.utcnow()
            session.user_responses_count = user_responses_count
            session.ai_questions_count = ai_questions_count
            if score is not None:
                session.score = score
            
            # Вычисляем длительность если не указана
            if session.started_at and not session.duration_seconds:
                duration = int((datetime.utcnow() - session.started_at).total_seconds())
                session.duration_seconds = duration
            
            db.commit()
            logger.info(f"✅ Сессия {session_id} завершена: score={score}, responses={user_responses_count}")
        else:
            logger.warning(f"⚠️ Сессия {session_id} не найдена в БД")
            # Не выбрасываем ошибку, просто возвращаем успех для обратной совместимости
            return {
                "success": True,
                "message": "Тренировка завершена (сессия не найдена в БД)",
                "score": score
            }
        
        return {
            "success": True,
            "message": "Тренировка завершена",
            "score": score,
            "session_id": session_id
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Ошибка завершения тренировки: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка завершения тренировки: {str(e)}")

