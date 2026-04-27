import json
import os
import logging
from typing import Optional, List, Dict
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy import func
from models import (
    ManagerAction, ActionPattern, TeamMember, Team, User,
    Conversation
)

logger = logging.getLogger(__name__)

STAGES = ["contact", "needs", "presentation", "objections", "closing"]

STAGE_LABELS = {
    "contact": "Вступление в контакт",
    "needs": "Работа с потребностями",
    "presentation": "Презентация",
    "objections": "Работа с возражениями",
    "closing": "Завершение сделки",
}

MIN_CALLS_FOR_PATTERN = 10
CONFIRMATION_THRESHOLD = 0.60


async def extract_manager_actions(
    dialogue_json_str: str, analysis_text: str
) -> Optional[List[Dict]]:
    """GPT извлекает успешные/неуспешные действия менеджера из звонка."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    prompt = f"""Ты — аналитик продаж. На основе диалога менеджера с клиентом и результатов анализа 
выдели конкретные действия/фразы менеджера, которые повлияли на решение клиента.

Для каждого действия определи:
- На каком этапе продаж оно произошло (contact, needs, presentation, objections, closing)
- Что именно сделал или сказал менеджер (конкретная фраза или приём)
- Тип действия: phrase (конкретная фраза), technique (приём/техника), question (вопрос)
- Результат: positive (клиент стал ближе к покупке, заинтересовался, раскрылся) 
  или negative (клиент отдалился от покупки, закрылся, стал сопротивляться)
- Как отреагировал клиент

Этапы продаж:
- "contact" — Вступление в контакт и открытие
- "needs" — Работа с потребностями
- "presentation" — Презентация
- "objections" — Работа с возражениями
- "closing" — Завершение сделки

Результаты анализа:
{analysis_text[:3000]}

Диалог (JSON):
{dialogue_json_str[:3000]}

Верни ТОЛЬКО JSON без комментариев:
{{
  "actions": [
    {{
      "stage": "одно из: contact, needs, presentation, objections, closing",
      "action_text": "Что сделал/сказал менеджер (до 300 символов, суть действия)",
      "action_type": "phrase | technique | question",
      "outcome": "positive | negative",
      "client_reaction": "Как отреагировал клиент (до 200 символов)",
      "confidence": 0.85
    }}
  ]
}}

Выдели от 2 до 6 самых заметных действий. Только те, которые реально повлияли на ход разговора."""

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.3,
            timeout=30.0,
        )
        result = json.loads(response.choices[0].message.content)
        actions = result.get("actions", [])

        valid = []
        for a in actions:
            stage = a.get("stage", "")
            outcome = a.get("outcome", "")
            if stage in STAGES and outcome in ("positive", "negative") and a.get("action_text"):
                a["action_text"] = a["action_text"][:500]
                a["client_reaction"] = (a.get("client_reaction") or "")[:300]
                a["confidence"] = max(0.0, min(1.0, float(a.get("confidence", 0.8))))
                valid.append(a)
        return valid
    except Exception as e:
        logger.error(f"Ошибка извлечения действий менеджера: {e}", exc_info=True)
        return None


def _save_actions(
    db: Session,
    user_id: int,
    conversation_id: int,
    team_id: Optional[int],
    actions: List[Dict],
) -> int:
    """Сохраняет действия менеджера в БД."""
    saved = 0
    for a in actions:
        action = ManagerAction(
            team_id=team_id,
            user_id=user_id,
            conversation_id=conversation_id,
            stage=a["stage"],
            action_text=a["action_text"],
            action_type=a.get("action_type", "phrase"),
            outcome=a["outcome"],
            client_reaction=a.get("client_reaction"),
            confidence=a.get("confidence", 0.8),
        )
        db.add(action)
        saved += 1
    db.commit()
    return saved


def check_and_update_patterns(db: Session, team_id: int) -> List[ActionPattern]:
    """
    Агрегирует действия команды, обновляет паттерны.
    Возвращает список новых confirmed-паттернов (>= 60%).
    """
    total_conversations = (
        db.query(func.count(func.distinct(ManagerAction.conversation_id)))
        .filter(ManagerAction.team_id == team_id)
        .scalar()
    ) or 0

    if total_conversations < MIN_CALLS_FOR_PATTERN:
        return []

    newly_confirmed = []

    for stage in STAGES:
        for outcome in ("positive", "negative"):
            actions = (
                db.query(ManagerAction)
                .filter(
                    ManagerAction.team_id == team_id,
                    ManagerAction.stage == stage,
                    ManagerAction.outcome == outcome,
                )
                .all()
            )
            if not actions:
                continue

            groups = _group_similar_actions(actions)

            for group_text, group_actions in groups.items():
                conv_ids = set(a.conversation_id for a in group_actions)
                occurrence = len(conv_ids)
                pct = round(occurrence / total_conversations * 100, 1)

                pattern = (
                    db.query(ActionPattern)
                    .filter(
                        ActionPattern.team_id == team_id,
                        ActionPattern.stage == stage,
                        ActionPattern.outcome == outcome,
                        ActionPattern.pattern_text == group_text,
                    )
                    .first()
                )

                if pattern:
                    pattern.occurrence_count = occurrence
                    pattern.total_calls = total_conversations
                    pattern.percentage = pct
                    pattern.updated_at = datetime.utcnow()
                    if pct >= CONFIRMATION_THRESHOLD * 100 and pattern.status == "collecting":
                        pattern.status = "confirmed"
                        newly_confirmed.append(pattern)
                else:
                    status = "confirmed" if pct >= CONFIRMATION_THRESHOLD * 100 else "collecting"
                    pattern = ActionPattern(
                        team_id=team_id,
                        stage=stage,
                        pattern_text=group_text,
                        outcome=outcome,
                        occurrence_count=occurrence,
                        total_calls=total_conversations,
                        percentage=pct,
                        status=status,
                    )
                    db.add(pattern)
                    if status == "confirmed":
                        newly_confirmed.append(pattern)

    db.commit()
    return newly_confirmed


def _group_similar_actions(actions: List[ManagerAction]) -> Dict[str, List[ManagerAction]]:
    """
    Простая группировка похожих действий по тексту.
    В будущем можно заменить на GPT-кластеризацию.
    """
    groups: Dict[str, List[ManagerAction]] = {}
    for action in actions:
        key = action.action_text.strip()[:200]
        matched = False
        for existing_key in list(groups.keys()):
            if _texts_similar(key, existing_key):
                groups[existing_key].append(action)
                matched = True
                break
        if not matched:
            groups[key] = [action]
    return groups


def _texts_similar(a: str, b: str) -> bool:
    """Простая проверка похожести (пересечение слов >= 60%)."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return False
    intersection = words_a & words_b
    shorter = min(len(words_a), len(words_b))
    return len(intersection) / shorter >= 0.6 if shorter > 0 else False


def send_patterns_report(db: Session, team_id: int, patterns: List[ActionPattern]):
    """Отправляет отчёт о подтверждённых паттернах владельцу команды на почту."""
    from services.email import _get_smtp_config
    import smtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    team = db.query(Team).filter(Team.id == team_id).first()
    if not team:
        return

    owner = db.query(User).filter(User.id == team.manager_id).first()
    if not owner or not owner.email:
        return

    positive = [p for p in patterns if p.outcome == "positive"]
    negative = [p for p in patterns if p.outcome == "negative"]

    positive_html = ""
    for p in positive:
        stage_label = STAGE_LABELS.get(p.stage, p.stage)
        positive_html += f"""
        <tr>
            <td style="padding: 12px; border-bottom: 1px solid #e5e7eb;">{stage_label}</td>
            <td style="padding: 12px; border-bottom: 1px solid #e5e7eb;">{p.pattern_text}</td>
            <td style="padding: 12px; border-bottom: 1px solid #e5e7eb; color: #16a34a; font-weight: 600;">{p.percentage}%</td>
        </tr>"""

    negative_html = ""
    for p in negative:
        stage_label = STAGE_LABELS.get(p.stage, p.stage)
        negative_html += f"""
        <tr>
            <td style="padding: 12px; border-bottom: 1px solid #e5e7eb;">{stage_label}</td>
            <td style="padding: 12px; border-bottom: 1px solid #e5e7eb;">{p.pattern_text}</td>
            <td style="padding: 12px; border-bottom: 1px solid #e5e7eb; color: #dc2626; font-weight: 600;">{p.percentage}%</td>
        </tr>"""

    total_calls = patterns[0].total_calls if patterns else 0

    html_body = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 700px; margin: 0 auto; padding: 20px;">
        <div style="background: linear-gradient(135deg, #2563eb, #1d4ed8); padding: 30px; border-radius: 12px; text-align: center; margin-bottom: 30px;">
            <h1 style="color: white; margin: 0; font-size: 24px;">Обнаружены паттерны продаж</h1>
            <p style="color: rgba(255,255,255,0.8); margin: 8px 0 0 0;">Команда: {team.name} | Проанализировано звонков: {total_calls}</p>
        </div>
        
        {"<div style='margin-bottom: 30px;'><h2 style='color: #16a34a;'>✅ Работает (успешные приёмы)</h2><table style=\\'width: 100%; border-collapse: collapse;\\'><tr style=\\'background: #f0fdf4;\\'><th style=\\'padding: 12px; text-align: left;\\'>Этап</th><th style=\\'padding: 12px; text-align: left;\\'>Действие</th><th style=\\'padding: 12px; text-align: left;\\'>% звонков</th></tr>" + positive_html + "</table></div>" if positive_html else ""}
        
        {"<div style='margin-bottom: 30px;'><h2 style='color: #dc2626;'>❌ Не работает (вредные приёмы)</h2><table style=\\'width: 100%; border-collapse: collapse;\\'><tr style=\\'background: #fef2f2;\\'><th style=\\'padding: 12px; text-align: left;\\'>Этап</th><th style=\\'padding: 12px; text-align: left;\\'>Действие</th><th style=\\'padding: 12px; text-align: left;\\'>% звонков</th></tr>" + negative_html + "</table></div>" if negative_html else ""}
        
        <div style="background: #f8fafc; padding: 20px; border-radius: 12px; margin-top: 20px;">
            <p style="font-size: 14px; color: #64748b; margin: 0;">
                Эти паттерны выявлены автоматически на основе анализа звонков всей команды.
                Действия, встречающиеся в {int(CONFIRMATION_THRESHOLD * 100)}%+ звонков с одинаковым результатом, считаются подтверждёнными.
            </p>
        </div>
        
        <div style="text-align: center; padding-top: 20px; margin-top: 20px; border-top: 1px solid #e5e7eb;">
            <p style="font-size: 12px; color: #94a3b8;">UpStat — ИИ-аналитика продаж</p>
        </div>
    </body>
    </html>
    """

    config = _get_smtp_config()
    if not config["host"] or not config["user"] or not config["password"]:
        logger.info(f"Patterns report for team {team_id} -> {owner.email} (SMTP not configured)")
        return

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"UpStat: Обнаружены паттерны продаж — {team.name}"
        msg["From"] = f"{config['from_name']} <{config['from_email']}>"
        msg["To"] = owner.email

        text_lines = [f"Обнаружены паттерны продаж — {team.name}", f"Проанализировано звонков: {total_calls}", ""]
        if positive:
            text_lines.append("РАБОТАЕТ:")
            for p in positive:
                text_lines.append(f"  [{STAGE_LABELS.get(p.stage, p.stage)}] {p.pattern_text} — {p.percentage}%")
        if negative:
            text_lines.append("\nНЕ РАБОТАЕТ:")
            for p in negative:
                text_lines.append(f"  [{STAGE_LABELS.get(p.stage, p.stage)}] {p.pattern_text} — {p.percentage}%")

        msg.attach(MIMEText("\n".join(text_lines), "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(config["host"], config["port"]) as server:
            if config["use_tls"]:
                server.starttls()
            server.login(config["user"], config["password"])
            server.send_message(msg)

        for p in patterns:
            p.status = "reported"
            p.reported_at = datetime.utcnow()
        db.commit()

        logger.info(f"Отчёт о паттернах отправлен: {owner.email}, team={team_id}, patterns={len(patterns)}")
    except Exception as e:
        logger.error(f"Ошибка отправки отчёта о паттернах: {e}", exc_info=True)


async def process_manager_actions(
    db: Session,
    user_id: int,
    conversation_id: int,
    dialogue_json_str: str,
    analysis_text: str,
):
    """Главная функция: извлекает действия, сохраняет, проверяет паттерны, шлёт отчёт."""
    team_member = db.query(TeamMember).filter(TeamMember.user_id == user_id).first()
    team_id = team_member.team_id if team_member else None

    actions = await extract_manager_actions(dialogue_json_str, analysis_text)
    if not actions:
        logger.info(f"Действия менеджера не извлечены: user={user_id}, conv={conversation_id}")
        return

    saved = _save_actions(db, user_id, conversation_id, team_id, actions)
    logger.info(f"Сохранено {saved} действий менеджера: user={user_id}, conv={conversation_id}")

    if not team_id:
        return

    newly_confirmed = check_and_update_patterns(db, team_id)
    if newly_confirmed:
        logger.info(f"Обнаружено {len(newly_confirmed)} новых паттернов для team={team_id}")
        try:
            send_patterns_report(db, team_id, newly_confirmed)
        except Exception as e:
            logger.error(f"Ошибка отправки отчёта по паттернам: {e}", exc_info=True)
