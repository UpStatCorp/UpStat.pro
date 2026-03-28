"""
Роутер страницы «Аналитика» — дашборд с графиками + чат с ИИ-ассистентом для РОПа.
"""

import logging
from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, Request, Form, Response, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, case, Integer, exists

from database import get_db
from deps import require_user
from models import (
    AnalyticsMessage, TeamMember, Team, User,
    ParameterDefinition, ParameterValue, Conversation,
    CRMDeal, CRMLead, CRMIntegration,
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


def _parse_period(period: str) -> int:
    """Преобразует строку периода в количество дней (0 = всё время)."""
    mapping = {
        "2d": 2,
        "5d": 5,
        "7d": 7,
        "1m": 30,
        "3m": 90,
        "1y": 365,
        "all": 0,
    }
    return mapping.get(period, 30)


@router.get("/analytics/api/summary")
def api_summary(
    request: Request,
    db: Session = Depends(get_db),
    period: str = Query("1m"),
):
    """Карточки-сводка: всего звонков, средний talk%, среднее кол-во вопросов, возражения."""
    user = require_user(request, db)
    member_ids = _get_team_member_ids(db, user.id)
    if not member_ids:
        return JSONResponse({"total_calls": 0, "avg_talk": 0, "avg_questions": 0, "avg_objections": 0})

    days = _parse_period(period)

    def _avg_for(code: str):
        pd = db.query(ParameterDefinition).filter(ParameterDefinition.code == code).first()
        if not pd:
            return 0
        query = (
            db.query(func.avg(ParameterValue.value_number))
            .join(Conversation, Conversation.id == ParameterValue.conversation_id)
            .filter(
                ParameterValue.parameter_id == pd.id,
                ParameterValue.confidence >= 75,  # Только надёжные данные
                Conversation.user_id.in_(member_ids),
            )
        )
        if days > 0:
            since = datetime.utcnow() - timedelta(days=days)
            query = query.filter(Conversation.created_at >= since)
        val = query.scalar()
        return round(float(val), 1) if val else 0

    has_params = exists().where(ParameterValue.conversation_id == Conversation.id)
    query = db.query(func.count(Conversation.id)).filter(Conversation.user_id.in_(member_ids), has_params)
    if days > 0:
        since = datetime.utcnow() - timedelta(days=days)
        query = query.filter(Conversation.created_at >= since)
    total = query.scalar() or 0

    return JSONResponse({
        "total_calls": total,
        "avg_talk": _avg_for("talk_listen_ratio"),
        "avg_questions": _avg_for("manager_questions_count"),
        "avg_objections": _avg_for("objections_count"),
    })


def _get_friday_of_week(d) -> datetime:
    """Возвращает пятницу недели для даты (неделя: пт–чт)."""
    from datetime import date
    if isinstance(d, date) and not isinstance(d, datetime):
        d = datetime.combine(d, datetime.min.time())
    dow = d.weekday()  # 0=пн, 4=пт, 6=вс
    if dow >= 4:  # пт(4), сб(5), вс(6) — пятница этой недели
        days_since_fri = dow - 4
    else:  # пн(0), вт(1), ср(2), чт(3) — пятница прошлой недели
        days_since_fri = dow + 3
    return (d - timedelta(days=days_since_fri)).date()


def _get_period_key(d, interval: str):
    """Возвращает ключ группировки для даты в зависимости от интервала."""
    from datetime import date
    if isinstance(d, str):
        d = datetime.strptime(d, "%Y-%m-%d").date()
    elif isinstance(d, datetime):
        d = d.date()

    if interval == "1d":
        return d
    elif interval == "7d":
        return _get_friday_of_week(d)
    elif interval == "1m":
        return d.replace(day=1)
    elif interval == "3m":
        quarter = (d.month - 1) // 3
        return d.replace(month=quarter * 3 + 1, day=1)
    elif interval == "1y":
        return d.replace(month=1, day=1)
    return d


def _generate_all_period_keys(start_date, end_date, interval: str):
    """Генерирует все ключи периодов между start_date и end_date."""
    from datetime import date
    from calendar import monthrange
    
    keys = []
    current = _get_period_key(start_date, interval)
    end_key = _get_period_key(end_date, interval)
    
    while current <= end_key:
        keys.append(current)
        
        if interval == "1d":
            current = current + timedelta(days=1)
        elif interval == "7d":
            current = current + timedelta(days=7)
        elif interval == "1m":
            # Следующий месяц
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)
        elif interval == "3m":
            # Следующий квартал
            new_month = current.month + 3
            if new_month > 12:
                current = current.replace(year=current.year + 1, month=new_month - 12)
            else:
                current = current.replace(month=new_month)
        elif interval == "1y":
            current = current.replace(year=current.year + 1)
        else:
            current = current + timedelta(days=1)
    
    return keys


def _format_period_label(key, interval: str) -> str:
    """Форматирует метку для оси X в зависимости от интервала."""
    if interval == "1d":
        return key.strftime("%d.%m")
    elif interval == "7d":
        end = key + timedelta(days=6)
        return f"{key.strftime('%d.%m')}–{end.strftime('%d.%m')}"
    elif interval == "1m":
        months_ru = ["", "Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]
        return f"{months_ru[key.month]} {key.year}"
    elif interval == "3m":
        quarter = (key.month - 1) // 3 + 1
        return f"Q{quarter} {key.year}"
    elif interval == "1y":
        return str(key.year)
    return str(key)


@router.get("/analytics/api/trend")
def api_trend(
    request: Request,
    db: Session = Depends(get_db),
    metric: str = Query("talk_listen_ratio"),
    period: str = Query("1m"),
    interval: str = Query("7d"),
    manager_id: int = Query(None),
    fill_gaps: bool = Query(False),
):
    """Тренд числового параметра с настраиваемым интервалом и периодом."""
    user = require_user(request, db)
    member_ids = _get_team_member_ids(db, user.id)
    if not member_ids:
        return JSONResponse({"labels": [], "values": [], "title": "", "unit": "", "interval": interval})

    if manager_id and manager_id in member_ids:
        filter_ids = [manager_id]
    else:
        filter_ids = member_ids

    pd = db.query(ParameterDefinition).filter(ParameterDefinition.code == metric).first()
    if not pd:
        return JSONResponse({"labels": [], "values": [], "title": "Параметр не найден", "unit": "", "interval": interval})

    days = _parse_period(period)

    query = (
        db.query(
            func.date(Conversation.created_at).label("d"),
            ParameterValue.value_number,
        )
        .join(ParameterValue, ParameterValue.conversation_id == Conversation.id)
        .filter(
            ParameterValue.parameter_id == pd.id,
            ParameterValue.confidence >= 75,  # Только надёжные данные
            Conversation.user_id.in_(filter_ids),
        )
    )

    if days > 0:
        since = datetime.utcnow() - timedelta(days=days)
        query = query.filter(Conversation.created_at >= since)

    rows = query.all()

    from collections import defaultdict
    period_data = defaultdict(list)
    for r in rows:
        key = _get_period_key(r.d, interval)
        if r.value_number is not None:
            period_data[key].append(r.value_number)

    # Определяем диапазон дат
    if days > 0:
        since_date = (datetime.utcnow() - timedelta(days=days)).date()
        end_date = datetime.utcnow().date()
    else:
        # Всё время — берём минимальную и максимальную даты из данных
        if period_data:
            since_date = min(period_data.keys())
            end_date = datetime.utcnow().date()
        else:
            since_date = datetime.utcnow().date()
            end_date = datetime.utcnow().date()

    # Генерируем все периоды если fill_gaps=True
    if fill_gaps:
        all_keys = _generate_all_period_keys(since_date, end_date, interval)
    else:
        # Фильтруем ключи: показываем только те периоды, которые попадают в выбранный диапазон
        all_keys = [k for k in period_data.keys() if k >= since_date]
    
    sorted_keys = sorted(all_keys)

    labels = []
    values = []
    for key in sorted_keys:
        label = _format_period_label(key, interval)
        labels.append(label)
        avg_val = sum(period_data[key]) / len(period_data[key]) if period_data[key] else 0
        values.append(round(avg_val, 1))

    interval_names = {
        "1d": "день",
        "7d": "неделя (пт–чт)",
        "1m": "месяц",
        "3m": "квартал",
        "1y": "год",
    }

    return JSONResponse({
        "labels": labels,
        "values": values,
        "title": pd.title,
        "unit": pd.unit or "",
        "interval": interval,
        "interval_name": interval_names.get(interval, interval),
    })


@router.get("/analytics/api/comparison")
def api_comparison(
    request: Request,
    db: Session = Depends(get_db),
    metric: str = Query("talk_listen_ratio"),
    period: str = Query("1m"),
):
    """Сравнение менеджеров по числовому параметру (bar chart)."""
    user = require_user(request, db)
    member_ids = _get_team_member_ids(db, user.id)
    if not member_ids:
        return JSONResponse({"labels": [], "values": [], "title": ""})

    pd = db.query(ParameterDefinition).filter(ParameterDefinition.code == metric).first()
    if not pd:
        return JSONResponse({"labels": [], "values": [], "title": "Параметр не найден"})

    days = _parse_period(period)

    query = (
        db.query(
            User.name,
            func.avg(ParameterValue.value_number).label("avg_val"),
            func.count(ParameterValue.id).label("cnt"),
        )
        .join(Conversation, Conversation.user_id == User.id)
        .join(ParameterValue, ParameterValue.conversation_id == Conversation.id)
        .filter(
            ParameterValue.parameter_id == pd.id,
            ParameterValue.confidence >= 75,  # Только надёжные данные
            User.id.in_(member_ids),
        )
    )

    if days > 0:
        since = datetime.utcnow() - timedelta(days=days)
        query = query.filter(Conversation.created_at >= since)

    rows = query.group_by(User.name).order_by(func.avg(ParameterValue.value_number).desc()).all()

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
    period: str = Query("1m"),
):
    """Статистика по boolean-параметрам (doughnut chart)."""
    user = require_user(request, db)
    member_ids = _get_team_member_ids(db, user.id)
    if not member_ids:
        return JSONResponse({"params": []})

    days = _parse_period(period)
    true_expr = func.sum(case((ParameterValue.value_bool == True, 1), else_=0).cast(Integer))

    query = (
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
            ParameterValue.confidence >= 75,  # Только надёжные данные
            Conversation.user_id.in_(member_ids),
        )
    )

    if days > 0:
        since = datetime.utcnow() - timedelta(days=days)
        query = query.filter(Conversation.created_at >= since)

    rows = query.group_by(ParameterDefinition.code, ParameterDefinition.title).all()

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


@router.get("/analytics/api/team-members")
def api_team_members(request: Request, db: Session = Depends(get_db)):
    """Список менеджеров команды для фильтра."""
    user = require_user(request, db)
    member_ids = _get_team_member_ids(db, user.id)
    if not member_ids:
        return JSONResponse({"members": []})

    members = db.query(User).filter(User.id.in_(member_ids)).order_by(User.name).all()
    return JSONResponse({
        "members": [{"id": m.id, "name": m.name or m.email.split("@")[0]} for m in members]
    })


# ─── API: кнопочная навигация в чате ─────────────────────────


@router.get("/analytics/api/buttons")
def api_buttons(
    request: Request,
    ctx: str = Query("main_menu"),
    db: Session = Depends(get_db),
):
    """Возвращает кнопки для текущего уровня навигации."""
    user = require_user(request, db)

    from services.analytics_buttons import get_context
    block = get_context(ctx)

    if "buttons" in block:
        items = [
            {"id": b["id"], "label": b["label"], "full": b.get("full", b["label"]), "type": "nav"}
            for b in block["buttons"]
        ]
    elif "questions" in block:
        items = [
            {"id": q["id"], "label": q["short"], "full": q["full"], "type": "question"}
            for q in block["questions"]
        ]
    else:
        items = []

    return JSONResponse({
        "context": ctx,
        "label": block.get("label", ""),
        "parent": block.get("parent"),
        "buttons": items,
    })


class QueryRequest(BaseModel):
    question_id: str
    days: int = 30


@router.post("/analytics/query")
async def api_query(
    request: Request,
    body: QueryRequest,
    db: Session = Depends(get_db),
):
    """Выполняет SQL-запрос для выбранного вопроса и форматирует ответ через AI."""
    user = require_user(request, db)

    from services.analytics_buttons import find_question
    from services.analytics_queries import execute_query, format_with_ai, get_team_member_ids

    block_id, question = find_question(body.question_id)
    if not question:
        return JSONResponse({"error": "Вопрос не найден"}, status_code=404)

    member_ids = get_team_member_ids(db, user.id)
    if not member_ids:
        return JSONResponse({
            "response": "У вас пока нет команды. Создайте команду и пригласите менеджеров.",
            "question_full": question["full"],
            "block_id": block_id,
        })

    raw_data = execute_query(question, db, member_ids, body.days)
    formatted = await format_with_ai(question["full"], raw_data)

    user_msg = AnalyticsMessage(user_id=user.id, role="user", text=question["full"])
    db.add(user_msg)
    bot_msg = AnalyticsMessage(user_id=user.id, role="bot", text=formatted)
    db.add(bot_msg)
    db.commit()

    return JSONResponse({
        "response": formatted,
        "question_full": question["full"],
        "block_id": block_id,
        "user_msg_id": user_msg.id,
        "bot_msg_id": bot_msg.id,
    })


# ═══════════════════════════════════════════════════════════
# CRM-аналитика (третья вкладка)
# ═══════════════════════════════════════════════════════════

def _get_user_integration_ids(db: Session, user_id: int) -> List[int]:
    """Возвращает список ID интеграций пользователя."""
    rows = db.query(CRMIntegration.id).filter(
        CRMIntegration.user_id == user_id, CRMIntegration.is_active == True
    ).all()
    return [r.id for r in rows]


def _crm_period_filter(query, model_cls, period: str):
    """Фильтрует query по периоду (поле close_date или created_at)."""
    days = _parse_period(period)
    if days > 0:
        since = datetime.utcnow() - timedelta(days=days)
        date_col = getattr(model_cls, "close_date", None) or model_cls.created_at
        query = query.filter(date_col >= since)
    return query


@router.get("/analytics/api/crm/revenue")
def api_crm_revenue(request: Request, db: Session = Depends(get_db), period: str = Query("1m")):
    """Выручка по периодам (сумма opportunity закрытых выигранных сделок)."""
    user = require_user(request, db)
    integ_ids = _get_user_integration_ids(db, user.id)
    if not integ_ids:
        return JSONResponse({"total": 0, "count": 0, "labels": [], "values": []})

    base = db.query(CRMDeal).filter(
        CRMDeal.integration_id.in_(integ_ids),
        CRMDeal.closed == True,
        CRMDeal.is_won == True,
    )
    base = _crm_period_filter(base, CRMDeal, period)
    deals = base.all()

    total = sum(d.opportunity or 0 for d in deals)
    count = len(deals)

    by_month = {}
    for d in deals:
        key = d.close_date.strftime("%Y-%m") if d.close_date else "N/A"
        by_month[key] = by_month.get(key, 0) + (d.opportunity or 0)
    sorted_keys = sorted(by_month.keys())

    return JSONResponse({
        "total": round(total, 2),
        "count": count,
        "labels": sorted_keys,
        "values": [round(by_month[k], 2) for k in sorted_keys],
    })


@router.get("/analytics/api/crm/avg-check")
def api_crm_avg_check(request: Request, db: Session = Depends(get_db), period: str = Query("1m")):
    """Средний чек (avg opportunity по закрытым выигранным сделкам)."""
    user = require_user(request, db)
    integ_ids = _get_user_integration_ids(db, user.id)
    if not integ_ids:
        return JSONResponse({"avg_check": 0, "count": 0})

    row = db.query(
        func.avg(CRMDeal.opportunity).label("avg"),
        func.count(CRMDeal.id).label("cnt"),
    ).filter(
        CRMDeal.integration_id.in_(integ_ids),
        CRMDeal.closed == True,
        CRMDeal.is_won == True,
    )
    days = _parse_period(period)
    if days > 0:
        row = row.filter(CRMDeal.close_date >= datetime.utcnow() - timedelta(days=days))
    result = row.first()

    return JSONResponse({
        "avg_check": round(float(result.avg or 0), 2),
        "count": result.cnt or 0,
    })


@router.get("/analytics/api/crm/win-rate")
def api_crm_win_rate(request: Request, db: Session = Depends(get_db), period: str = Query("1m")):
    """Win rate: % выигранных от всех закрытых сделок."""
    user = require_user(request, db)
    integ_ids = _get_user_integration_ids(db, user.id)
    if not integ_ids:
        return JSONResponse({"win_rate": 0, "won": 0, "lost": 0, "total_closed": 0})

    base = db.query(CRMDeal).filter(
        CRMDeal.integration_id.in_(integ_ids),
        CRMDeal.closed == True,
    )
    days = _parse_period(period)
    if days > 0:
        base = base.filter(CRMDeal.close_date >= datetime.utcnow() - timedelta(days=days))

    total_closed = base.count()
    won = base.filter(CRMDeal.is_won == True).count()
    lost = total_closed - won

    return JSONResponse({
        "win_rate": round(won / total_closed * 100, 1) if total_closed > 0 else 0,
        "won": won,
        "lost": lost,
        "total_closed": total_closed,
    })


@router.get("/analytics/api/crm/conversion")
def api_crm_conversion(request: Request, db: Session = Depends(get_db), period: str = Query("1m")):
    """Конверсия воронки: лиды -> сделки -> закрытые -> выиграны."""
    user = require_user(request, db)
    integ_ids = _get_user_integration_ids(db, user.id)
    if not integ_ids:
        return JSONResponse({"leads": 0, "deals": 0, "closed": 0, "won": 0})

    days = _parse_period(period)
    since = datetime.utcnow() - timedelta(days=days) if days > 0 else None

    leads_q = db.query(CRMLead).filter(CRMLead.integration_id.in_(integ_ids))
    deals_q = db.query(CRMDeal).filter(CRMDeal.integration_id.in_(integ_ids))
    if since:
        leads_q = leads_q.filter(CRMLead.created_at >= since)
        deals_q = deals_q.filter(CRMDeal.created_at >= since)

    total_leads = leads_q.count()
    total_deals = deals_q.count()
    total_closed = deals_q.filter(CRMDeal.closed == True).count()
    total_won = deals_q.filter(CRMDeal.closed == True, CRMDeal.is_won == True).count()

    return JSONResponse({
        "leads": total_leads,
        "deals": total_deals,
        "closed": total_closed,
        "won": total_won,
    })


@router.get("/analytics/api/crm/cycle")
def api_crm_cycle(request: Request, db: Session = Depends(get_db), period: str = Query("1m")):
    """Средний цикл сделки (дни от создания до закрытия)."""
    user = require_user(request, db)
    integ_ids = _get_user_integration_ids(db, user.id)
    if not integ_ids:
        return JSONResponse({"avg_days": 0, "count": 0})

    base = db.query(CRMDeal).filter(
        CRMDeal.integration_id.in_(integ_ids),
        CRMDeal.closed == True,
        CRMDeal.created_at.isnot(None),
        CRMDeal.close_date.isnot(None),
    )
    days = _parse_period(period)
    if days > 0:
        base = base.filter(CRMDeal.close_date >= datetime.utcnow() - timedelta(days=days))
    deals = base.all()

    if not deals:
        return JSONResponse({"avg_days": 0, "count": 0})

    total_days = sum((d.close_date - d.created_at).days for d in deals if d.close_date and d.created_at)
    return JSONResponse({
        "avg_days": round(total_days / len(deals), 1) if deals else 0,
        "count": len(deals),
    })


@router.get("/analytics/api/crm/revenue-by-manager")
def api_crm_revenue_by_manager(request: Request, db: Session = Depends(get_db), period: str = Query("1m")):
    """Выручка по менеджерам (bar chart)."""
    user = require_user(request, db)
    integ_ids = _get_user_integration_ids(db, user.id)
    if not integ_ids:
        return JSONResponse({"labels": [], "values": []})

    base = db.query(
        CRMDeal.assigned_by_name,
        func.sum(CRMDeal.opportunity).label("total"),
        func.count(CRMDeal.id).label("cnt"),
    ).filter(
        CRMDeal.integration_id.in_(integ_ids),
        CRMDeal.closed == True,
        CRMDeal.is_won == True,
        CRMDeal.assigned_by_name.isnot(None),
    )
    days = _parse_period(period)
    if days > 0:
        base = base.filter(CRMDeal.close_date >= datetime.utcnow() - timedelta(days=days))
    rows = base.group_by(CRMDeal.assigned_by_name).order_by(func.sum(CRMDeal.opportunity).desc()).all()

    return JSONResponse({
        "labels": [r.assigned_by_name or "—" for r in rows],
        "values": [round(float(r.total or 0), 2) for r in rows],
        "counts": [r.cnt for r in rows],
    })
