import json
import re
from typing import List, Dict, Optional, Tuple
from sqlalchemy.orm import Session
from models import AnalysisTrainingPlan, Training, Message, Attachment
import logging

logger = logging.getLogger(__name__)

STAGE_LABELS = {
    "contact": "Вступление в контакт и открытие",
    "needs": "Работа с потребностями",
    "presentation": "Презентация",
    "objections": "Работа с возражениями",
    "closing": "Завершение сделки",
}


class TrainingPlanService:
    """Сервис для создания планов тренировок на основе анализа"""

    @staticmethod
    def _pick_critical_stage(
        db: Session, report_message_id: int
    ) -> Tuple[Optional[str], Optional[Dict[str, float]]]:
        """
        Определяет самый критичный этап продаж по PassportSnapshot для звонка.

        Возвращает (stage_key, stage_scores_dict) или (None, None) если снимка нет.
        Критичный этап = с минимальным баллом среди 5.
        """
        try:
            from models import PassportSnapshot

            msg = db.query(Message).filter_by(id=report_message_id).first()
            if not msg:
                return None, None

            conversation_id = msg.conversation_id
            snapshot = (
                db.query(PassportSnapshot)
                .filter_by(conversation_id=conversation_id)
                .order_by(PassportSnapshot.id.desc())
                .first()
            )
            if not snapshot:
                logger.info(
                    f"PassportSnapshot для conversation_id={conversation_id} не найден, "
                    f"используем GPT для определения критичного этапа"
                )
                return None, None

            scores = {
                "contact": snapshot.score_contact,
                "needs": snapshot.score_needs,
                "presentation": snapshot.score_presentation,
                "objections": snapshot.score_objections,
                "closing": snapshot.score_closing,
            }

            critical_stage = min(scores, key=lambda k: scores[k])
            logger.info(
                f"Критичный этап по паспорту: {critical_stage} "
                f"({scores[critical_stage]:.1f}%), все баллы: {scores}"
            )
            return critical_stage, scores

        except Exception as e:
            logger.warning(f"Ошибка _pick_critical_stage: {e}")
            return None, None

    @staticmethod
    async def _extract_recommendations_with_gpt(
        analysis_text: str,
        forced_stage: Optional[str] = None,
        stage_scores: Optional[Dict[str, float]] = None,
    ) -> List[Dict]:
        """
        Использует GPT для извлечения рекомендаций из анализа.

        Если forced_stage указан — GPT формулирует рекомендацию именно по этому этапу.
        """
        import os
        from services.ai_provider import get_async_llm_client, model_mini, json_mode_kwargs, extract_json

        client = get_async_llm_client()

        if forced_stage:
            stage_label = STAGE_LABELS.get(forced_stage, forced_stage)
            score_info = ""
            if stage_scores:
                score_info = (
                    "\nТекущие баллы менеджера по этапам (% вероятности закрытия):\n"
                    + "\n".join(
                        f"  - {STAGE_LABELS.get(k, k)}: {v:.1f}%"
                        for k, v in stage_scores.items()
                    )
                )

            prompt = f"""Проанализируй следующий отчёт по звонку.

По данным паспорта продавца самый критичный этап — «{stage_label}» (ключ: "{forced_stage}").{score_info}

Твоя задача: на основе отчёта сформулируй ОДНУ конкретную рекомендацию по улучшению именно этапа «{stage_label}».

Отчёт:
{analysis_text[:4000]}

Выбери самую важную проблему менеджера НА ЭТОМ КОНКРЕТНОМ ЭТАПЕ.

Верни JSON в формате:
{{
  "recommendations": [
    {{
      "title": "Краткое название проблемы на этапе '{stage_label}' (до 100 символов)",
      "issue": "Описание что было не так на этом этапе (до 200 символов)",
      "recommendation": "Конкретная рекомендация как улучшить именно этот этап (до 500 символов)",
      "priority": "high",
      "stage": "{forced_stage}",
      "reason_chosen": "Почему этот этап требует тренировки прямо сейчас (до 200 символов)"
    }}
  ]
}}

ВАЖНО: Верни РОВНО 1 рекомендацию. stage должен быть строго "{forced_stage}". Только JSON, без комментариев."""

        else:
            prompt = f"""Проанализируй следующий отчёт по звонку и определи ОДНУ САМУЮ КРИТИЧНУЮ ошибку менеджера,
которая больше всего повлияла на результат звонка.

Отчёт:
{analysis_text[:3000]}

Выбери именно ту ошибку, исправление которой даст максимальный эффект на продажи.
Учитывай: потерянные возможности, упущенные моменты закрытия, грубые нарушения скрипта,
отсутствие ключевых этапов продажи.

Определи, к какому из 5 этапов продаж относится ошибка:
- "contact" — Вступление в контакт и открытие
- "needs" — Работа с потребностями
- "presentation" — Презентация
- "objections" — Работа с возражениями
- "closing" — Завершение сделки

Верни JSON в формате:
{{
  "recommendations": [
    {{
      "title": "Краткое название проблемы (до 100 символов)",
      "issue": "Описание что было не так (до 200 символов)",
      "recommendation": "Конкретная рекомендация как улучшить (до 500 символов)",
      "priority": "high",
      "stage": "одно из: contact, needs, presentation, objections, closing",
      "reason_chosen": "Почему именно эта ошибка самая критичная (до 200 символов)"
    }}
  ]
}}

ВАЖНО: Верни РОВНО 1 рекомендацию — самую критичную. Только JSON, без комментариев."""

        try:
            response = await client.chat.completions.create(
                model=model_mini(),
                messages=[{"role": "user", "content": prompt}],
                **json_mode_kwargs(),
                timeout=30.0,
            )

            result = extract_json(response.choices[0].message.content)
            recs = result.get("recommendations", [])

            # Гарантируем правильный stage, если был forced
            if forced_stage and recs:
                recs[0]["stage"] = forced_stage

            return recs
        except Exception as e:
            logger.error(f"Ошибка извлечения рекомендаций через GPT: {e}")
            return []

    @staticmethod
    async def _fallback_regex_recommendations(analysis_text: str) -> List[Dict]:
        """
        Последний фолбэк: regex-парсинг текста анализа.
        Используется только если GPT не дал результат.
        stage остаётся None (неизвестен).
        """
        recommendations = []

        patterns = [
            r"(?:Рекомендации|РЕКОМЕНДАЦИИ|Что улучшить|ЧТО УЛУЧШИТЬ)[\s:]*\n(.*?)(?:\n\n|\Z)",
            r"(?:Советы|СОВЕТЫ|Области для улучшения)[\s:]*\n(.*?)(?:\n\n|\Z)",
        ]

        recommendations_text = ""
        for pattern in patterns:
            match = re.search(pattern, analysis_text, re.DOTALL | re.IGNORECASE)
            if match:
                recommendations_text = match.group(1)
                break

        if not recommendations_text:
            lines = analysis_text.split("\n")
            for i, line in enumerate(lines):
                if ("Нет" in line or "Частично" in line) and (
                    "Статус:" in line or "❌" in line or "⚠️" in line
                ):
                    title_match = re.search(
                        r"(?:^|\d+\.)\s*(.+?)(?:\s*[-–—]\s*Статус|$)", line
                    )
                    if title_match:
                        title = title_match.group(1).strip()
                        recommendation = ""
                        for j in range(i + 1, min(i + 5, len(lines))):
                            if lines[j].strip() and not lines[j].startswith(
                                ("Статус:", "Комментарий:", "---")
                            ):
                                recommendation += lines[j].strip() + " "

                        if recommendation:
                            recommendations.append(
                                {
                                    "title": title[:100],
                                    "issue": line,
                                    "recommendation": recommendation.strip()[:500],
                                    "priority": "high" if "Нет" in line else "medium",
                                    "stage": None,
                                }
                            )
        else:
            items = re.split(r"\n\s*\d+\.", recommendations_text)
            for item in items:
                item = item.strip()
                if len(item) > 20:
                    lines = item.split("\n")
                    title = lines[0].split(":")[0].strip()[:100]
                    recommendations.append(
                        {
                            "title": title or "Улучшение навыков продаж",
                            "issue": item[:200],
                            "recommendation": item,
                            "priority": "medium",
                            "stage": None,
                        }
                    )

        priority_order = {"high": 0, "medium": 1, "low": 2}
        recommendations.sort(
            key=lambda r: priority_order.get(r.get("priority", "low"), 2)
        )
        return recommendations[:1]

    @staticmethod
    async def create_training_plan(
        db: Session,
        user_id: int,
        report_message_id: int,
        analysis_text: str,
    ) -> AnalysisTrainingPlan:
        """
        Создаёт план тренировок на основе анализа.

        Логика выбора тренировки:
        1. Берём самый слабый этап из PassportSnapshot (по conversation_id сообщения).
        2. Если снимок есть — GPT формулирует рекомендацию именно по этому этапу.
        3. Если снимка нет — GPT сам определяет критичный этап по тексту анализа.
        4. Последний фолбэк — regex-парсинг (stage = None, тренировка generic).
        """
        recommendations: List[Dict] = []

        # Шаг 1: определяем критичный этап по паспорту продавца
        forced_stage, stage_scores = TrainingPlanService._pick_critical_stage(
            db, report_message_id
        )

        # Шаг 2: GPT формулирует рекомендацию (с forced_stage или без)
        recommendations = await TrainingPlanService._extract_recommendations_with_gpt(
            analysis_text, forced_stage=forced_stage, stage_scores=stage_scores
        )

        # Шаг 3: regex-фолбэк
        if not recommendations:
            logger.warning(
                "GPT не вернул рекомендации, используем regex-фолбэк (stage=None)"
            )
            recommendations = await TrainingPlanService._fallback_regex_recommendations(
                analysis_text
            )

        if not recommendations:
            raise ValueError("Не удалось извлечь рекомендации из анализа")

        plan = AnalysisTrainingPlan(
            user_id=user_id,
            report_message_id=report_message_id,
            title=f"План тренировок #{report_message_id}",
            recommendations_json=json.dumps(recommendations, ensure_ascii=False),
            total_trainings=len(recommendations),
        )

        db.add(plan)
        db.flush()

        for i, rec in enumerate(recommendations):
            training = Training(
                plan_id=plan.id,
                order=i + 1,
                title=rec["title"],
                description=rec["issue"],
                recommendation=rec["recommendation"],
                scenario_type="custom",
                stage=rec.get("stage"),
                status="available",
                checklist_json=json.dumps(
                    TrainingPlanService._generate_checklist(rec), ensure_ascii=False
                ),
            )
            db.add(training)

        db.commit()
        db.refresh(plan)

        stage_key = recommendations[0].get("stage") if recommendations else None
        logger.info(
            f"Создан план тренировок {plan.id} с {len(recommendations)} тренировками, "
            f"этап: {stage_key or 'не определён'}"
        )

        try:
            from services.notification_service import NotificationService

            NotificationService.create_training_ready_notification(
                db=db,
                user_id=user_id,
                plan_id=plan.id,
                report_msg_id=report_message_id,
                trainings_count=len(recommendations),
            )
            logger.info(f"Создано уведомление о готовности плана тренировок {plan.id}")
        except Exception as e:
            logger.error(f"Ошибка создания уведомления: {e}")

        return plan

    @staticmethod
    def _generate_checklist(recommendation: Dict) -> Dict:
        """Генерирует чеклист для тренировки на основе рекомендации"""
        return {
            "categories": [
                {
                    "name": "Цель тренировки",
                    "items": [
                        recommendation["title"],
                        "Применить рекомендацию",
                        "Получить обратную связь от ИИ",
                    ],
                }
            ]
        }

    @staticmethod
    def unlock_next_training(db: Session, plan_id: int):
        """Разблокирует следующую тренировку после завершения текущей"""
        plan = db.query(AnalysisTrainingPlan).filter_by(id=plan_id).first()
        if not plan:
            return

        next_training = (
            db.query(Training)
            .filter_by(plan_id=plan_id, status="locked")
            .order_by(Training.order)
            .first()
        )

        if next_training:
            next_training.status = "available"
            db.commit()
            logger.info(
                f"Разблокирована тренировка {next_training.id} (порядок {next_training.order})"
            )
