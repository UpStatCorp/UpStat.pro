"""
Упрощённый отчёт РОПу в train-режиме:
«Кто тренировался сегодня / средний score / streak».
Требует capability train_report (нет call_analysis).
"""
from datetime import datetime, date, timedelta
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from database import get_db
from deps import require_user, require_capability
from models import Team, TeamMember, User, TrainingSession, Training

router = APIRouter(
    tags=["train_report"],
    dependencies=[Depends(require_capability("train_report"))],
)

_STAGE_LABELS = {
    "contact": "Вступление в контакт",
    "needs": "Работа с потребностями",
    "presentation": "Презентация",
    "objections": "Работа с возражениями",
    "closing": "Завершение сделки",
}


def _compute_streak(dates_trained: set) -> int:
    """Считает streak — сколько дней подряд была хоть одна завершённая сессия."""
    streak = 0
    check = date.today()
    while True:
        if check in dates_trained:
            streak += 1
            check -= timedelta(days=1)
        else:
            break
    return streak


@router.get("/teams/{team_id}/train-report", response_class=HTMLResponse)
def train_report_page(team_id: int, request: Request, db: Session = Depends(get_db)):
    """Отчёт РОПу: кто тренировался сегодня / score / streak."""
    user = require_user(request, db)
    team = db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Команда не найдена")
    if team.manager_id != user.id:
        raise HTTPException(403, "Только менеджер команды может просматривать отчёт")

    today = date.today()
    tomorrow = today + timedelta(days=1)

    members = (
        db.query(TeamMember)
        .filter(TeamMember.team_id == team_id)
        .all()
    )
    member_user_ids = [m.user_id for m in members]

    # Все завершённые сессии участников команды (с джойном на Training для stage)
    all_rows = (
        db.query(TrainingSession, Training)
        .join(Training, TrainingSession.training_id == Training.id)
        .filter(
            TrainingSession.user_id.in_(member_user_ids),
            TrainingSession.status == "completed",
        )
        .order_by(TrainingSession.completed_at.desc())
        .all()
    )

    # Группируем по user_id
    from collections import defaultdict
    sessions_by_user: dict[int, list[tuple]] = defaultdict(list)
    for s, t in all_rows:
        sessions_by_user[s.user_id].append((s, t))

    # Строим статистику по каждому участнику
    report_rows = []
    for member in members:
        member_user = db.get(User, member.user_id)
        if not member_user:
            continue

        user_pairs = sessions_by_user[member.user_id]
        today_pairs = [
            (s, t) for s, t in user_pairs
            if s.completed_at and today <= s.completed_at.date() < tomorrow
        ]

        scores = [s.score for s, t in user_pairs if s.score is not None]
        avg_score = sum(scores) / len(scores) if scores else None

        t_scores = [s.score for s, t in today_pairs if s.score is not None]
        today_score = sum(t_scores) / len(t_scores) if t_scores else None

        dates_trained = {s.completed_at.date() for s, t in user_pairs if s.completed_at}
        streak = _compute_streak(dates_trained)

        # Последний этап из Training.stage / scenario_type
        last_stage = "—"
        if user_pairs:
            _, last_training = user_pairs[0]
            stage_key = last_training.stage or last_training.scenario_type or ""
            last_stage = _STAGE_LABELS.get(stage_key, "—")

        report_rows.append({
            "user": member_user,
            "trained_today": len(today_pairs) > 0,
            "today_sessions_count": len(today_pairs),
            "today_score": today_score,
            "avg_score": avg_score,
            "streak": streak,
            "total_sessions": len(user_pairs),
            "last_stage": last_stage,
        })

    # Сортировка: сначала тренировавшиеся сегодня, потом по streak
    report_rows.sort(key=lambda r: (-int(r["trained_today"]), -r["streak"]))

    return request.app.state.templates.TemplateResponse(
        "train/rop_report.html",
        {
            "request": request,
            "user": user,
            "team": team,
            "report_rows": report_rows,
            "today": today,
        },
    )
