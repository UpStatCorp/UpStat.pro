from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from database import get_db
from deps import require_user
from models import AnalysisTrainingPlan, Training, Message, Attachment, TrainingSession
from services.training_plan_service import TrainingPlanService
from services.team_access import get_accessible_user_ids_for_manager
from services.pii_redactor import redact_pii
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)
router = APIRouter()




@router.get("/training-plan/by-plan/{plan_id}")
async def get_training_plan_by_id(
    request: Request,
    plan_id: int,
    db: Session = Depends(get_db)
):
    """Redirect to training plan page by plan ID (used from CRM recordings)"""
    user = require_user(request, db)
    plan = db.query(AnalysisTrainingPlan).filter_by(id=plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="План тренировок не найден")
    from fastapi.responses import RedirectResponse
    return RedirectResponse(f"/training-plan/{plan.report_message_id}", status_code=302)


@router.get("/training-plan/{report_msg_id}")
async def get_training_plan(
    request: Request,
    report_msg_id: int,
    view_mode: int = 0,  # Параметр для режима просмотра (менеджер смотрит план участника)
    db: Session = Depends(get_db)
):
    """Показывает план тренировок для конкретного анализа"""
    user = require_user(request, db)
    
    # Получаем доступные user_id в зависимости от роли
    accessible_user_ids = get_accessible_user_ids_for_manager(db, user)
    
    # Проверяем есть ли уже план для этого анализа
    query = db.query(AnalysisTrainingPlan).filter_by(
        report_message_id=report_msg_id
    )
    
    # Фильтруем по доступным пользователям
    if accessible_user_ids is None:
        # Админ видит всё
        existing_plan = query.first()
    else:
        # Менеджер или обычный пользователь видит только свои планы
        existing_plan = query.filter(
            AnalysisTrainingPlan.user_id.in_(accessible_user_ids)
        ).first()
    
    # Определяем режим просмотра: если менеджер смотрит план участника команды
    is_viewer_mode = False
    if view_mode == 1 and existing_plan:
        # Если план принадлежит не текущему пользователю, но он в списке доступных (менеджер смотрит план участника)
        if existing_plan.user_id != user.id and (accessible_user_ids is None or existing_plan.user_id in accessible_user_ids):
            is_viewer_mode = True
    
    if not existing_plan:
        # Создаём новый план
        # Получаем текст анализа
        message = db.query(Message).filter_by(id=report_msg_id).first()
        if not message:
            raise HTTPException(status_code=404, detail="Анализ не найден")
        
        # Получаем диалог, чтобы проверить владельца
        from models import Conversation
        conversation = db.query(Conversation).filter_by(id=message.conversation_id).first()
        if not conversation:
            raise HTTPException(status_code=404, detail="Диалог не найден")
        
        # Проверяем доступ к сообщению (проверяем владельца диалога, а не автора сообщения)
        # Для бот-сообщений user_id может быть None, поэтому проверяем conversation.user_id
        if accessible_user_ids is not None and conversation.user_id not in accessible_user_ids:
            raise HTTPException(status_code=403, detail="Нет доступа к этому анализу")
        
        # Находим файл analysis_*.txt
        analysis_att = None
        for att in message.attachments:
            if att.file_name.startswith("analysis_"):
                analysis_att = att
                break
        
        if not analysis_att:
            raise HTTPException(status_code=404, detail="Файл анализа не найден")
        
        # Читаем содержимое
        analysis_path = Path("uploads") / analysis_att.storage_key
        if not analysis_path.exists():
            raise HTTPException(status_code=404, detail="Файл анализа не найден на диске")
        
        analysis_text = analysis_path.read_text(encoding="utf-8")
        
        # Создаём план для владельца диалога (участника), а не для текущего пользователя
        # Если менеджер создает тренировку для участника, план должен быть привязан к участнику
        plan_owner_id = conversation.user_id
        
        # Создаём план
        try:
            existing_plan = await TrainingPlanService.create_training_plan(
                db, plan_owner_id, report_msg_id, analysis_text
            )
        except Exception as e:
            logger.error(f"Ошибка создания плана: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"Не удалось создать план тренировок: {str(e)}")
    
    # Загружаем тренировки
    trainings = db.query(Training).filter_by(
        plan_id=existing_plan.id
    ).order_by(Training.order).all()
    
    return request.app.state.templates.TemplateResponse(
        "training_plan.html",
        {
            "request": request,
            "user": user,
            "plan": existing_plan,
            "trainings": trainings,
            "is_viewer_mode": is_viewer_mode  # Флаг режима просмотра (менеджер смотрит план участника)
        }
    )


@router.post("/training/{training_id}/start")
async def start_training(
    request: Request,
    training_id: int,
    db: Session = Depends(get_db)
):
    """Начинает тренировку (создаёт сессию)"""
    user = require_user(request, db)
    
    training = db.query(Training).filter_by(id=training_id).first()
    if not training:
        raise HTTPException(status_code=404, detail="Тренировка не найдена")
    
    # Проверяем доступ к тренировке
    accessible_user_ids = get_accessible_user_ids_for_manager(db, user)
    if accessible_user_ids is not None and training.plan.user_id not in accessible_user_ids:
        raise HTTPException(status_code=403, detail="Нет доступа к этой тренировке")
    
    if training.status == "locked":
        raise HTTPException(status_code=403, detail="Тренировка ещё не доступна")
    
    # Создаём сессию
    session = TrainingSession(
        training_id=training_id,
        user_id=user.id
    )
    db.add(session)
    
    # Обновляем статус
    training.status = "in_progress"
    training.attempts += 1
    training.last_attempt_at = datetime.utcnow()
    
    db.commit()
    db.refresh(session)
    
    # Перенаправляем на страницу тренировки
    return JSONResponse(content={
        "success": True,
        "redirect": f"/voice-training/training?training_id={training_id}&session_id={session.id}"
    })


@router.post("/training-session/{session_id}/complete")
async def complete_training_session(
    request: Request,
    session_id: int,
    db: Session = Depends(get_db)
):
    """Завершает сессию тренировки"""
    user = require_user(request, db)
    
    # Получаем данные из тела запроса
    data = await request.json()
    score = data.get("score", 0)
    transcript = data.get("transcript", "")
    feedback = data.get("feedback", "")
    
    session = db.query(TrainingSession).filter_by(id=session_id, user_id=user.id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Сессия не найдена")
    
    # Проверяем доступ к тренировке
    accessible_user_ids = get_accessible_user_ids_for_manager(db, user)
    if accessible_user_ids is not None and session.training.plan.user_id not in accessible_user_ids:
        raise HTTPException(status_code=403, detail="Нет доступа к этой тренировке")
    
    duration = int((datetime.utcnow() - session.started_at).total_seconds())
    
    session.completed_at = datetime.utcnow()
    session.duration_seconds = duration
    session.score = score
    session.transcript = redact_pii(transcript) if transcript else transcript
    session.feedback = feedback
    
    training = session.training
    
    # Обновляем лучший результат
    if training.best_score is None or score > training.best_score:
        training.best_score = score
    
    # Если score >= 70, считаем тренировку пройденной
    if score >= 70:
        training.status = "completed"
        training.completed_at = datetime.utcnow()
        
        # Обновляем счётчик в плане
        plan = training.plan
        plan.completed_trainings += 1
        
        # Разблокируем следующую тренировку
        TrainingPlanService.unlock_next_training(db, plan.id)
        
        # Проверяем завершён ли весь план
        if plan.completed_trainings >= plan.total_trainings:
            plan.status = "completed"
    else:
        # Если не прошли, возвращаем статус в available для повторной попытки
        training.status = "available"
    
    db.commit()
    
    return JSONResponse(content={
        "success": True,
        "score": score,
        "training_completed": training.status == "completed",
        "plan_completed": training.plan.status == "completed",
        "next_available": training.status == "completed"
    })


@router.get("/api/training/{training_id}")
async def get_training_info(
    request: Request,
    training_id: int,
    db: Session = Depends(get_db)
):
    """Получает информацию о тренировке (API endpoint для voice assistant)"""
    user = require_user(request, db)
    
    training = db.query(Training).filter_by(id=training_id).first()
    if not training:
        raise HTTPException(status_code=404, detail="Тренировка не найдена")
    
    # Проверяем что пользователь имеет доступ к этой тренировке
    accessible_user_ids = get_accessible_user_ids_for_manager(db, user)
    if accessible_user_ids is not None and training.plan.user_id not in accessible_user_ids:
        raise HTTPException(status_code=403, detail="Нет доступа к этой тренировке")
    
    return JSONResponse(content={
        "id": training.id,
        "title": training.title,
        "description": training.description,
        "recommendation": training.recommendation,
        "scenario_type": training.scenario_type,
        "status": training.status,
        "attempts": training.attempts,
        "best_score": training.best_score
    })


