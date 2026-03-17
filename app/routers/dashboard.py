from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from deps import require_user
from models import Conversation, Message, Attachment, AnalysisTrainingPlan, Training, CRMRecording, User
from sqlalchemy.orm import joinedload, selectinload

router = APIRouter(tags=["dashboard"])

def _get_user_conversation(db: Session, user_id: int) -> Optional[Conversation]:
    return (
        db.query(Conversation)
        .filter(Conversation.user_id == user_id)
        .order_by(Conversation.id.desc())
        .first()
    )

def _collect_analysis_packages(db: Session, conv_id: int, limit: int = 100):
    """
    Пакет = сообщение бота, где есть вложение analysis_*.txt.
    Плюс ищем предыдущее бот-сообщение (обычно с transcript_*/dialogue_*)
    """
    rows = (
        db.query(Message)
        .filter(Message.conversation_id == conv_id, Message.role == "bot")
        .order_by(Message.created_at.desc())
        .limit(limit)
        .all()
    )
    packages = []
    for i, m in enumerate(rows):
        report_atts = [a for a in m.attachments if a.file_name.startswith("analysis_")]
        if not report_atts:
            continue
        # предыдущее бот-сообщение (если есть)
        prev_msg = rows[i + 1] if i + 1 < len(rows) else None
        trans_dialog_atts = []
        if prev_msg:
            for a in prev_msg.attachments:
                if a.file_name.startswith(("transcript_", "dialogue_")):
                    trans_dialog_atts.append(a)

        packages.append({
            "report_msg_id": m.id,
            "created_at": m.created_at,
            "report": report_atts[0],  # один главный отчёт
            "extras": trans_dialog_atts  # transcript/dialogue
        })
    return packages


def _collect_user_analysis_packages(db: Session, user_id: int, limit: int = 100):
    """
    Собирает все анализы пользователя из ВСЕХ его конверсаций,
    включая анализы записей из CRM систем (без дублирования).
    """
    crm_conv_ids_q = (
        db.query(CRMRecording.conversation_id)
        .filter(
            CRMRecording.user_id == user_id,
            CRMRecording.conversation_id.isnot(None),
        )
        .all()
    )
    crm_conv_ids = {r[0] for r in crm_conv_ids_q}

    user_conversations = (
        db.query(Conversation.id)
        .filter(Conversation.user_id == user_id)
        .all()
    )
    non_crm_conv_ids = [c.id for c in user_conversations if c.id not in crm_conv_ids]

    packages = []
    seen_msg_ids: set = set()

    if non_crm_conv_ids:
        rows = (
            db.query(Message)
            .options(selectinload(Message.attachments))
            .filter(
                Message.conversation_id.in_(non_crm_conv_ids),
                Message.role == "bot"
            )
            .order_by(Message.created_at.desc())
            .limit(limit)
            .all()
        )

        messages_by_conv = {}
        for m in rows:
            messages_by_conv.setdefault(m.conversation_id, []).append(m)

        for m in rows:
            report_atts = [a for a in m.attachments if a.file_name.startswith("analysis_")]
            if not report_atts:
                continue

            trans_dialog_atts = []
            conv_messages = messages_by_conv.get(m.conversation_id, [])
            try:
                current_idx = conv_messages.index(m)
                for offset in range(1, min(6, len(conv_messages) - current_idx)):
                    prev_msg = conv_messages[current_idx + offset]
                    for a in prev_msg.attachments:
                        if a.file_name.startswith(("transcript_", "dialogue_")):
                            trans_dialog_atts.append(a)
                    if trans_dialog_atts:
                        break
            except (ValueError, IndexError):
                pass

            seen_msg_ids.add(m.id)
            packages.append({
                "report_msg_id": m.id,
                "created_at": m.created_at,
                "report": report_atts[0],
                "extras": trans_dialog_atts,
                "is_from_crm": False,
            })

    crm_recordings = (
        db.query(CRMRecording)
        .options(joinedload(CRMRecording.integration))
        .filter(
            CRMRecording.user_id == user_id,
            CRMRecording.sync_status == "completed",
            CRMRecording.conversation_id.isnot(None),
        )
        .order_by(CRMRecording.created_at.desc())
        .limit(limit)
        .all()
    )

    for recording in crm_recordings:
        report_msg = (
            db.query(Message)
            .options(selectinload(Message.attachments))
            .filter(
                Message.conversation_id == recording.conversation_id,
                Message.role == "bot",
            )
            .join(Attachment)
            .filter(Attachment.file_name.startswith("analysis_"))
            .order_by(Message.created_at.desc())
            .first()
        )

        if not report_msg or report_msg.id in seen_msg_ids:
            continue

        report_att = next((a for a in report_msg.attachments if a.file_name.startswith("analysis_")), None)
        if not report_att:
            continue

        prev_msgs = (
            db.query(Message)
            .filter(
                Message.conversation_id == recording.conversation_id,
                Message.role == "bot",
                Message.id < report_msg.id,
            )
            .order_by(Message.id.desc())
            .limit(5)
            .all()
        )

        trans_dialog_atts = []
        for prev_m in prev_msgs:
            for a in prev_m.attachments:
                if a.file_name.startswith(("transcript_", "dialogue_")):
                    trans_dialog_atts.append(a)
            if trans_dialog_atts:
                break

        seen_msg_ids.add(report_msg.id)
        packages.append({
            "report_msg_id": report_msg.id,
            "created_at": report_msg.created_at,
            "report": report_att,
            "extras": trans_dialog_atts,
            "is_from_crm": True,
            "crm_info": {
                "integration_name": recording.integration.crm_name if recording.integration else "CRM",
                "call_date": recording.call_date,
                "manager_name": recording.manager_name,
            },
        })

    packages.sort(key=lambda x: x["created_at"], reverse=True)
    return packages[:limit]

@router.get("/dashboard")
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    # Загружаем информацию о командах пользователя для отображения в меню
    user = db.query(User).options(joinedload(User.team_memberships)).filter(User.id == user.id).first()
    
    # Получаем все конверсации пользователя
    user_conversations = (
        db.query(Conversation.id)
        .filter(Conversation.user_id == user.id)
        .all()
    )
    conv_ids = [c.id for c in user_conversations]
    
    analyses_count = 0
    audio_count = 0
    last_activity = None

    if conv_ids:
        # кол-во анализов во ВСЕХ конверсациях пользователя
        analyses_count = (
            db.query(Attachment.id)
            .join(Message, Message.id == Attachment.message_id)
            .filter(
                Message.conversation_id.in_(conv_ids),
                Message.role == "bot",
                Attachment.file_name.startswith("analysis_"),
            )
            .count()
        )
        # кол-во загруженных аудио во ВСЕХ конверсациях пользователя
        audio_count = (
            db.query(Attachment.id)
            .join(Message, Message.id == Attachment.message_id)
            .filter(
                Message.conversation_id.in_(conv_ids),
                Attachment.mime_type.like("audio/%"),
            )
            .count()
        )
        # последняя активность во ВСЕХ конверсациях пользователя
        last_msg = (
            db.query(Message.created_at)
            .filter(Message.conversation_id.in_(conv_ids))
            .order_by(Message.created_at.desc())
            .first()
        )
        last_activity = last_msg[0] if last_msg else None

    # последние 5 пакетов из ВСЕХ конверсаций пользователя
    packages = _collect_user_analysis_packages(db, user.id, limit=5)
    
    # Загружаем планы тренировок пользователя
    training_plans = (
        db.query(AnalysisTrainingPlan)
        .filter(AnalysisTrainingPlan.user_id == user.id)
        .order_by(AnalysisTrainingPlan.created_at.desc())
        .limit(10)
        .all()
    )
    
    # Получаем количество записей из CRM
    crm_recordings_count = (
        db.query(CRMRecording)
        .filter(CRMRecording.user_id == user.id)
        .count()
    )
    
    # Получаем количество проанализированных записей из CRM
    crm_analyzed_count = (
        db.query(CRMRecording)
        .filter(
            CRMRecording.user_id == user.id,
            CRMRecording.sync_status == "completed"
        )
        .count()
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

    # Проверяем, нужно ли показать приветственное окно (первый вход после регистрации)
    show_welcome = request.session.pop("show_welcome", False)
    
    return request.app.state.templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "analyses_count": analyses_count,
            "audio_count": audio_count,
            "last_activity": last_activity,
            "packages": packages,
            "training_plans": training_plans_data,
            "crm_recordings_count": crm_recordings_count,
            "crm_analyzed_count": crm_analyzed_count,
            "show_welcome": show_welcome,
        },
    )

@router.get("/calls")
def calls(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    # Загружаем информацию о командах пользователя для отображения в меню
    user = db.query(User).options(joinedload(User.team_memberships)).filter(User.id == user.id).first()
    # Используем новую функцию, которая ищет анализы во ВСЕХ конверсациях пользователя
    packages = _collect_user_analysis_packages(db, user.id, limit=100)
    
    # Добавляем информацию о тренировках для каждого пакета
    for package in packages:
        # Ищем план тренировок для этого анализа
        training_plan = (
            db.query(AnalysisTrainingPlan)
            .filter(AnalysisTrainingPlan.report_message_id == package["report_msg_id"])
            .first()
        )
        
        if training_plan:
            # Получаем текущую доступную тренировку
            current_training = (
                db.query(Training)
                .filter(
                    Training.plan_id == training_plan.id,
                    Training.status.in_(['available', 'in_progress'])
                )
                .order_by(Training.order)
                .first()
            )
            
            package["training_plan"] = {
                "id": training_plan.id,
                "total": training_plan.total_trainings,
                "completed": training_plan.completed_trainings,
                "progress": int((training_plan.completed_trainings / training_plan.total_trainings * 100)) if training_plan.total_trainings > 0 else 0,
                "status": training_plan.status,
                "current_training": current_training
            }
        else:
            package["training_plan"] = None
    
    return request.app.state.templates.TemplateResponse(
        "calls.html", {"request": request, "user": user, "packages": packages}
    )

@router.get("/settings")
def settings(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    # Загружаем информацию о командах пользователя для отображения в меню
    user = db.query(User).options(joinedload(User.team_memberships)).filter(User.id == user.id).first()
    return request.app.state.templates.TemplateResponse(
        "settings.html", {"request": request, "user": user}
    )
