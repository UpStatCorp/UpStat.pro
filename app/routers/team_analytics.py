"""Роутер для аналитики команды"""
from fastapi import APIRouter, Depends, Request, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session
from database import get_db
from deps import require_user
from models import Team, TeamMember, TrainingErrorCorrection
from services.team_access import assert_can_manage_team, get_accessible_user_ids_for_manager
from services.analytics_service import AnalyticsService
from datetime import datetime, timedelta
from typing import Optional

router = APIRouter(tags=["team_analytics"])


@router.get("/teams/{team_id}/analytics", response_class=HTMLResponse)
def team_analytics_page(
    team_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Страница аналитики команды для менеджера"""
    current_user = require_user(request, db)
    
    # Проверяем права доступа
    team = assert_can_manage_team(db, current_user, team_id)
    
    # Получаем аналитику команды
    analytics = AnalyticsService.get_team_analytics(db, team_id)
    
    return request.app.state.templates.TemplateResponse(
        "team_analytics.html",
        {
            "request": request,
            "user": current_user,
            "team": team,
            "analytics": analytics
        }
    )


@router.get("/teams/{team_id}/member/{member_id}/report", response_class=HTMLResponse)
def member_report_page(
    team_id: int,
    member_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Детальный отчет по участнику команды"""
    current_user = require_user(request, db)
    
    # Проверяем права доступа
    team = assert_can_manage_team(db, current_user, team_id)
    
    # Проверяем, что участник действительно в команде
    from sqlalchemy.orm import joinedload
    member = db.query(TeamMember).options(joinedload(TeamMember.user)).filter(
        TeamMember.team_id == team_id,
        TeamMember.user_id == member_id
    ).first()
    
    if not member:
        raise HTTPException(status_code=404, detail="Участник не найден в команде")
    
    # Получаем аналитику участника
    member_analytics = AnalyticsService.get_member_analytics(db, member_id, team_id)
    
    # Убеждаемся, что user объект загружен с last_login_at
    if member_analytics and member_analytics.get('user'):
        # Обновляем user объект из member, чтобы получить актуальные данные
        member_analytics['user'] = member.user
    
    # Получаем планы тренировок участника в формате для карточек (как в dashboard)
    from models import AnalysisTrainingPlan, Training
    
    training_plans = (
        db.query(AnalysisTrainingPlan)
        .filter(AnalysisTrainingPlan.user_id == member_id)
        .order_by(AnalysisTrainingPlan.created_at.desc())
        .limit(20)
        .all()
    )
    
    # Добавляем информацию о тренировках к каждому плану
    training_plans_data = []
    for plan in training_plans:
        # Получаем тренировки плана
        trainings = (
            db.query(Training)
            .filter(Training.plan_id == plan.id)
            .order_by(Training.order)
            .all()
        )
        
        # Находим текущую доступную тренировку
        current_training = None
        for t in trainings:
            if t.status in ('available', 'in_progress'):
                current_training = t
                break
        
        training_plans_data.append({
            'plan': plan,
            'trainings': trainings,
            'current_training': current_training,
            'progress_percent': int((plan.completed_trainings / plan.total_trainings * 100)) if plan.total_trainings > 0 else 0
        })
    
    return request.app.state.templates.TemplateResponse(
        "member_report.html",
        {
            "request": request,
            "user": current_user,
            "team": team,
            "member": member,
            "analytics": member_analytics,
            "training_plans": training_plans_data
        }
    )


@router.get("/api/teams/{team_id}/conversion-metrics")
def get_conversion_metrics(
    team_id: int,
    request: Request,
    period: str = Query("weekly", description="daily, weekly, monthly"),
    days: int = Query(30, description="Количество дней для анализа"),
    member_id: Optional[int] = Query(None, description="ID участника для фильтрации"),
    db: Session = Depends(get_db)
):
    """API для получения метрик конверсии"""
    current_user = require_user(request, db)
    
    # Проверяем права доступа
    team = assert_can_manage_team(db, current_user, team_id)
    
    # Рассчитываем период
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)
    
    # Получаем участников команды
    query = db.query(TeamMember).filter(TeamMember.team_id == team_id)
    
    # Если указан member_id, фильтруем по нему
    if member_id:
        query = query.filter(TeamMember.user_id == member_id)
    
    members = query.all()
    
    metrics = []
    for member in members:
        conversions = AnalyticsService.calculate_conversion_rates(
            db, member.user_id, start_date, end_date
        )
        metrics.append({
            "user_id": member.user_id,
            "user_name": member.user.name,
            "conversions": conversions
        })
    
    return JSONResponse(content={
        "success": True,
        "period": period,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "metrics": metrics
    })


@router.get("/api/teams/{team_id}/errors-corrections")
def get_errors_corrections(
    team_id: int,
    request: Request,
    member_id: Optional[int] = Query(None, description="ID участника для фильтрации"),
    db: Session = Depends(get_db)
):
    """API для получения ошибок и коррекций"""
    current_user = require_user(request, db)
    
    # Проверяем права доступа
    team = assert_can_manage_team(db, current_user, team_id)
    
    query = db.query(TrainingErrorCorrection).filter(
        TrainingErrorCorrection.team_id == team_id
    )
    
    if member_id:
        query = query.filter(TrainingErrorCorrection.user_id == member_id)
    
    errors = query.order_by(TrainingErrorCorrection.detected_at.desc()).limit(100).all()
    
    return JSONResponse(content={
        "success": True,
        "errors": [
            {
                "id": e.id,
                "user_id": e.user_id,
                "user_name": e.user.name,
                "error_type": e.error_type,
                "error_description": e.error_description,
                "correction_text": e.correction_text,
                "correction_applied": e.correction_applied,
                "error_severity": e.error_severity,
                "detected_at": e.detected_at.isoformat(),
                "conversation_id": e.conversation_id
            }
            for e in errors
        ]
    })


@router.post("/api/teams/{team_id}/errors/{error_id}/mark-applied")
def mark_correction_applied(
    team_id: int,
    error_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Отмечает коррекцию как примененную"""
    current_user = require_user(request, db)
    
    # Проверяем права доступа
    team = assert_can_manage_team(db, current_user, team_id)
    
    error = db.query(TrainingErrorCorrection).filter(
        TrainingErrorCorrection.id == error_id,
        TrainingErrorCorrection.team_id == team_id
    ).first()
    
    if not error:
        raise HTTPException(status_code=404, detail="Ошибка не найдена")
    
    error.correction_applied = True
    error.correction_applied_at = datetime.utcnow()
    db.commit()
    
    return JSONResponse(content={
        "success": True,
        "message": "Коррекция отмечена как примененная"
    })


@router.get("/teams/{team_id}/member/{member_id}/plan/{plan_id}/stats", response_class=HTMLResponse)
def member_plan_stats_page(
    team_id: int,
    member_id: int,
    plan_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Статистика по конкретному плану тренировок участника"""
    current_user = require_user(request, db)
    
    # Проверяем права доступа
    team = assert_can_manage_team(db, current_user, team_id)
    
    # Проверяем, что участник действительно в команде
    member = db.query(TeamMember).filter(
        TeamMember.team_id == team_id,
        TeamMember.user_id == member_id
    ).first()
    
    if not member:
        raise HTTPException(status_code=404, detail="Участник не найден в команде")
    
    # Получаем план тренировок
    from models import AnalysisTrainingPlan, Training, TrainingSession
    
    plan = db.query(AnalysisTrainingPlan).filter(
        AnalysisTrainingPlan.id == plan_id,
        AnalysisTrainingPlan.user_id == member_id
    ).first()
    
    if not plan:
        raise HTTPException(status_code=404, detail="План тренировок не найден")
    
    # Получаем тренировки плана
    trainings = db.query(Training).filter(
        Training.plan_id == plan.id
    ).order_by(Training.order).all()
    
    # Получаем сессии для каждой тренировки
    training_stats = []
    for training in trainings:
        sessions = db.query(TrainingSession).filter(
            TrainingSession.training_id == training.id
        ).order_by(TrainingSession.started_at.desc()).all()
        
        avg_score = None
        if sessions:
            scores = [s.score for s in sessions if s.score is not None]
            if scores:
                avg_score = sum(scores) / len(scores)
        
        training_stats.append({
            'training': training,
            'sessions': sessions,
            'avg_score': avg_score,
            'total_attempts': len(sessions),
            'best_score': training.best_score
        })
    
    return request.app.state.templates.TemplateResponse(
        "member_plan_stats.html",
        {
            "request": request,
            "user": current_user,
            "team": team,
            "member": member,
            "plan": plan,
            "training_stats": training_stats
        }
    )

