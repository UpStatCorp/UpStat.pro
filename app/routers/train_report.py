"""
Отчёты РОПу в train-режиме:
- /teams/{id}/train-report — сводка по команде (кто тренировался / score / streak)
- /teams/{id}/member/{mid}/train-report — детальный отчёт по члену команды
Требует capability train_report (нет call_analysis).
"""
import json
from collections import defaultdict
from datetime import date, timedelta
from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from database import get_db
from deps import require_user, require_capability
from models import (
    Team, TeamMember, User, TrainingSession, Training, SellerPassport,
    TrainingProgram,
)
from time_utils import local_date, today_local
from services import streak_service

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
_STAGE_ORDER = ["contact", "needs", "presentation", "objections", "closing"]


def _stage_key(training: Training) -> str:
    return (training.stage or training.scenario_type or "") if training else ""


@router.get("/teams/{team_id}/train-report", response_class=HTMLResponse)
def train_report_page(team_id: int, request: Request, db: Session = Depends(get_db)):
    """Сводный отчёт РОПу: кто тренировался сегодня / score / streak."""
    user = require_user(request, db)
    team = db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Команда не найдена")
    if team.manager_id != user.id:
        raise HTTPException(403, "Только менеджер команды может просматривать отчёт")

    today = today_local()

    members = db.query(TeamMember).filter(TeamMember.team_id == team_id).all()
    member_user_ids = [m.user_id for m in members]

    all_rows = (
        db.query(TrainingSession, Training)
        .join(Training, TrainingSession.training_id == Training.id)
        .filter(
            TrainingSession.user_id.in_(member_user_ids),
            TrainingSession.status == "completed",
        )
        .order_by(TrainingSession.completed_at.desc())
        .all()
    ) if member_user_ids else []

    sessions_by_user: dict[int, list[tuple]] = defaultdict(list)
    trained_dates_by_user: dict[int, set] = defaultdict(set)
    for s, t in all_rows:
        sessions_by_user[s.user_id].append((s, t))
        if s.completed_at:
            d = local_date(s.completed_at)
            if d:
                trained_dates_by_user[s.user_id].add(d)

    # Пользователи одним запросом (без N+1 db.get на каждого)
    users_by_id = {
        u.id: u
        for u in db.query(User).filter(User.id.in_(member_user_ids)).all()
    } if member_user_ids else {}

    # best_streak из паспортов
    passports = {
        p.user_id: p
        for p in db.query(SellerPassport)
        .filter(SellerPassport.user_id.in_(member_user_ids))
        .all()
    } if member_user_ids else {}

    # Расписание программы команды считаем один раз для всех участников
    team_program = (
        db.query(TrainingProgram)
        .filter(TrainingProgram.team_id == team_id, TrainingProgram.is_active == True)  # noqa: E712
        .first()
    )
    sched, prog_start, prog_cycle = streak_service.program_schedule(db, team_program)

    report_rows = []
    for member in members:
        member_user = users_by_id.get(member.user_id)
        if not member_user:
            continue

        user_pairs = sessions_by_user[member.user_id]
        today_pairs = [
            (s, t) for s, t in user_pairs
            if s.completed_at and local_date(s.completed_at) == today
        ]

        scores = [s.score for s, t in user_pairs if s.score is not None]
        avg_score = sum(scores) / len(scores) if scores else None

        t_scores = [s.score for s, t in today_pairs if s.score is not None]
        today_score = sum(t_scores) / len(t_scores) if t_scores else None

        streak = streak_service.streak_from_dates(
            trained_dates_by_user[member.user_id], today, sched, prog_start, prog_cycle
        )
        passport = passports.get(member.user_id)
        # fallback: если паспорт ещё не создан — берём рассчитанный стрик,
        # иначе максимум (паспорт мог отстать от свежих сессий)
        best_streak = max(streak, passport.best_streak or 0) if passport else streak

        last_stage = "—"
        if user_pairs:
            last_stage = _STAGE_LABELS.get(_stage_key(user_pairs[0][1]), "—")

        report_rows.append({
            "user": member_user,
            "trained_today": len(today_pairs) > 0,
            "today_sessions_count": len(today_pairs),
            "today_score": today_score,
            "avg_score": avg_score,
            "streak": streak,
            "best_streak": best_streak,
            "total_sessions": len(user_pairs),
            "last_stage": last_stage,
        })

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


@router.get("/teams/{team_id}/member/{member_id}/train-report", response_class=HTMLResponse)
def member_train_report(team_id: int, member_id: int, request: Request, db: Session = Depends(get_db)):
    """Детальный отчёт по члену команды: этапы, тренд, активность, история сессий."""
    user = require_user(request, db)
    team = db.get(Team, team_id)
    if not team:
        raise HTTPException(404, "Команда не найдена")
    if team.manager_id != user.id:
        raise HTTPException(403, "Только менеджер команды может просматривать отчёт")

    # участник должен быть в команде
    membership = (
        db.query(TeamMember)
        .filter(TeamMember.team_id == team_id, TeamMember.user_id == member_id)
        .first()
    )
    if not membership:
        raise HTTPException(404, "Участник не найден в команде")

    member_user = db.get(User, member_id)
    if not member_user:
        raise HTTPException(404, "Пользователь не найден")

    today = today_local()

    # Все завершённые сессии участника
    rows = (
        db.query(TrainingSession, Training)
        .join(Training, TrainingSession.training_id == Training.id)
        .filter(
            TrainingSession.user_id == member_id,
            TrainingSession.status == "completed",
            TrainingSession.completed_at.isnot(None),
        )
        .order_by(TrainingSession.completed_at.asc())
        .all()
    )

    scores = [s.score for s, t in rows if s.score is not None]
    avg_score = round(sum(scores) / len(scores), 1) if scores else None

    # ── Разбивка по этапам ────────────────────────────────────────────────
    stage_acc = {k: {"label": _STAGE_LABELS[k], "scores": [], "attempts": 0, "last": None}
                 for k in _STAGE_ORDER}
    for s, t in rows:
        k = _stage_key(t)
        if k in stage_acc:
            stage_acc[k]["attempts"] += 1
            if s.score is not None:
                stage_acc[k]["scores"].append(s.score)
            d = local_date(s.completed_at)
            if d and (stage_acc[k]["last"] is None or d > stage_acc[k]["last"]):
                stage_acc[k]["last"] = d
    per_stage = []
    for k in _STAGE_ORDER:
        a = stage_acc[k]
        per_stage.append({
            "key": k,
            "label": a["label"],
            "attempts": a["attempts"],
            "avg": round(sum(a["scores"]) / len(a["scores"]), 1) if a["scores"] else None,
            "best": max(a["scores"]) if a["scores"] else None,
            "last": a["last"],
        })

    # ── Тренд балла во времени → точки для inline-SVG ──────────────────────
    trend = [(local_date(s.completed_at), s.score) for s, t in rows if s.score is not None]
    W, H, P = 640, 140, 12
    trend_points = ""
    if trend:
        n = len(trend)
        coords = []
        for i, (_, v) in enumerate(trend):
            x = P + (W - 2 * P) * (i / (n - 1) if n > 1 else 0.5)
            y = (H - P) - (H - 2 * P) * (max(0, min(100, v)) / 100)
            coords.append(f"{x:.1f},{y:.1f}")
        trend_points = " ".join(coords)

    # ── Heatmap активности (последние 8 недель) ────────────────────────────
    trained_count = defaultdict(int)
    for s, t in rows:
        d = local_date(s.completed_at)
        if d:
            trained_count[d] += 1
    heatmap = []
    for i in range(55, -1, -1):
        d = today - timedelta(days=i)
        c = trained_count.get(d, 0)
        level = 0 if c == 0 else (1 if c == 1 else (2 if c == 2 else (3 if c <= 4 else 4)))
        heatmap.append({"date": d, "count": c, "level": level})

    # ── Дисциплина / adherence (последние 30 дней по программе) ────────────
    adherence = None
    scheduled_days = trained_scheduled = 0
    missed_days = []
    prog = streak_service.active_program(db, member_user)
    if prog and prog.start_date:
        sched = streak_service._scheduled_day_indices(db, prog)
        start = local_date(prog.start_date)
        cycle = max(1, prog.cycle_days)
        if sched and start:
            for i in range(29, -1, -1):
                d = today - timedelta(days=i)
                if d < start:
                    continue
                day_idx = (d - start).days % cycle
                if day_idx not in sched:
                    continue  # выходной
                scheduled_days += 1
                if trained_count.get(d, 0) > 0:
                    trained_scheduled += 1
                elif d < today:  # сегодня ещё не считаем пропуском
                    missed_days.append(d)
            if scheduled_days:
                adherence = round(100 * trained_scheduled / scheduled_days)

    # ── Вовлечённость ──────────────────────────────────────────────────────
    voice_count = sum(1 for s, t in rows if (s.session_type or "") == "voice")
    text_count = len(rows) - voice_count
    durations = [s.duration_seconds for s, t in rows if s.duration_seconds]
    avg_duration = round(sum(durations) / len(durations)) if durations else None
    total_responses = sum((s.user_responses_count or 0) for s, t in rows)
    total_questions = sum((s.ai_questions_count or 0) for s, t in rows)

    # ── История сессий (последние 50, новые сверху) ────────────────────────
    sessions = []
    for s, t in reversed(rows[-50:]):
        checklist = None
        if s.checklist_results_json:
            try:
                checklist = json.loads(s.checklist_results_json)
            except Exception:
                checklist = None
        sessions.append({
            "id": s.id,
            "date": s.completed_at,
            "stage_label": _STAGE_LABELS.get(_stage_key(t), "Тренировка"),
            "duration": s.duration_seconds,
            "score": s.score,
            "type": s.session_type or "text",
            "feedback": s.feedback,
            "transcript": s.transcript,
            "checklist": checklist,
            "responses": s.user_responses_count,
            "questions": s.ai_questions_count,
        })

    passport = db.query(SellerPassport).filter_by(user_id=member_id).first()
    current_streak = streak_service.compute_streak(db, member_user, today)
    best_streak = max(current_streak, passport.best_streak or 0) if passport else current_streak

    stats = {
        "total_sessions": len(rows),
        "avg_score": avg_score,
        "current_streak": current_streak,
        "best_streak": best_streak,
        "adherence": adherence,
        "scheduled_days": scheduled_days,
        "trained_scheduled": trained_scheduled,
    }
    engagement = {
        "voice_count": voice_count,
        "text_count": text_count,
        "avg_duration": avg_duration,
        "total_responses": total_responses,
        "total_questions": total_questions,
    }

    return request.app.state.templates.TemplateResponse(
        "train/member_report.html",
        {
            "request": request,
            "user": user,
            "team": team,
            "member": member_user,
            "stats": stats,
            "per_stage": per_stage,
            "trend_points": trend_points,
            "trend_w": W, "trend_h": H,
            "heatmap": heatmap,
            "missed_days": missed_days,
            "engagement": engagement,
            "sessions": sessions,
        },
    )
