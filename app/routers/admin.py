from fastapi import APIRouter, Depends, Request, status, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from database import get_db
from models import (
    User, Conversation, ZoomMeeting, Message, Attachment,
    AnalysisTrainingPlan, Training, TrainingSession, VoiceTrainingMessage,
    Notification, CRMIntegration, CRMRecording, MeetingParticipant,
    CustomMeeting, TeamMember, Team, TeamInvitation, TeamScript,
    TrainingConversionMetric, PasswordResetToken, TrainingErrorCorrection,
    Prompt,
)
from admin import admin_required, get_current_user, is_admin
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/", response_class=HTMLResponse)
def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    """Главная страница админки"""
    current_user = get_current_user(request, db)
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для доступа к админ-панели"
        )
    
    # Получаем статистику
    total_users = db.query(User).count()
    total_conversations = db.query(Conversation).count()
    total_meetings = db.query(ZoomMeeting).count()
    
    # Получаем последних пользователей
    recent_users = db.query(User).order_by(User.created_at.desc()).limit(5).all()
    
    stats = {
        "total_users": total_users,
        "total_conversations": total_conversations,
        "total_meetings": total_meetings,
        "new_users_today": 0,  # TODO: реализовать подсчет новых пользователей за день
        "new_conversations_today": 0,  # TODO: реализовать подсчет новых диалогов за день
        "new_meetings_today": 0,  # TODO: реализовать подсчет новых встреч за день
        "active_sessions": 1  # TODO: реализовать подсчет активных сессий
    }
    
    return request.app.state.templates.TemplateResponse(
        "admin/dashboard.html",
        {"request": request, "current_user": current_user, "stats": stats, "recent_users": recent_users}
    )


@router.get("/users", response_class=HTMLResponse)
def admin_users(request: Request, db: Session = Depends(get_db)):
    """Управление пользователями"""
    current_user = get_current_user(request, db)
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для доступа к управлению пользователями"
        )
    
    users = db.query(User).order_by(User.created_at.desc()).all()
    
    return request.app.state.templates.TemplateResponse(
        "admin/users.html",
        {"request": request, "current_user": current_user, "users": users}
    )


ALLOWED_ROLES = {"user", "admin", "manager", "sale_manager"}


@router.post("/users/{user_id}/set-role")
def set_user_role(
    user_id: int,
    request: Request,
    role: str = Form(...),
    db: Session = Depends(get_db)
):
    """Установка роли пользователя"""
    current_user = get_current_user(request, db)
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для изменения ролей пользователей"
        )
    
    if role not in ALLOWED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Недопустимая роль: {role}"
        )
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Пользователь не найден"
        )
    
    # Нельзя изменить роль самого себя
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя изменить собственную роль"
        )
    
    user.role = role
    db.commit()
    
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)


def _cascade_delete_user(db: Session, uid: int):
    """Удаляет все зависимые записи пользователя перед удалением самого User."""

    # 1. Teams managed by user — delete team data first
    managed_team_ids = [t.id for t in db.query(Team.id).filter(Team.manager_id == uid).all()]
    if managed_team_ids:
        db.query(TeamScript).filter(TeamScript.team_id.in_(managed_team_ids)).delete(synchronize_session=False)
        db.query(TeamInvitation).filter(TeamInvitation.team_id.in_(managed_team_ids)).delete(synchronize_session=False)
        db.query(TeamMember).filter(TeamMember.team_id.in_(managed_team_ids)).delete(synchronize_session=False)
        db.query(TrainingConversionMetric).filter(TrainingConversionMetric.team_id.in_(managed_team_ids)).delete(synchronize_session=False)
        db.query(TrainingErrorCorrection).filter(TrainingErrorCorrection.team_id.in_(managed_team_ids)).delete(synchronize_session=False)
        db.query(Team).filter(Team.id.in_(managed_team_ids)).delete(synchronize_session=False)

    # 2. Voice messages -> training sessions
    all_session_ids = [s.id for s in db.query(TrainingSession.id).filter(TrainingSession.user_id == uid).all()]
    if all_session_ids:
        db.query(VoiceTrainingMessage).filter(VoiceTrainingMessage.session_id.in_(all_session_ids)).delete(synchronize_session=False)
    db.query(TrainingSession).filter(TrainingSession.user_id == uid).delete(synchronize_session=False)

    # 3. Training plans -> trainings (and their sessions/voice messages)
    plan_ids = [p.id for p in db.query(AnalysisTrainingPlan.id).filter(AnalysisTrainingPlan.user_id == uid).all()]
    if plan_ids:
        training_ids = [t.id for t in db.query(Training.id).filter(Training.plan_id.in_(plan_ids)).all()]
        if training_ids:
            sub_session_ids = [s.id for s in db.query(TrainingSession.id).filter(TrainingSession.training_id.in_(training_ids)).all()]
            if sub_session_ids:
                db.query(VoiceTrainingMessage).filter(VoiceTrainingMessage.session_id.in_(sub_session_ids)).delete(synchronize_session=False)
            db.query(TrainingSession).filter(TrainingSession.training_id.in_(training_ids)).delete(synchronize_session=False)
        db.query(Training).filter(Training.plan_id.in_(plan_ids)).delete(synchronize_session=False)
        db.query(CRMRecording).filter(CRMRecording.training_plan_id.in_(plan_ids)).update(
            {CRMRecording.training_plan_id: None}, synchronize_session=False
        )
    db.query(AnalysisTrainingPlan).filter(AnalysisTrainingPlan.user_id == uid).delete(synchronize_session=False)

    # 4. CRM recordings & integrations
    db.query(CRMRecording).filter(CRMRecording.user_id == uid).delete(synchronize_session=False)
    db.query(CRMIntegration).filter(CRMIntegration.user_id == uid).delete(synchronize_session=False)

    # 5. Training error corrections (FK -> conversations, messages)
    db.query(TrainingErrorCorrection).filter(TrainingErrorCorrection.user_id == uid).delete(synchronize_session=False)

    # 6. Training conversion metrics
    db.query(TrainingConversionMetric).filter(TrainingConversionMetric.user_id == uid).delete(synchronize_session=False)

    # 7. Conversations -> messages -> attachments
    conv_ids = [c.id for c in db.query(Conversation.id).filter(Conversation.user_id == uid).all()]
    if conv_ids:
        msg_ids = [m.id for m in db.query(Message.id).filter(Message.conversation_id.in_(conv_ids)).all()]
        if msg_ids:
            db.query(Attachment).filter(Attachment.message_id.in_(msg_ids)).delete(synchronize_session=False)
        db.query(Message).filter(Message.conversation_id.in_(conv_ids)).delete(synchronize_session=False)
    db.query(Conversation).filter(Conversation.user_id == uid).delete(synchronize_session=False)
    db.query(Message).filter(Message.user_id == uid).update({Message.user_id: None}, synchronize_session=False)

    # 8. Meetings
    db.query(MeetingParticipant).filter(MeetingParticipant.user_id == uid).delete(synchronize_session=False)
    db.query(ZoomMeeting).filter(ZoomMeeting.user_id == uid).delete(synchronize_session=False)
    db.query(CustomMeeting).filter(CustomMeeting.creator_id == uid).delete(synchronize_session=False)

    # 9. Notifications
    db.query(Notification).filter(Notification.user_id == uid).delete(synchronize_session=False)

    # 10. Team memberships & invitations
    db.query(TeamMember).filter(TeamMember.user_id == uid).delete(synchronize_session=False)
    db.query(TeamInvitation).filter(TeamInvitation.invited_by_user_id == uid).delete(synchronize_session=False)
    db.query(TeamInvitation).filter(TeamInvitation.accepted_user_id == uid).update(
        {TeamInvitation.accepted_user_id: None}, synchronize_session=False
    )

    # 11. Password reset tokens
    db.query(PasswordResetToken).filter(PasswordResetToken.user_id == uid).delete(synchronize_session=False)

    # 12. Prompts & team scripts created by user
    db.query(Prompt).filter(Prompt.created_by == uid).delete(synchronize_session=False)
    db.query(TeamScript).filter(TeamScript.uploaded_by_user_id == uid).delete(synchronize_session=False)

    # 13. Self-referencing premium_granted_by
    db.query(User).filter(User.premium_granted_by == uid).update(
        {User.premium_granted_by: None}, synchronize_session=False
    )


@router.post("/users/{user_id}/delete")
def delete_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Удаление пользователя"""
    current_user = get_current_user(request, db)
    if not is_admin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Недостаточно прав для удаления пользователей"
        )
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Пользователь не найден"
        )
    
    # Нельзя удалить самого себя
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя удалить самого себя"
        )
    
    try:
        uid = user.id
        _cascade_delete_user(db, uid)
        db.delete(user)
        db.commit()
        logger.info(f"User {uid} deleted successfully by admin {current_user.id}")
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to delete user {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка при удалении пользователя: {str(e)}"
        )
    
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)
