import json
import os
import logging
from typing import Optional, Dict
from datetime import datetime
from sqlalchemy.orm import Session
from models import (
    SellerPassport, PassportSnapshot, Training, TrainingSession,
    Conversation, CRMManagerMapping, CRMRecording
)

logger = logging.getLogger(__name__)

STAGES = ["contact", "needs", "presentation", "objections", "closing"]

STAGE_LABELS = {
    "contact": "Вступление в контакт и открытие",
    "needs": "Работа с потребностями",
    "presentation": "Презентация",
    "objections": "Работа с возражениями",
    "closing": "Завершение сделки",
}

STAGE_SCORE_CRITERIA = """
Шкала оценки для каждого этапа (процент вероятности закрытия сделки, который обеспечивает работа менеджера на этом этапе):
- 0–4% — этап полностью пропущен или грубо провален
- 5–10% — попытался, но с критическими ошибками
- 11–25% — базовая работа, много упущений
- 26–50% — средне, есть существенные зоны роста
- 51–70% — хорошо, мелкие замечания
- 71–85% — очень хорошо, незначительные улучшения
- 86–100% — отлично, эталонная работа на этом этапе
"""


async def evaluate_stage_scores(dialogue_json_str: str, analysis_text: str) -> Optional[Dict]:
    """GPT оценивает менеджера по 5 этапам продаж и возвращает проценты."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    prompt = f"""Ты — эксперт по оценке навыков продаж. На основе диалога и результатов анализа 
оцени работу менеджера по каждому из 5 этапов продаж.

Для каждого этапа дай процент вероятности закрытия сделки, который обеспечивает 
работа менеджера именно на этом этапе.

5 этапов продаж:
1. contact — Вступление в контакт и открытие
2. needs — Работа с потребностями  
3. presentation — Презентация
4. objections — Работа с возражениями
5. closing — Завершение сделки

{STAGE_SCORE_CRITERIA}

Результаты анализа по чек-листам:
{analysis_text[:4000]}

Диалог (JSON):
{dialogue_json_str[:3000]}

Верни ТОЛЬКО JSON без комментариев:
{{
  "stage_scores": {{
    "contact": <число от 0 до 100>,
    "needs": <число от 0 до 100>,
    "presentation": <число от 0 до 100>,
    "objections": <число от 0 до 100>,
    "closing": <число от 0 до 100>
  }},
  "overall_score": <среднее по всем этапам, число от 0 до 100>,
  "comment": "Краткий комментарий о сильных/слабых сторонах (до 300 символов)"
}}"""

    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.3,
            timeout=30.0,
        )
        result = json.loads(response.choices[0].message.content)
        scores = result.get("stage_scores", {})

        for stage in STAGES:
            val = scores.get(stage, 0)
            scores[stage] = max(0.0, min(100.0, float(val)))

        result["stage_scores"] = scores
        if "overall_score" not in result:
            result["overall_score"] = round(sum(scores.values()) / len(STAGES), 1)

        return result
    except Exception as e:
        logger.error(f"Ошибка оценки этапов продаж (SellerPassport): {e}", exc_info=True)
        return None


def _resolve_manager_user_id(db: Session, user_id: int, conversation_id: int) -> int:
    """Определяет user_id менеджера: если звонок из CRM — через маппинг, иначе user_id разговора."""
    recording = (
        db.query(CRMRecording)
        .filter(CRMRecording.conversation_id == conversation_id)
        .first()
    )
    if recording and recording.manager_name:
        mapping = (
            db.query(CRMManagerMapping)
            .filter(
                CRMManagerMapping.crm_manager_name == recording.manager_name,
                CRMManagerMapping.user_id.isnot(None),
            )
            .first()
        )
        if mapping:
            return mapping.user_id

    return user_id


def _find_completed_training_since(db: Session, manager_id: int, since: datetime) -> Optional[Training]:
    """Находит последнюю завершённую тренировку менеджера после указанной даты."""
    session = (
        db.query(TrainingSession)
        .filter(
            TrainingSession.user_id == manager_id,
            TrainingSession.completed_at.isnot(None),
            TrainingSession.completed_at > since,
        )
        .order_by(TrainingSession.completed_at.desc())
        .first()
    )
    if session:
        return db.query(Training).filter(Training.id == session.training_id).first()
    return None


async def update_seller_passport(
    db: Session,
    user_id: int,
    conversation_id: int,
    dialogue_json_str: str,
    analysis_text: str,
) -> Optional[SellerPassport]:
    """Главная функция: оценивает этапы, создаёт снимок, обновляет паспорт."""
    manager_id = _resolve_manager_user_id(db, user_id, conversation_id)

    scores_data = await evaluate_stage_scores(dialogue_json_str, analysis_text)
    if not scores_data:
        logger.warning(f"Не удалось получить оценки этапов для user_id={manager_id}, conv={conversation_id}")
        return None

    scores = scores_data["stage_scores"]
    overall = scores_data.get("overall_score", 0)
    comment = scores_data.get("comment", "")

    passport = db.query(SellerPassport).filter(SellerPassport.user_id == manager_id).first()
    is_first_call = passport is None

    if is_first_call:
        passport = SellerPassport(
            user_id=manager_id,
            score_contact=scores["contact"],
            score_needs=scores["needs"],
            score_presentation=scores["presentation"],
            score_objections=scores["objections"],
            score_closing=scores["closing"],
            overall_score=overall,
            total_calls_analyzed=1,
            first_call_at=datetime.utcnow(),
        )
        db.add(passport)
        db.flush()
    else:
        passport.score_contact = scores["contact"]
        passport.score_needs = scores["needs"]
        passport.score_presentation = scores["presentation"]
        passport.score_objections = scores["objections"]
        passport.score_closing = scores["closing"]
        passport.overall_score = overall
        passport.total_calls_analyzed += 1
        passport.last_updated_at = datetime.utcnow()

    # Ищем пройденную тренировку между предыдущим и текущим звонком
    training_before = None
    training_applied = None
    training_delta = None
    training_stage = None

    prev_snapshot = (
        db.query(PassportSnapshot)
        .filter(PassportSnapshot.user_id == manager_id)
        .order_by(PassportSnapshot.created_at.desc())
        .first()
    )

    if prev_snapshot:
        training_before = _find_completed_training_since(db, manager_id, prev_snapshot.created_at)
        if training_before and training_before.stage:
            training_stage = training_before.stage
            stage_field = f"score_{training_stage}"
            prev_score = getattr(prev_snapshot, stage_field, None)
            new_score = scores.get(training_stage, 0)

            if prev_score is not None:
                training_delta = round(new_score - prev_score, 1)
                if training_delta > 0.5:
                    training_applied = "yes"
                elif training_delta >= -0.5:
                    training_applied = "partial"
                else:
                    training_applied = "no"

    snapshot = PassportSnapshot(
        passport_id=passport.id,
        user_id=manager_id,
        conversation_id=conversation_id,
        score_contact=scores["contact"],
        score_needs=scores["needs"],
        score_presentation=scores["presentation"],
        score_objections=scores["objections"],
        score_closing=scores["closing"],
        overall_score=overall,
        training_id_before=training_before.id if training_before else None,
        training_stage=training_stage,
        training_applied=training_applied,
        training_delta=training_delta,
        gpt_comment=comment[:500] if comment else None,
    )
    db.add(snapshot)

    if training_before and training_applied:
        passport.total_trainings_completed += 1

    db.commit()
    db.refresh(passport)

    logger.info(
        f"Паспорт продавца обновлён: user_id={manager_id}, conv={conversation_id}, "
        f"scores={scores}, delta={training_delta}, applied={training_applied}"
    )
    return passport
