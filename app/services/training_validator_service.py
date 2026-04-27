"""
AI-валидатор тренировки.
Проверяет транскрипт диалога между менеджером и ИИ-тренером,
оценивает выполнение задач тренировки и даёт разрешение на завершение.
"""

import json
import os
import logging
from typing import Dict, Optional
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

VALIDATION_PROMPT = """Ты — валидатор тренировки по продажам. Твоя задача — проверить, правильно ли менеджер прошёл тренировку.

КОНТЕКСТ ТРЕНИРОВКИ:
- Тема: {training_title}
- Описание проблемы: {training_description}
- Рекомендация: {training_recommendation}
- Этап продаж: {training_stage}

ПРОМПТ ТРЕНИРОВКИ (по нему ИИ-тренер вёл тренировку):
{system_prompt}

ТРАНСКРИПТ ДИАЛОГА (менеджер и ИИ-тренер):
{transcript}

КРИТЕРИИ ОЦЕНКИ:

1. **Прохождение полного цикла** (0-25 баллов):
   - Менеджер прошёл и «неправильный» вариант (увидел как НЕ надо делать)
   - Менеджер прошёл и «правильный» вариант (отработал как НАДО делать)
   - Если промпт предусматривает обе стороны (менеджер→клиент и клиент→менеджер) — проверь это тоже

2. **Понимание техники** (0-25 баллов):
   - Менеджер осознал разницу между правильным и неправильным подходом
   - При обсуждении итогов сделал правильные выводы
   - Показал понимание, ПОЧЕМУ правильная техника работает

3. **Качество исполнения** (0-25 баллов):
   - Менеджер использовал правильные фразы/приёмы в «правильном» варианте
   - Не путал правильный и неправильный подход
   - Реплики были осмысленными, а не формальными отписками

4. **Активное участие** (0-25 баллов):
   - Менеджер давал развёрнутые ответы, а не односложные
   - Не уклонялся от упражнений
   - Задавал уточняющие вопросы или проявлял интерес

ВАЖНО:
- Если транскрипт пустой или слишком короткий (менее 4-5 реплик) — это автоматически 0 баллов
- Если менеджер явно халтурил (односложные ответы, непонимание) — максимум 30 баллов
- Если менеджер прошёл только половину тренировки — максимум 50 баллов
- Валидация должна быть СТРОГОЙ — тренировка нужна для реального улучшения навыков

Верни JSON:
{{
  "score": <число от 0 до 100>,
  "passed": <true если score >= 70>,
  "criteria": {{
    "full_cycle": <0-25>,
    "understanding": <0-25>,
    "execution_quality": <0-25>,
    "active_participation": <0-25>
  }},
  "feedback": "<Краткая обратная связь менеджеру на русском — 2-3 предложения: что получилось, что нужно улучшить>",
  "details": "<Подробный разбор на русском — что именно менеджер сделал правильно и где ошибся>"
}}

Только JSON, без комментариев."""


class TrainingValidatorService:
    """Сервис AI-валидации тренировочного диалога"""

    @staticmethod
    async def validate_training(
        transcript: str,
        training_title: str,
        training_description: str,
        training_recommendation: str,
        training_stage: Optional[str] = None,
        system_prompt: Optional[str] = None
    ) -> Dict:
        """
        Валидирует транскрипт тренировки через GPT.
        
        Returns:
            Dict с полями: score, passed, criteria, feedback, details
        """
        if not transcript or len(transcript.strip()) < 50:
            return {
                "score": 0,
                "passed": False,
                "criteria": {
                    "full_cycle": 0,
                    "understanding": 0,
                    "execution_quality": 0,
                    "active_participation": 0
                },
                "feedback": "Тренировка не пройдена — диалог слишком короткий или отсутствует.",
                "details": "Транскрипт пуст или содержит менее 50 символов. Пройдите тренировку полностью."
            }

        stage_names = {
            "contact": "Вступление в контакт",
            "needs": "Работа с потребностями",
            "presentation": "Презентация",
            "objections": "Работа с возражениями",
            "closing": "Завершение сделки"
        }

        prompt = VALIDATION_PROMPT.format(
            training_title=training_title,
            training_description=training_description,
            training_recommendation=training_recommendation,
            training_stage=stage_names.get(training_stage, training_stage or "Не указан"),
            system_prompt=system_prompt or "(стандартный промпт тренера)",
            transcript=transcript[:8000]
        )

        try:
            client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.3,
                timeout=30.0
            )

            result = json.loads(response.choices[0].message.content)

            score = max(0, min(100, int(result.get("score", 0))))
            result["score"] = score
            result["passed"] = score >= 70

            logger.info(
                f"Валидация тренировки: score={score}, passed={result['passed']}"
            )
            return result

        except Exception as e:
            logger.error(f"Ошибка AI-валидации тренировки: {e}", exc_info=True)
            return {
                "score": 0,
                "passed": False,
                "criteria": {
                    "full_cycle": 0,
                    "understanding": 0,
                    "execution_quality": 0,
                    "active_participation": 0
                },
                "feedback": "Не удалось выполнить валидацию. Попробуйте завершить тренировку ещё раз.",
                "details": f"Ошибка сервиса валидации: {str(e)}"
            }

    @staticmethod
    async def validate_and_complete_training(
        db,
        session_id: int,
        training_id: int,
        transcript: str,
        system_prompt: Optional[str] = None
    ) -> Dict:
        """
        Валидирует тренировку и обновляет статусы в БД.
        Объединяет валидацию + обновление TrainingSession + Training + Plan.
        """
        try:
            from models import TrainingSession, Training, AnalysisTrainingPlan
        except ImportError:
            from app.models import TrainingSession, Training, AnalysisTrainingPlan

        from datetime import datetime

        training = db.query(Training).filter_by(id=training_id).first()
        if not training:
            return {
                "score": 0,
                "passed": False,
                "feedback": "Тренировка не найдена в базе данных.",
                "details": f"training_id={training_id} не существует."
            }

        validation_result = await TrainingValidatorService.validate_training(
            transcript=transcript,
            training_title=training.title,
            training_description=training.description,
            training_recommendation=training.recommendation,
            training_stage=training.stage,
            system_prompt=system_prompt
        )

        score = validation_result["score"]
        passed = validation_result["passed"]

        session = db.query(TrainingSession).filter_by(id=session_id).first()
        if session:
            session.status = "completed"
            session.completed_at = datetime.utcnow()
            session.score = score
            session.feedback = validation_result.get("feedback", "")
            if session.started_at and not session.duration_seconds:
                session.duration_seconds = int(
                    (datetime.utcnow() - session.started_at).total_seconds()
                )

        if training.best_score is None or score > training.best_score:
            training.best_score = score

        if passed:
            training.status = "completed"
            training.completed_at = datetime.utcnow()

            plan = training.plan
            if plan:
                plan.completed_trainings += 1

                try:
                    from services.training_plan_service import TrainingPlanService
                except ImportError:
                    from app.services.training_plan_service import TrainingPlanService
                TrainingPlanService.unlock_next_training(db, plan.id)

                if plan.completed_trainings >= plan.total_trainings:
                    plan.status = "completed"
        else:
            training.status = "available"

        db.commit()

        validation_result["training_completed"] = passed
        validation_result["plan_completed"] = (
            training.plan.status == "completed" if training.plan else False
        )

        logger.info(
            f"Валидация завершена: training_id={training_id}, session_id={session_id}, "
            f"score={score}, passed={passed}"
        )

        return validation_result
