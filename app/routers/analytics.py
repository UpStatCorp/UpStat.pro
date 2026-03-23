"""
Роутер страницы «Аналитика» — дашборд с графиками + чат с ИИ-ассистентом для РОПа.
"""

import logging
from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, Request, Form, Response, Query
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import func, case, Integer, exists

from database import get_db
from deps import require_user
from models import (
    AnalyticsMessage, TeamMember, Team, User,
    ParameterDefinition, ParameterValue, Conversation,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analytics"])


def _is_team_owner(db: Session, user_id: int) -> bool:
    """Проверяет, является ли пользователь владельцем/менеджером хотя бы одной команды."""
    owns_team = db.query(Team).filter(Team.manager_id == user_id).first()
    if owns_team:
        return True
    manages = (
        db.query(TeamMember)
        .filter(TeamMember.user_id == user_id, TeamMember.role_in_team == "manager")
        .first()
    )
    return manages is not None


@router.get("/analytics", response_class=HTMLResponse)
def analytics_page(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)

    if user.role not in ("manager", "admin") and not _is_team_owner(db, user.id):
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/chat", status_code=302)

    messages = (
        db.query(AnalyticsMessage)
        .filter(AnalyticsMessage.user_id == user.id)
        .order_by(AnalyticsMessage.created_at.asc())
        .limit(200)
        .all()
    )

    return request.app.state.templates.TemplateResponse(
        "analytics.html", {
            "request": request,
            "user": user,
            "messages": messages,
        }
    )


@router.post("/analytics/send", response_class=HTMLResponse)
async def analytics_send(request: Request, text: str = Form(...), db: Session = Depends(get_db)):
    user = require_user(request, db)

    if user.role not in ("manager", "admin") and not _is_team_owner(db, user.id):
        return Response(status_code=403)

    question = text.strip()
    if not question:
        return Response(status_code=400)

    user_msg = AnalyticsMessage(user_id=user.id, role="user", text=question)
    db.add(user_msg)
    db.commit()

    from services.analytics_assistant import get_ai_response
    answer = await get_ai_response(db, user.id, question)

    bot_msg = AnalyticsMessage(user_id=user.id, role="bot", text=answer)
    db.add(bot_msg)
    db.commit()

    return request.app.state.templates.TemplateResponse(
        "partials/analytics_messages.html", {
            "request": request,
            "messages": [user_msg, bot_msg],
        }
    )


@router.get("/analytics/poll", response_class=HTMLResponse)
def analytics_poll(request: Request, last_id: int = 0, db: Session = Depends(get_db)):
    user = require_user(request, db)

    new_msgs = (
        db.query(AnalyticsMessage)
        .filter(
            AnalyticsMessage.user_id == user.id,
            AnalyticsMessage.id > last_id,
        )
        .order_by(AnalyticsMessage.created_at.asc())
        .all()
    )

    if not new_msgs:
        return Response(status_code=204)

    return request.app.state.templates.TemplateResponse(
        "partials/analytics_messages.html", {
            "request": request,
            "messages": new_msgs,
        }
    )


@router.post("/analytics/clear")
def analytics_clear(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    db.query(AnalyticsMessage).filter(AnalyticsMessage.user_id == user.id).delete()
    db.commit()
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/analytics", status_code=302)


def _get_team_member_ids(db: Session, user_id: int) -> List[int]:
    """Возвращает ID всех участников команд, которыми управляет пользователь."""
    owned = db.query(Team).filter(Team.manager_id == user_id).all()
    managed = (
        db.query(Team)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .filter(TeamMember.user_id == user_id, TeamMember.role_in_team == "manager")
        .all()
    )
    seen = set()
    teams = []
    for t in owned + managed:
        if t.id not in seen:
            teams.append(t)
            seen.add(t.id)
    if not teams:
        return []
    team_ids = [t.id for t in teams]
    members = db.query(TeamMember).filter(TeamMember.team_id.in_(team_ids)).all()
    return list(set(m.user_id for m in members))


# ─── API: данные для графиков ───────────────────────────────


@router.get("/analytics/api/summary")
def api_summary(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=1, le=365),
):
    """Карточки-сводка: всего звонков, средний talk%, среднее кол-во вопросов, возражения."""
    user = require_user(request, db)
    member_ids = _get_team_member_ids(db, user.id)
    if not member_ids:
        return JSONResponse({"total_calls": 0, "avg_talk": 0, "avg_questions": 0, "avg_objections": 0})

    since = datetime.utcnow() - timedelta(days=days)

    def _avg_for(code: str):
        pd = db.query(ParameterDefinition).filter(ParameterDefinition.code == code).first()
        if not pd:
            return 0
        val = (
            db.query(func.avg(ParameterValue.value_number))
            .join(Conversation, Conversation.id == ParameterValue.conversation_id)
            .filter(
                ParameterValue.parameter_id == pd.id,
                Conversation.user_id.in_(member_ids),
                ParameterValue.created_at >= since,
            )
            .scalar()
        )
        return round(float(val), 1) if val else 0

    has_params = exists().where(ParameterValue.conversation_id == Conversation.id)
    total = (
        db.query(func.count(Conversation.id))
        .filter(Conversation.user_id.in_(member_ids), Conversation.created_at >= since, has_params)
        .scalar()
    ) or 0

    return JSONResponse({
        "total_calls": total,
        "avg_talk": _avg_for("talk_listen_ratio"),
        "avg_questions": _avg_for("manager_questions_count"),
        "avg_objections": _avg_for("objections_count"),
    })


@router.get("/analytics/api/trend")
def api_trend(
    request: Request,
    db: Session = Depends(get_db),
    metric: str = Query("talk_listen_ratio"),
    days: int = Query(30, ge=1, le=365),
):
    """Тренд числового параметра по дням."""
    user = require_user(request, db)
    member_ids = _get_team_member_ids(db, user.id)
    if not member_ids:
        return JSONResponse({"labels": [], "values": [], "title": ""})

    pd = db.query(ParameterDefinition).filter(ParameterDefinition.code == metric).first()
    if not pd:
        return JSONResponse({"labels": [], "values": [], "title": "Параметр не найден"})

    since = datetime.utcnow() - timedelta(days=days)

    rows = (
        db.query(
            func.date(Conversation.created_at).label("d"),
            func.avg(ParameterValue.value_number).label("avg_val"),
        )
        .join(ParameterValue, ParameterValue.conversation_id == Conversation.id)
        .filter(
            ParameterValue.parameter_id == pd.id,
            Conversation.user_id.in_(member_ids),
            ParameterValue.created_at >= since,
        )
        .group_by(func.date(Conversation.created_at))
        .order_by(func.date(Conversation.created_at))
        .all()
    )

    labels = []
    values = []
    for r in rows:
        d = r.d
        if isinstance(d, str):
            labels.append(d)
        else:
            labels.append(d.strftime("%d.%m"))
        values.append(round(float(r.avg_val), 1) if r.avg_val else 0)

    return JSONResponse({"labels": labels, "values": values, "title": pd.title, "unit": pd.unit or ""})


@router.get("/analytics/api/comparison")
def api_comparison(
    request: Request,
    db: Session = Depends(get_db),
    metric: str = Query("talk_listen_ratio"),
    days: int = Query(30, ge=1, le=365),
):
    """Сравнение менеджеров по числовому параметру (bar chart)."""
    user = require_user(request, db)
    member_ids = _get_team_member_ids(db, user.id)
    if not member_ids:
        return JSONResponse({"labels": [], "values": [], "title": ""})

    pd = db.query(ParameterDefinition).filter(ParameterDefinition.code == metric).first()
    if not pd:
        return JSONResponse({"labels": [], "values": [], "title": "Параметр не найден"})

    since = datetime.utcnow() - timedelta(days=days)

    rows = (
        db.query(
            User.name,
            func.avg(ParameterValue.value_number).label("avg_val"),
            func.count(ParameterValue.id).label("cnt"),
        )
        .join(Conversation, Conversation.user_id == User.id)
        .join(ParameterValue, ParameterValue.conversation_id == Conversation.id)
        .filter(
            ParameterValue.parameter_id == pd.id,
            User.id.in_(member_ids),
            ParameterValue.created_at >= since,
        )
        .group_by(User.name)
        .order_by(func.avg(ParameterValue.value_number).desc())
        .all()
    )

    labels = [r.name for r in rows]
    values = [round(float(r.avg_val), 1) if r.avg_val else 0 for r in rows]
    counts = [r.cnt for r in rows]

    return JSONResponse({
        "labels": labels,
        "values": values,
        "counts": counts,
        "title": pd.title,
        "unit": pd.unit or "",
    })


@router.get("/analytics/api/boolean-stats")
def api_boolean_stats(
    request: Request,
    db: Session = Depends(get_db),
    days: int = Query(30, ge=1, le=365),
):
    """Статистика по boolean-параметрам (doughnut chart)."""
    user = require_user(request, db)
    member_ids = _get_team_member_ids(db, user.id)
    if not member_ids:
        return JSONResponse({"params": []})

    since = datetime.utcnow() - timedelta(days=days)
    true_expr = func.sum(case((ParameterValue.value_bool == True, 1), else_=0).cast(Integer))

    rows = (
        db.query(
            ParameterDefinition.code,
            ParameterDefinition.title,
            func.count(ParameterValue.id).label("total"),
            true_expr.label("true_count"),
        )
        .join(ParameterValue, ParameterValue.parameter_id == ParameterDefinition.id)
        .join(Conversation, Conversation.id == ParameterValue.conversation_id)
        .filter(
            ParameterDefinition.value_type == "boolean",
            Conversation.user_id.in_(member_ids),
            ParameterValue.created_at >= since,
        )
        .group_by(ParameterDefinition.code, ParameterDefinition.title)
        .all()
    )

    params = []
    for r in rows:
        total = r.total or 0
        true_c = r.true_count or 0
        pct = round(true_c / total * 100) if total > 0 else 0
        params.append({
            "code": r.code,
            "title": r.title,
            "total": total,
            "true_count": true_c,
            "false_count": total - true_c,
            "true_pct": pct,
        })

    return JSONResponse({"params": params})


@router.get("/analytics/api/metrics-list")
def api_metrics_list(request: Request, db: Session = Depends(get_db)):
    """Список доступных числовых метрик для выбора в селекторе."""
    user = require_user(request, db)
    defs = (
        db.query(ParameterDefinition)
        .filter(ParameterDefinition.value_type == "number", ParameterDefinition.is_active == True)
        .order_by(ParameterDefinition.id)
        .all()
    )
    return JSONResponse({
        "metrics": [{"code": d.code, "title": d.title, "unit": d.unit or ""} for d in defs]
    })
