"""
ИИ-ассистент для РОПа — отвечает на вопросы по структурированным параметрам звонков.
Формирует SQL-запросы к parameter_values, агрегирует данные, передаёт контекст GPT.
"""

import json
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from openai import OpenAI
from sqlalchemy.orm import Session
from sqlalchemy import func, exists
from collections import defaultdict

from models import (
    ParameterDefinition, ParameterValue, Conversation,
    TeamMember, Team, User, CRMRecording
)

import os
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("main")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
_client = OpenAI(api_key=OPENAI_API_KEY)


SYSTEM_PROMPT = """Ты — ИИ-аналитик продаж платформы UpStat. Ты помогаешь РОПу (руководителю отдела продаж) анализировать звонки своей команды.

У тебя есть доступ к данным по 65 параметрам, извлечённым из звонков. Вот основные категории:

УСТАНОВЛЕНИЕ КОНТАКТА: greeting_quality (оценка приветствия), rapport_established (установлен раппорт), opening_clarity (ясность цели звонка), permission_granted (разрешение на разговор), resistance_at_start (сопротивление в начале).

СТРУКТУРА РАЗГОВОРА: structure_followed (соблюдена структура), stage_sequence_correct (правильный порядок этапов), stage_completion_rate (% завершённых этапов), clarity_of_flow (ясность хода), chaos_flag (хаос в разговоре).

ВЫЯВЛЕНИЕ ПОТРЕБНОСТЕЙ: manager_questions_count (кол-во вопросов), open_questions_ratio (% открытых вопросов), needs_identified (потребности выявлены), listening_quality (качество слушания), manager_interruptions (перебивания).

ПРЕЗЕНТАЦИЯ: features_vs_benefits_ratio (функции vs выгоды), benefits_presented (выгоды представлены), benefit_personalization (персонализация), value_linked_to_needs (связка с потребностями), overload_of_information (перегруз), client_interest_during_presentation (интерес клиента).

ВОЗРАЖЕНИЯ: objections_count (кол-во возражений), objection_handled (% обработанных), handling_quality (качество обработки), objection_ignored (проигнорировано), defensive_behavior (защитное поведение).

ЗАКРЫТИЕ: closing_attempt (попытка закрытия), next_step_defined (следующий шаг), next_step_confirmed (шаг подтверждён), client_commitment (обязательства клиента), urgency_created (срочность), deal_momentum (импульс сделки).

ОБЩЕЕ ПОВЕДЕНИЕ: talk_listen_ratio (% речи менеджера), confidence (уверенность), filler_words (слова-паразиты), empathy (эмпатия), over_talking (чрезмерная речь), pressure_behavior (давление), critical_error (критические ошибки).

ДОПОЛНИТЕЛЬНЫЕ: avg_manager_reply_len, avg_client_reply_len, dialogue_density, questions_by_stage, system_identified, problem_identified, consequences_identified, price_devaluation.

В контексте также есть «Реестр звонков» — построчно каждый проанализированный звонок с conversation_id, датой, менеджером, CRM-данными (если есть) и ключевыми параметрами. Используй его для вопросов про конкретные звонки, периоды, сравнение по звонкам.

ПРАВИЛА:
- Отвечай по-русски, кратко и по существу.
- Приводи конкретные цифры и проценты из данных.
- Для ссылок на звонок указывай conversation_id из реестра.
- Давай практические рекомендации.
- Если данных недостаточно — скажи об этом честно.
- Не выдумывай данные, используй только то, что предоставлено в контексте.
"""

# Максимум звонков в реестре за один запрос (защита от переполнения контекста)
ALL_CALLS_LIMIT = 100
ALL_CALLS_DAYS = 90


def _build_team_context(db: Session, user_id: int) -> Dict[str, Any]:
    """Собирает контекст о команде пользователя и доступных данных."""
    owned_teams = db.query(Team).filter(Team.manager_id == user_id).all()
    managed_teams = (
        db.query(Team)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .filter(TeamMember.user_id == user_id, TeamMember.role_in_team == "manager")
        .all()
    )
    seen_ids = set()
    teams = []
    for t in owned_teams + managed_teams:
        if t.id not in seen_ids:
            teams.append(t)
            seen_ids.add(t.id)
    
    if not teams:
        return {"teams": [], "member_ids": [], "total_calls": 0}
    
    team_ids = [t.id for t in teams]
    members = (
        db.query(TeamMember)
        .filter(TeamMember.team_id.in_(team_ids))
        .all()
    )
    member_user_ids = list(set(m.user_id for m in members))
    
    total_calls = (
        db.query(func.count(Conversation.id))
        .filter(Conversation.user_id.in_(member_user_ids))
        .scalar()
    )
    
    return {
        "teams": [{"id": t.id, "name": t.name} for t in teams],
        "member_ids": member_user_ids,
        "total_calls": total_calls or 0,
    }


def _get_aggregated_data(db: Session, member_ids: List[int], days: int = 60) -> str:
    """Агрегирует данные параметров по звонкам команды за указанный период."""
    since = datetime.utcnow() - timedelta(days=days)
    
    from sqlalchemy import case, Integer
    true_count_expr = func.sum(case((ParameterValue.value_bool == True, 1), else_=0).cast(Integer))
    
    results = (
        db.query(
            ParameterDefinition.code,
            ParameterDefinition.title,
            ParameterDefinition.value_type,
            ParameterDefinition.unit,
            func.count(ParameterValue.id).label("total"),
            func.avg(ParameterValue.value_number).label("avg_num"),
            func.min(ParameterValue.value_number).label("min_num"),
            func.max(ParameterValue.value_number).label("max_num"),
            true_count_expr.label("true_count"),
        )
        .join(ParameterValue, ParameterValue.parameter_id == ParameterDefinition.id)
        .join(Conversation, Conversation.id == ParameterValue.conversation_id)
        .filter(
            Conversation.user_id.in_(member_ids),
            ParameterValue.created_at >= since,
        )
        .group_by(ParameterDefinition.code, ParameterDefinition.title, ParameterDefinition.value_type, ParameterDefinition.unit)
        .all()
    )
    
    if not results:
        return "Данных по параметрам пока нет. Нужно проанализировать хотя бы несколько звонков."
    
    lines = [f"Агрегированные данные за последние {days} дней:"]
    for r in results:
        if r.value_type == "number" and r.avg_num is not None:
            lines.append(
                f"- {r.title}: среднее={r.avg_num:.1f}{' ' + r.unit if r.unit else ''}, "
                f"мин={r.min_num:.1f}, макс={r.max_num:.1f}, звонков={r.total}"
            )
        elif r.value_type == "boolean":
            pct = (r.true_count or 0) / r.total * 100 if r.total > 0 else 0
            lines.append(f"- {r.title}: да в {pct:.0f}% случаев ({r.true_count or 0}/{r.total})")
        elif r.value_type == "text":
            lines.append(f"- {r.title}: {r.total} записей")
    
    return "\n".join(lines)


def _get_per_member_data(db: Session, member_ids: List[int], days: int = 60) -> str:
    """Данные в разрезе менеджеров."""
    since = datetime.utcnow() - timedelta(days=days)
    
    results = (
        db.query(
            User.name,
            ParameterDefinition.code,
            ParameterDefinition.title,
            func.count(ParameterValue.id).label("total"),
            func.avg(ParameterValue.value_number).label("avg_num"),
        )
        .join(Conversation, Conversation.user_id == User.id)
        .join(ParameterValue, ParameterValue.conversation_id == Conversation.id)
        .join(ParameterDefinition, ParameterDefinition.id == ParameterValue.parameter_id)
        .filter(
            User.id.in_(member_ids),
            ParameterValue.created_at >= since,
            ParameterDefinition.value_type == "number",
        )
        .group_by(User.name, ParameterDefinition.code, ParameterDefinition.title)
        .order_by(User.name, ParameterDefinition.code)
        .all()
    )
    
    if not results:
        return ""
    
    lines = ["\nДанные по менеджерам:"]
    current_name = None
    for r in results:
        if r.name != current_name:
            current_name = r.name
            lines.append(f"\n  {r.name}:")
        if r.avg_num is not None:
            lines.append(f"    - {r.title}: среднее={r.avg_num:.1f} ({r.total} звонков)")
    
    return "\n".join(lines)


def _get_latest_call_data(db: Session, member_ids: List[int]) -> str:
    """Данные по последнему звонку каждого менеджера (все 11 параметров)."""
    lines = ["\nПоследний звонок каждого менеджера:"]
    found_any = False

    for uid in member_ids:
        user = db.query(User).get(uid)
        if not user:
            continue

        latest_conv = (
            db.query(Conversation)
            .filter(Conversation.user_id == uid)
            .join(ParameterValue, ParameterValue.conversation_id == Conversation.id)
            .order_by(Conversation.created_at.desc())
            .first()
        )
        if not latest_conv:
            continue

        pvs = (
            db.query(ParameterDefinition.title, ParameterDefinition.value_type,
                     ParameterDefinition.unit,
                     ParameterValue.value_number, ParameterValue.value_bool,
                     ParameterValue.value_text)
            .join(ParameterValue, ParameterValue.parameter_id == ParameterDefinition.id)
            .filter(ParameterValue.conversation_id == latest_conv.id)
            .order_by(ParameterDefinition.id)
            .all()
        )
        if not pvs:
            continue

        found_any = True
        lines.append(f"\n  {user.name} (последний звонок: {latest_conv.created_at.strftime('%d.%m.%Y %H:%M')}, \"{latest_conv.title}\"):")
        for p in pvs:
            if p.value_type == "number" and p.value_number is not None:
                lines.append(f"    - {p.title}: {p.value_number:.1f}{' ' + p.unit if p.unit else ''}")
            elif p.value_type == "boolean" and p.value_bool is not None:
                lines.append(f"    - {p.title}: {'Да' if p.value_bool else 'Нет'}")
            elif p.value_type == "text" and p.value_text:
                lines.append(f"    - {p.title}: {p.value_text[:200]}")

    return "\n".join(lines) if found_any else ""


# Короткие подписи для компактной строки в реестре звонков
_PARAM_SHORT = {
    "talk_listen_ratio": "talk%",
    "avg_manager_reply_len": "сл.м",
    "avg_client_reply_len": "сл.к",
    "dialogue_density": "плотн",
    "manager_questions_count": "вопр",
    "questions_by_stage": "этапы",
    "system_identified": "сист",
    "problem_identified": "проб",
    "consequences_identified": "посл",
    "price_devaluation": "цену-",
    "objections_count": "возр",
}


def _format_param_compact(code: str, value_type: str, num, boo, txt, unit: Optional[str]) -> Optional[str]:
    label = _PARAM_SHORT.get(code, code[:8])
    if value_type == "number" and num is not None:
        u = unit or ""
        return f"{label}={num:.1f}{u}"
    if value_type == "boolean" and boo is not None:
        return f"{label}={'да' if boo else 'нет'}"
    if value_type == "text" and txt:
        t = txt.replace("\n", " ")[:80]
        return f"{label}={t}"
    return None


def _get_all_calls_register(db: Session, member_ids: List[int], days: int = ALL_CALLS_DAYS, limit: int = ALL_CALLS_LIMIT) -> str:
    """
    Реестр всех звонков команды, по которым есть извлечённые параметры.
    Компактный формат, чтобы поместить до ~100 звонков в контекст.
    """
    since = datetime.utcnow() - timedelta(days=days)
    has_params = exists().where(ParameterValue.conversation_id == Conversation.id)

    total_q = (
        db.query(func.count(Conversation.id))
        .filter(
            Conversation.user_id.in_(member_ids),
            Conversation.created_at >= since,
            has_params,
        )
        .scalar()
    ) or 0

    convs = (
        db.query(Conversation)
        .filter(
            Conversation.user_id.in_(member_ids),
            Conversation.created_at >= since,
            has_params,
        )
        .order_by(Conversation.created_at.desc())
        .limit(limit)
        .all()
    )

    if not convs:
        return ""

    conv_ids = [c.id for c in convs]
    user_by_id = {u.id: u for u in db.query(User).filter(User.id.in_({c.user_id for c in convs})).all()}

    crm_by_conv: Dict[int, Any] = {}
    for rec in db.query(CRMRecording).filter(CRMRecording.conversation_id.in_(conv_ids)).all():
        crm_by_conv[rec.conversation_id] = rec

    rows = (
        db.query(
            ParameterValue.conversation_id,
            ParameterDefinition.code,
            ParameterDefinition.value_type,
            ParameterDefinition.unit,
            ParameterValue.value_number,
            ParameterValue.value_bool,
            ParameterValue.value_text,
        )
        .join(ParameterDefinition, ParameterDefinition.id == ParameterValue.parameter_id)
        .filter(ParameterValue.conversation_id.in_(conv_ids))
        .order_by(ParameterValue.conversation_id, ParameterDefinition.id)
        .all()
    )

    by_conv: Dict[int, List] = defaultdict(list)
    for r in rows:
        by_conv[r.conversation_id].append(r)

    lines = [
        f"\nРеестр звонков (последние {days} дн., с извлечёнными параметрами):",
        f"Всего таких звонков: {total_q}. В контексте строк: {len(convs)} (новее → старее).",
        "Формат строки: id | дата | менеджер | CRM | параметры (сокращения: talk%=доля речи менеджера, вопр=вопросы, возр=возражения, сист/проб/посл=да/нет, цену-=обесценивание цены).",
        "",
    ]

    for idx, conv in enumerate(convs, start=1):
        mgr = user_by_id.get(conv.user_id)
        mgr_name = mgr.name if mgr else f"user#{conv.user_id}"
        dt = conv.created_at.strftime("%d.%m.%Y %H:%M")
        crm = crm_by_conv.get(conv.id)
        crm_bits = []
        if crm:
            if crm.client_name:
                crm_bits.append(f"кл:{crm.client_name[:30]}")
            if crm.client_phone:
                crm_bits.append(str(crm.client_phone)[:18])
            if crm.manager_name and crm.manager_name != mgr_name:
                crm_bits.append(f"CRM:{crm.manager_name[:20]}")
            if crm.call_date:
                crm_bits.append(f"зв:{crm.call_date.strftime('%d.%m.%y %H:%M')}")
        crm_str = " | ".join(crm_bits) if crm_bits else "—"

        parts = []
        for r in by_conv.get(conv.id, []):
            s = _format_param_compact(
                r.code, r.value_type, r.value_number, r.value_bool, r.value_text, r.unit
            )
            if s:
                parts.append(s)
        params_str = " | ".join(parts) if parts else "(нет параметров)"

        title_short = (conv.title or "")[:50].replace("\n", " ")
        lines.append(f"{idx}. conv_id={conv.id} | {dt} | {mgr_name} | {crm_str} | {title_short}")
        lines.append(f"   {params_str}")

    return "\n".join(lines)


async def get_ai_response(db: Session, user_id: int, question: str) -> str:
    """Формирует ответ ИИ-ассистента на вопрос РОПа."""
    try:
        ctx = _build_team_context(db, user_id)
        
        if not ctx["member_ids"]:
            return ("У вас пока нет команды или участников. "
                    "Создайте команду и пригласите менеджеров, чтобы начать анализировать их звонки.")
        
        if ctx["total_calls"] == 0:
            return ("Пока нет проанализированных звонков в вашей команде. "
                    "Загрузите или синхронизируйте звонки через CRM — после анализа данные появятся здесь.")
        
        aggregated = _get_aggregated_data(db, ctx["member_ids"])
        per_member = _get_per_member_data(db, ctx["member_ids"])
        latest_calls = _get_latest_call_data(db, ctx["member_ids"])
        all_calls = _get_all_calls_register(db, ctx["member_ids"])
        
        team_names = ", ".join(t["name"] for t in ctx["teams"])
        data_context = (
            f"Команды РОПа: {team_names}\n"
            f"Всего звонков в системе: {ctx['total_calls']}\n\n"
            f"{aggregated}"
            f"{per_member}"
            f"{latest_calls}"
            f"{all_calls}"
        )
        
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": f"ДАННЫЕ ИЗ БД:\n{data_context}"},
            {"role": "user", "content": question},
        ]
        
        response = await asyncio.to_thread(
            lambda: _client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                temperature=0.3,
                max_tokens=2000,
            )
        )
        
        return response.choices[0].message.content.strip()
    
    except Exception as e:
        logger.error(f"Ошибка ИИ-ассистента: {e}", exc_info=True)
        return f"Произошла ошибка при обработке вашего запроса. Попробуйте позже."


async def format_response_with_ai(question_label: str, data_dict: dict) -> str:
    """Лёгкий вызов GPT-4o-mini для форматирования предвычисленных данных в текст.
    Используется кнопочной аналитикой (analytics_queries.format_with_ai — основной путь,
    эта функция — альтернативный entrypoint из assistant-модуля).
    """
    from services.analytics_queries import format_with_ai
    return await format_with_ai(question_label, data_dict)
