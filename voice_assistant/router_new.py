"""
Новый роутер для масштабируемого голосового ассистента.
Поддерживает 100+ одновременных пользователей с изолированными сессиями.
"""

import logging
from datetime import datetime
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
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
    db_session_id: Optional[int] = Query(None, description="ID существующей TrainingSession для реконнекта"),
):
    """
    WebSocket endpoint для голосовой тренировки.

    Параметры:
        training_id: ID тренировки из БД (опционально, по умолчанию 1)
        db_session_id: ID существующей TrainingSession — если передан и сессия активна,
                       при реконнекте мы продолжаем её, а не создаём новую.

    Поддерживает:
        - Изолированные сессии для каждого пользователя
        - Автоматическое сохранение в БД
        - Ограничение одновременных подключений
        - Voice Activity Detection (VAD)
        - Session-based аутентификация (через cookies)
        - Auto-reconnect: при том же db_session_id продолжаем сессию
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
        
        # Декодируем session cookie → извлекаем session_user_id (защита от подмены)
        import os, json as _json, base64 as _b64
        from itsdangerous import TimestampSigner
        secret_key = os.getenv("SECRET_KEY")
        if not secret_key:
            await websocket.send_json({"type": "error", "message": "⚠️ Сервер не настроен (SECRET_KEY)"})
            await websocket.close(code=1011, reason="Server misconfigured")
            return
        # Starlette SessionMiddleware подписывает cookie как
        # TimestampSigner(secret).sign(b64encode(json(session))) — читаем тем же способом.
        signer = TimestampSigner(str(secret_key))
        session_user_id: Optional[int] = None
        _session_max_age = int(os.getenv("SESSION_MAX_AGE", str(60 * 60 * 8)))
        try:
            data = signer.unsign(session_cookie, max_age=_session_max_age)
            payload = _json.loads(_b64.b64decode(data))
            session_user_id = payload.get("user_id") if isinstance(payload, dict) else None
        except Exception:
            pass  # Сессия не читается — пробуем query param только если разрешено

        if session_user_id is not None:
            user_id = session_user_id
        else:
            # query-param fallback — только если явно разрешён (dev/тест).
            # В продакшене ALLOW_QUERY_USER_ID оставить false (дефолт).
            _allow_query = os.getenv("ALLOW_QUERY_USER_ID", "false").lower() == "true"
            user_id_param = websocket.query_params.get('user_id')
            if _allow_query and user_id_param:
                user_id = int(user_id_param)
            else:
                await websocket.send_json({
                    "type": "error",
                    "message": "⚠️ Сессия недействительна или истекла. Войдите заново."
                })
                await websocket.close(code=1008, reason="Unauthorized")
                return

        # Проверяем пользователя и доступ в КОРОТКОЙ сессии БД, которая сразу
        # закрывается — соединение не держится на всю WebSocket-тренировку.
        try:
            from database import SessionLocal
        except ImportError:
            from app.database import SessionLocal
        try:
            from models import User
        except ImportError:
            from app.models import User
        try:
            from services.capability_service import has_capability
        except ImportError:
            from app.services.capability_service import has_capability

        with SessionLocal() as _auth_db:
            user = _auth_db.query(User).filter(User.id == user_id).first()
            if not user:
                await websocket.send_json({
                    "type": "error",
                    "message": "⚠️ Пользователь не найден"
                })
                await websocket.close(code=1008, reason="User not found")
                return
            if not has_capability(user, "voice_training"):
                await websocket.send_json({
                    "type": "error",
                    "message": "⚠️ Голосовые тренировки не входят в ваш тарифный план"
                })
                await websocket.close(code=1008, reason="voice_training capability required")
                return

        logger.info(f"🔐 Аутентификация успешна: user_id={user_id}, training_id={training_id}")
        
    except Exception as e:
        logger.error(f"❌ Ошибка аутентификации: {e}")
        logger.warning(f"WebSocket auth error: {e}")
        await websocket.send_json({
            "type": "error",
            "message": "❌ Ошибка аутентификации"
        })
        await websocket.close(code=1011, reason="Authentication error")
        return
    
    # Передаём управление обработчику (db_session_id для реконнекта).
    # БД обработчику НЕ передаём: он открывает короткие сессии через run_db.
    await handle_websocket_connection(websocket, user_id, training_id, db_session_id)


@router.get("/stats")
async def get_training_stats(request: Request, db: Session = Depends(get_db)):
    """
    Возвращает статистику использования сервера. Только для администратора —
    содержит инфраструктурные метрики (активные сессии, нагрузка).
    """
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Требуется аутентификация")
    try:
        from models import User
    except ImportError:
        from app.models import User
    user = db.query(User).filter_by(id=user_id).first()
    if not user or user.role != "admin":
        raise HTTPException(status_code=403, detail="Доступ только для администратора")

    session_manager = get_session_manager()
    stats = session_manager.get_stats()

    return {
        "status": "ok",
        "sessions": stats,
        # ВАЖНО: цифры — ПО ОДНОМУ воркеру. SessionManager — синглтон процесса,
        # поэтому при WEB_CONCURRENCY>1 суммарная ёмкость = max_sessions * число
        # воркеров, а лимит «одна сессия на юзера» действует в пределах воркера.
        "scope": "per-worker",
    }


@router.get("/session/{session_id}")
async def get_session_info(
    session_id: str,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Получает информацию о сессии тренировки (только своей).

    Args:
        session_id: UUID сессии

    Returns:
        Информация о сессии
    """
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Требуется аутентификация")

    session_manager = get_session_manager()
    session = await session_manager.get_session(session_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Нет доступа к этой сессии")

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
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Принудительно завершает сессию тренировки (только свою).

    Args:
        session_id: UUID сессии

    Returns:
        Результат операции
    """
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Требуется аутентификация")

    session_manager = get_session_manager()
    session = await session_manager.get_session(session_id)

    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    if session.user_id != user_id:
        raise HTTPException(status_code=403, detail="Нет доступа к этой сессии")

    # Закрываем сессию
    await session_manager.close_session(session_id)
    
    return {
        "message": "Session closed successfully",
        "session_id": session_id
    }


@router.get("/training/{training_id}/history")
async def get_training_history(
    training_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Получает историю сообщений для тренировки (только своей).

    Returns:
        Список сообщений
    """
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Требуется аутентификация")

    try:
        try:
            from models import TrainingSession, VoiceTrainingMessage
        except ImportError:
            from app.models import TrainingSession, VoiceTrainingMessage
        
        # Найти сессии, принадлежащие только аутентифицированному пользователю
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
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Ошибка получения истории: {e}", exc_info=True)
        return {
            "error": "Внутренняя ошибка сервера",
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
    try:
        from models import User, Training
    except ImportError:
        from app.models import User, Training

    # Переиспользуем настроенный в app.main экземпляр Jinja2Templates: на нём
    # зарегистрированы globals (resolve_locale, _, gettext, get_brand) и фильтры,
    # которые требует train/_layout.html. Свежий Jinja2Templates их не имеет.
    templates = getattr(request.app.state, "templates", None)
    if templates is None:
        from fastapi.templating import Jinja2Templates
        current_file = Path(__file__).resolve()
        project_root = current_file.parent.parent  # /voice_assistant -> /
        templates_dir = project_root / "app" / "templates"
        if not templates_dir.exists():
            templates_dir = project_root / "templates"
        templates = Jinja2Templates(directory=str(templates_dir))
    
    # Получаем user_id из сессии и загружаем пользователя из БД.
    # Страница тренировки доступна только авторизованным — иначе редирект на логин
    # (раньше создавалась FakeUser-заглушка, что открывало страницу анонимам).
    user_id = request.session.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login", status_code=302)

    user = None
    try:
        user = db.query(User).filter_by(id=user_id).first()
    except Exception as e:
        logger.error(f"Ошибка загрузки пользователя: {e}", exc_info=True)

    if not user:
        request.session.clear()
        return RedirectResponse(url="/login", status_code=302)
    
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
            raise HTTPException(status_code=500, detail="Ошибка загрузки данных тренировки")
    
    # Данные для шаблона
    try:
        from services.capability_service import has_capability
    except ImportError:
        from app.services.capability_service import has_capability

    is_full_user = has_capability(user, "call_analysis")
    post_training_url = "/calls" if is_full_user else "/dashboard"
    # Full users keep the full dashboard layout (with sidebar); train users get train layout
    layout_template = "_layout_dashboard.html" if is_full_user else "train/_layout.html"

    context = {
        "request": request,
        "user": user,
        "current_user": user,
        "training": training_data,
        "post_training_url": post_training_url,
        "layout_template": layout_template,
    }
    
    return templates.TemplateResponse("voice_training_conference.html", context)


@router.post("/training/complete")
async def complete_training(request: Request, db: Session = Depends(get_db)):
    """Завершает тренировку, прогоняет AI-валидатор и обновляет прогресс плана."""
    # Обязательная аутентификация — предотвращает запуск AI-валидатора чужих сессий
    session_user_id = request.session.get("user_id")
    if not session_user_id:
        raise HTTPException(status_code=401, detail="Требуется аутентификация")

    try:
        from models import TrainingSession, Training
    except ImportError:
        from app.models import TrainingSession, Training
    
    try:
        from services.training_validator_service import TrainingValidatorService, ValidationTransientError
    except ImportError:
        from app.services.training_validator_service import TrainingValidatorService, ValidationTransientError
    
    try:
        data = await request.json()
        session_id = data.get("session_id")
        training_id = data.get("training_id")
        transcript = data.get("transcript", "")
        user_responses_count = data.get("user_responses_count", 0)
        ai_questions_count = data.get("ai_questions_count", 0)
        
        logger.info("Completing training session", extra={"session_id": session_id, "training_id": training_id})
        
        if not session_id:
            raise HTTPException(status_code=400, detail="session_id is required")
        
        # with_for_update — пессимистичная блокировка против двойного завершения
        # (автозавершение по WebSocket + ручное нажатие кнопки одновременно)
        session = (
            db.query(TrainingSession)
            .filter_by(id=session_id)
            .with_for_update()
            .first()
        )
        if not session:
            logger.warning("Training session not found in DB", extra={"session_id": session_id})
            return {
                "success": True,
                "message": "Тренировка завершена (сессия не найдена в БД)",
                "score": 0
            }

        # Проверяем, что сессия принадлежит аутентифицированному пользователю
        if session.user_id != session_user_id:
            raise HTTPException(status_code=403, detail="Нет доступа к этой сессии")

        # Если сессия уже завершена и есть score — возвращаем сохранённый результат
        # (защита от двойного вызова при автозавершении + ручном нажатии кнопки)
        if session.status == "completed" and session.score is not None:
            logger.info("Session already completed, returning cached result", extra={"session_id": session_id, "score": session.score})
            training = db.query(Training).filter_by(id=int(training_id)).first() if training_id else None
            return {
                "success": True,
                "message": "Тренировка уже была проверена",
                "score": session.score,
                "passed": (session.score or 0) >= 70,
                "feedback": session.feedback or "",
                "criteria": {},
                "training_completed": (session.score or 0) >= 70,
                "plan_completed": training.plan.status == "completed" if training and training.plan else False,
                "session_id": session_id
            }

        session.user_responses_count = user_responses_count
        session.ai_questions_count = ai_questions_count

        # Если это тренировка из плана — запускаем AI-валидатор
        if training_id and str(training_id) != 'new':
            # Контекст читаем ПОД блокировкой и сохраняем плоские значения, затем
            # СНИМАЕМ блокировку перед LLM-вызовом — чтобы не держать соединение и
            # строку на время ≤180 c при «шторме завершений» (план, раздел D).
            training = db.query(Training).filter_by(id=int(training_id)).first()
            if not training:
                db.commit()  # фиксируем счётчики и снимаем блокировку
                return {
                    "success": True,
                    "message": "Тренировка завершена (тренировка не найдена)",
                    "score": 0,
                    "passed": False,
                    "session_id": session_id,
                }

            from .config import SYSTEM_PROMPT

            t_title = training.title
            t_desc = training.description
            t_rec = training.recommendation
            t_stage = training.stage

            # Для многоэтапных тренировок собираем промпты всех этапов
            effective_prompt = SYSTEM_PROMPT
            if t_stage:
                try:
                    try:
                        from services.training_stages_service import load_stages
                    except ImportError:
                        from app.services.training_stages_service import load_stages
                    stages = load_stages(t_stage)
                    if stages:
                        effective_prompt = "\n\n---\n\n".join(
                            f"=== ЭТАП {s.number} (роль ИИ: {s.ai_role}) ===\n{s.prompt}"
                            for s in stages
                        )
                except Exception as e:
                    logger.warning("Failed to load stages for validation", extra={"error": str(e)})

            db.commit()  # фиксируем счётчики и СНИМАЕМ блокировку строки перед LLM

            # Серверный транскрипт (нельзя подделать с клиента) — чтение без блокировки.
            # expected_messages — хинт для ретрая на гонке последней реплики (не оценка).
            effective_transcript = await TrainingValidatorService._resolve_transcript(
                db, session_id, transcript,
                expected_messages=(user_responses_count or 0) + (ai_questions_count or 0),
            )

            # --- LLM-вызов БЕЗ удержания блокировки/строки ---
            try:
                validation_result = await TrainingValidatorService.validate_training(
                    transcript=effective_transcript,
                    training_title=t_title,
                    training_description=t_desc,
                    training_recommendation=t_rec,
                    training_stage=t_stage,
                    system_prompt=effective_prompt,
                )
            except ValidationTransientError:
                # Временная недоступность OpenAI — сессия остаётся active, юзер повторит
                logger.warning("Validation transient — session left active", extra={"session_id": session_id, "training_id": training_id})
                return {
                    "success": False,
                    "message": "Сервис проверки временно недоступен. Попробуйте ещё раз.",
                    "score": 0,
                    "passed": False,
                    "feedback": "Сервис проверки временно недоступен. Попробуйте завершить тренировку ещё раз через несколько минут.",
                    "criteria": {},
                    "validation_error": True,
                    "session_id": session_id,
                }

            # --- Повторная блокировка + идемпотентная запись ---
            # Если параллельный racer уже записал результат — возвращаем сохранённое
            # и НЕ запускаем persist повторно (иначе двойной инкремент плана).
            locked = (
                db.query(TrainingSession)
                .filter_by(id=session_id)
                .with_for_update()
                .first()
            )
            if locked and locked.status == "completed" and locked.score is not None:
                training_row = db.query(Training).filter_by(id=int(training_id)).first()
                logger.info("Session completed by concurrent request, returning cached", extra={"session_id": session_id, "score": locked.score})
                return {
                    "success": True,
                    "message": "Тренировка уже была проверена",
                    "score": locked.score,
                    "passed": (locked.score or 0) >= 70,
                    "feedback": locked.feedback or "",
                    "criteria": {},
                    "training_completed": (locked.score or 0) >= 70,
                    "plan_completed": training_row.plan.status == "completed" if training_row and training_row.plan else False,
                    "session_id": session_id,
                }

            validation_result = TrainingValidatorService.persist_validation(
                db, session_id, int(training_id), validation_result
            )

            is_error = validation_result.get("validation_error", False)
            logger.info(
                "AI validation result",
                extra={
                    "score": validation_result["score"],
                    "passed": validation_result["passed"],
                    "training_id": training_id,
                    "validation_error": is_error,
                },
            )

            return {
                "success": not is_error,
                "message": (
                    "Сервис проверки временно недоступен. Попробуйте ещё раз."
                    if is_error
                    else "Тренировка проверена AI-валидатором"
                ),
                "score": validation_result["score"],
                "passed": validation_result["passed"],
                "feedback": validation_result.get("feedback", ""),
                "criteria": validation_result.get("criteria", {}),
                "details": validation_result.get("details", ""),
                "training_completed": validation_result.get("training_completed", False),
                "plan_completed": validation_result.get("plan_completed", False),
                "validation_error": is_error,
                "session_id": session_id,
            }

        # Обычная тренировка (не из плана) — просто сохраняем (блокировка короткая)
        session.status = "completed"
        session.completed_at = datetime.utcnow()
        if session.started_at and not session.duration_seconds:
            session.duration_seconds = int(
                (datetime.utcnow() - session.started_at).total_seconds()
            )
        db.commit()

        # Стрик пользователя — свободная тренировка тоже засчитывает день
        try:
            from models import User
            try:
                from services.streak_service import refresh_streak
            except ImportError:
                from app.services.streak_service import refresh_streak
            u = db.query(User).get(session.user_id)
            if u:
                refresh_streak(db, u)
        except Exception:
            logger.warning("refresh_streak failed (free training)", exc_info=True)

        logger.info(f"✅ Свободная тренировка завершена: session_id={session_id}")

        return {
            "success": True,
            "message": "Тренировка завершена",
            "score": 0,
            "passed": False,
            "session_id": session_id
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Ошибка завершения тренировки: {e}", exc_info=True)
        db.rollback()
        raise HTTPException(status_code=500, detail="Ошибка завершения тренировки")

