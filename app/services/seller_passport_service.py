import json
import os
import logging
from typing import Optional, Dict, List
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


async def evaluate_stage_scores(
    dialogue_json_str: str,
    analysis_text: str,
    research: Optional[object] = None,
) -> Optional[Dict]:
    """GPT оценивает менеджера по 5 этапам продаж и возвращает проценты.

    Если передан research (ResearchLogger), включает расширенный режим:
    модель должна вернуть stage_reasoning по каждому этапу, и результат
    логируется в research-файл.
    """
    from services.ai_provider import get_async_llm_client, model_mini, json_mode_kwargs, extract_json, clamp_max_tokens

    client = get_async_llm_client()

    research_schema = (
        ',\n  "stage_reasoning": {\n'
        '    "contact": "РАЗВЁРНУТОЕ обоснование на 8–12 предложений: что именно увидел в диалоге (2–3 цитаты с таймкодами), какие сильные/слабые места этапа, какие альтернативные баллы (например 45 vs 55) рассматривал, какой решающий фактор склонил весы, что повышало/снижало уверенность",\n'
        '    "needs": "(аналогично — минимум 8–12 предложений с цитатами)",\n'
        '    "presentation": "(аналогично)",\n'
        '    "objections": "(аналогично)",\n'
        '    "closing": "(аналогично)"\n'
        '  }'
    ) if research is not None else ""

    research_note = (
        "\n══════════════════════════════════════════════════════════════════\n"
        "РЕЖИМ ИССЛЕДОВАНИЯ — МАКСИМАЛЬНО РАЗВЁРНУТОЕ ОБОСНОВАНИЕ\n"
        "══════════════════════════════════════════════════════════════════\n"
        "Для КАЖДОГО из 5 этапов в поле stage_reasoning напиши МИНИМУМ\n"
        "8–12 содержательных предложений по схеме:\n"
        "  [1] Что именно увидел: 2–3 цитаты менеджера с таймкодами +\n"
        "      реакции клиента. Не пересказывай — цитируй точно.\n"
        "  [2] Какие сильные стороны на этом этапе: конкретно.\n"
        "  [3] Какие слабые места: конкретно, с цитатами-провалами.\n"
        "  [4] Какие альтернативные баллы рассматривал (например, 45 vs 55):\n"
        "      почему НЕ выше и почему НЕ ниже.\n"
        "  [5] Какой РЕШАЮЩИЙ фактор склонил весы к финальному числу.\n"
        "  [6] Что повышало уверенность, что снижало.\n"
        "НЕ экономь слова. Это инструмент калибровки для коуча.\n"
        "ВАЖНО: stage_reasoning должен быть СТРОКОЙ (одной строкой текста),\n"
        "а не вложенным объектом. Можешь использовать \\n для переносов внутри.\n"
    ) if research is not None else ""

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
{research_note}
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
  "comment": "Краткий комментарий о сильных/слабых сторонах (до 300 символов)"{research_schema}
}}"""

    try:
        # В research-режиме нужно много токенов для развёрнутого stage_reasoning
        # (5 этапов × 8–12 предложений) и больший timeout.
        call_kwargs = {
            "model": model_mini(),
            "messages": [{"role": "user", "content": prompt}],
            **json_mode_kwargs(),
            "temperature": 0.4 if research is not None else 0.3,
            "timeout": 90.0 if research is not None else 30.0,
        }
        if research is not None:
            call_kwargs["max_tokens"] = clamp_max_tokens(8000)
        response = await client.chat.completions.create(**call_kwargs)
        raw_content = response.choices[0].message.content
        result = extract_json(raw_content)
        scores = result.get("stage_scores", {})

        for stage in STAGES:
            val = scores.get(stage, 0)
            scores[stage] = max(0.0, min(100.0, float(val)))

        result["stage_scores"] = scores
        if "overall_score" not in result:
            result["overall_score"] = round(sum(scores.values()) / len(STAGES), 1)

        if research is not None:
            try:
                # Собираем человекочитаемый reasoning по каждому из 5 этапов:
                # почему ИИ поставил именно такой балл.
                stage_reasoning = result.get("stage_reasoning", {}) or {}
                reasoning_lines: List[str] = [
                    f"ИИ оценил работу менеджера по 5 этапам продаж. "
                    f"Общий балл: {result.get('overall_score', '?')}%.",
                    "",
                ]
                for stage in STAGES:
                    label = STAGE_LABELS.get(stage, stage)
                    score = scores.get(stage, 0)
                    reasoning_lines.append(
                        f"━━━ {label}: {int(score)}% ━━━"
                    )
                    sr = stage_reasoning.get(stage)
                    if sr:
                        reasoning_lines.append(f"Почему такой балл: {sr}")
                    else:
                        reasoning_lines.append(
                            "Почему такой балл: (модель не вернула развёрнутое обоснование)"
                        )
                    reasoning_lines.append("")
                if result.get("comment"):
                    reasoning_lines.append(f"Итоговый комментарий ИИ: {result.get('comment')}")
                reasoning_text = "\n".join(reasoning_lines)

                research.capture_stage(
                    stage_name="Паспорт продавца (5 этапов)",
                    model=model_mini(),
                    prompt=prompt,
                    raw_response=raw_content or "",
                    parsed_decisions=result,
                    usage=getattr(response, "usage", None),
                    reasoning_override=reasoning_text,
                )
            except Exception as research_err:  # noqa: BLE001
                logger.warning(f"Research capture (seller_passport) failed: {research_err}")

        return result
    except Exception as e:
        logger.error(f"Ошибка оценки этапов продаж (SellerPassport): {e}", exc_info=True)
        if research is not None:
            try:
                research.capture_note(f"Ошибка оценки паспорта продавца: {e}")
            except Exception:
                pass
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
    research: Optional[object] = None,
) -> Optional[SellerPassport]:
    """Главная функция: оценивает этапы, создаёт снимок, обновляет паспорт."""
    manager_id = _resolve_manager_user_id(db, user_id, conversation_id)

    scores_data = await evaluate_stage_scores(dialogue_json_str, analysis_text, research=research)
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
