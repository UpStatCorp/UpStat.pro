"""
SQL-обработчики для кнопочной аналитики.
Каждый query_type выполняет SQL-запрос к parameter_values и возвращает dict с сырыми данными.
format_with_ai() оборачивает результат через GPT-4o-mini для красивого текста.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

from dotenv import load_dotenv
from openai import OpenAI
from sqlalchemy import func, case, Integer, text
from sqlalchemy.orm import Session

from models import (
    ParameterDefinition, ParameterValue, Conversation,
    User, TeamMember, Team,
)

load_dotenv()
logger = logging.getLogger("main")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
_client = OpenAI(api_key=OPENAI_API_KEY)


def get_team_member_ids(db: Session, user_id: int) -> List[int]:
    """Возвращает user_id всех участников команд, которыми управляет пользователь."""
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


def _param_ids(db: Session, codes: List[str]) -> Dict[str, int]:
    """Маппинг code -> id для заданных параметров."""
    rows = (
        db.query(ParameterDefinition.code, ParameterDefinition.id)
        .filter(ParameterDefinition.code.in_(codes))
        .all()
    )
    return {r.code: r.id for r in rows}


# ──────────────────────────────────────────────────────────────
# Query handlers
# ──────────────────────────────────────────────────────────────

def query_avg_number(
    db: Session, member_ids: List[int], param_codes: List[str], days: int = 30,
) -> Dict[str, Any]:
    """Средние значения числовых параметров по команде + по каждому менеджеру."""
    since = datetime.utcnow() - timedelta(days=days)
    pid_map = _param_ids(db, param_codes)
    if not pid_map:
        return {"error": "Параметры не найдены в справочнике."}

    p_ids = list(pid_map.values())

    team_avg = (
        db.query(
            ParameterDefinition.title,
            func.avg(ParameterValue.value_number).label("avg"),
            func.min(ParameterValue.value_number).label("min"),
            func.max(ParameterValue.value_number).label("max"),
            func.count(ParameterValue.id).label("cnt"),
        )
        .join(ParameterValue, ParameterValue.parameter_id == ParameterDefinition.id)
        .join(Conversation, Conversation.id == ParameterValue.conversation_id)
        .filter(
            ParameterValue.parameter_id.in_(p_ids),
            Conversation.user_id.in_(member_ids),
            ParameterValue.created_at >= since,
        )
        .group_by(ParameterDefinition.title)
        .all()
    )

    per_manager = (
        db.query(
            User.name,
            func.avg(ParameterValue.value_number).label("avg"),
            func.count(ParameterValue.id).label("cnt"),
        )
        .join(Conversation, Conversation.user_id == User.id)
        .join(ParameterValue, ParameterValue.conversation_id == Conversation.id)
        .filter(
            ParameterValue.parameter_id.in_(p_ids),
            User.id.in_(member_ids),
            ParameterValue.created_at >= since,
        )
        .group_by(User.name)
        .order_by(func.avg(ParameterValue.value_number).desc())
        .all()
    )

    return {
        "type": "avg_number",
        "period_days": days,
        "team": [
            {
                "title": r.title,
                "avg": round(float(r.avg), 2) if r.avg else 0,
                "min": round(float(r.min), 2) if r.min else 0,
                "max": round(float(r.max), 2) if r.max else 0,
                "calls": r.cnt,
            }
            for r in team_avg
        ],
        "managers": [
            {"name": r.name, "avg": round(float(r.avg), 2) if r.avg else 0, "calls": r.cnt}
            for r in per_manager
        ],
    }


def query_percent_true(
    db: Session, member_ids: List[int], param_codes: List[str], days: int = 30,
) -> Dict[str, Any]:
    """Процент True для boolean-параметров по команде + по каждому менеджеру."""
    since = datetime.utcnow() - timedelta(days=days)
    pid_map = _param_ids(db, param_codes)
    if not pid_map:
        return {"error": "Параметры не найдены в справочнике."}

    p_ids = list(pid_map.values())
    true_expr = func.sum(case((ParameterValue.value_bool == True, 1), else_=0).cast(Integer))

    team_stats = (
        db.query(
            ParameterDefinition.title,
            func.count(ParameterValue.id).label("total"),
            true_expr.label("true_cnt"),
        )
        .join(ParameterValue, ParameterValue.parameter_id == ParameterDefinition.id)
        .join(Conversation, Conversation.id == ParameterValue.conversation_id)
        .filter(
            ParameterValue.parameter_id.in_(p_ids),
            Conversation.user_id.in_(member_ids),
            ParameterValue.created_at >= since,
        )
        .group_by(ParameterDefinition.title)
        .all()
    )

    per_manager = (
        db.query(
            User.name,
            func.count(ParameterValue.id).label("total"),
            true_expr.label("true_cnt"),
        )
        .join(Conversation, Conversation.user_id == User.id)
        .join(ParameterValue, ParameterValue.conversation_id == Conversation.id)
        .filter(
            ParameterValue.parameter_id.in_(p_ids),
            User.id.in_(member_ids),
            ParameterValue.created_at >= since,
        )
        .group_by(User.name)
        .all()
    )

    def pct(true_c, total):
        return round(true_c / total * 100, 1) if total > 0 else 0

    return {
        "type": "percent_true",
        "period_days": days,
        "team": [
            {
                "title": r.title,
                "pct": pct(r.true_cnt or 0, r.total),
                "true_count": r.true_cnt or 0,
                "total": r.total,
            }
            for r in team_stats
        ],
        "managers": [
            {
                "name": r.name,
                "pct": pct(r.true_cnt or 0, r.total),
                "true_count": r.true_cnt or 0,
                "total": r.total,
            }
            for r in per_manager
        ],
    }


def query_rating(
    db: Session, member_ids: List[int], param_codes: List[str], days: int = 30,
) -> Dict[str, Any]:
    """Рейтинг менеджеров по числовому параметру (от лучшего к худшему)."""
    since = datetime.utcnow() - timedelta(days=days)
    pid_map = _param_ids(db, param_codes)
    if not pid_map:
        return {"error": "Параметры не найдены в справочнике."}

    p_ids = list(pid_map.values())

    rows = (
        db.query(
            User.name,
            func.avg(ParameterValue.value_number).label("avg"),
            func.count(ParameterValue.id).label("cnt"),
        )
        .join(Conversation, Conversation.user_id == User.id)
        .join(ParameterValue, ParameterValue.conversation_id == Conversation.id)
        .filter(
            ParameterValue.parameter_id.in_(p_ids),
            User.id.in_(member_ids),
            ParameterValue.created_at >= since,
        )
        .group_by(User.name)
        .order_by(func.avg(ParameterValue.value_number).desc())
        .all()
    )

    return {
        "type": "rating",
        "period_days": days,
        "ranking": [
            {"rank": i + 1, "name": r.name, "avg": round(float(r.avg), 2) if r.avg else 0, "calls": r.cnt}
            for i, r in enumerate(rows)
        ],
    }


def query_rating_bool(
    db: Session, member_ids: List[int], param_codes: List[str], days: int = 30,
) -> Dict[str, Any]:
    """Рейтинг менеджеров по % True boolean-параметра."""
    since = datetime.utcnow() - timedelta(days=days)
    pid_map = _param_ids(db, param_codes)
    if not pid_map:
        return {"error": "Параметры не найдены в справочнике."}

    p_ids = list(pid_map.values())
    true_expr = func.sum(case((ParameterValue.value_bool == True, 1), else_=0).cast(Integer))

    rows = (
        db.query(
            User.name,
            func.count(ParameterValue.id).label("total"),
            true_expr.label("true_cnt"),
        )
        .join(Conversation, Conversation.user_id == User.id)
        .join(ParameterValue, ParameterValue.conversation_id == Conversation.id)
        .filter(
            ParameterValue.parameter_id.in_(p_ids),
            User.id.in_(member_ids),
            ParameterValue.created_at >= since,
        )
        .group_by(User.name)
        .order_by((true_expr * 100 / func.count(ParameterValue.id)).desc())
        .all()
    )

    def pct(true_c, total):
        return round(true_c / total * 100, 1) if total > 0 else 0

    return {
        "type": "rating_bool",
        "period_days": days,
        "ranking": [
            {
                "rank": i + 1,
                "name": r.name,
                "pct": pct(r.true_cnt or 0, r.total),
                "calls": r.total,
            }
            for i, r in enumerate(rows)
        ],
    }


# ──────────────────────────────────────────────────────────────
# Dispatcher
# ──────────────────────────────────────────────────────────────

_QUERY_MAP = {
    "avg_number": query_avg_number,
    "percent_true": query_percent_true,
    "rating": query_rating,
    "rating_bool": query_rating_bool,
}


def execute_query(
    question_config: dict, db: Session, member_ids: List[int], days: int = 30,
) -> Dict[str, Any]:
    """Вызывает нужный обработчик по question_config['query_type']."""
    qt = question_config.get("query_type", "avg_number")
    handler = _QUERY_MAP.get(qt)
    if not handler:
        return {"error": f"Неизвестный query_type: {qt}"}
    return handler(db, member_ids, question_config.get("params", []), days)


# ──────────────────────────────────────────────────────────────
# AI formatting (GPT-4o-mini, cheap & fast)
# ──────────────────────────────────────────────────────────────

_FORMAT_SYSTEM = """Ты — ИИ-аналитик платформы UpStat. Тебе дают сырые данные (числа, проценты, рейтинги) из базы данных по звонкам.
Твоя задача — оформить их в читаемый, краткий текст для руководителя отдела продаж.

ПРАВИЛА:
- Пиши по-русски, кратко — максимум 5-7 строк.
- Указывай конкретные цифры.
- Если есть рейтинг менеджеров — укажи лидера и отстающего.
- Не выдумывай данные, используй только предоставленные.
- Не давай рекомендаций, только факты и цифры.
- Если данных нет (пустые списки) — скажи об этом.
- Используй эмодзи для визуального оформления (📊 📈 👤 ✅ ❌ 🏆 и т.д.)."""


async def format_with_ai(question_label: str, raw_data: Dict[str, Any]) -> str:
    """Форматирует сырые данные в читаемый текст через GPT-4o-mini."""
    if raw_data.get("error"):
        return f"⚠️ {raw_data['error']}"

    import json
    data_str = json.dumps(raw_data, ensure_ascii=False, indent=2)

    try:
        response = await asyncio.to_thread(
            lambda: _client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": _FORMAT_SYSTEM},
                    {"role": "user", "content": f"Вопрос: {question_label}\n\nДанные:\n{data_str}"},
                ],
                temperature=0.2,
                max_tokens=400,
            )
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"AI formatting error: {e}", exc_info=True)
        return _fallback_format(question_label, raw_data)


def _fallback_format(question_label: str, data: Dict[str, Any]) -> str:
    """Простое форматирование, если AI недоступен."""
    lines = [f"📊 {question_label}", ""]

    if data.get("type") in ("avg_number",):
        for item in data.get("team", []):
            lines.append(f"• {item['title']}: среднее {item['avg']} (мин {item['min']}, макс {item['max']}, звонков: {item['calls']})")
        if data.get("managers"):
            lines.append("")
            lines.append("По менеджерам:")
            for m in data["managers"]:
                lines.append(f"  👤 {m['name']}: {m['avg']} ({m['calls']} зв.)")

    elif data.get("type") in ("percent_true",):
        for item in data.get("team", []):
            lines.append(f"• {item['title']}: {item['pct']}% ({item['true_count']}/{item['total']})")
        if data.get("managers"):
            lines.append("")
            lines.append("По менеджерам:")
            for m in data["managers"]:
                lines.append(f"  👤 {m['name']}: {m['pct']}% ({m['true_count']}/{m['total']})")

    elif data.get("type") in ("rating", "rating_bool"):
        for item in data.get("ranking", []):
            val = item.get("avg", item.get("pct", "—"))
            lines.append(f"  {item['rank']}. {item['name']}: {val} ({item['calls']} зв.)")

    return "\n".join(lines) if len(lines) > 2 else f"📊 {question_label}\n\nДанных пока нет."
