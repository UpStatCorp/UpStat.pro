"""
AI-валидатор тренировки.
Проверяет транскрипт диалога между менеджером и ИИ-тренером,
оценивает выполнение задач тренировки и даёт разрешение на завершение.
"""

import asyncio
import json
import os
import logging
from datetime import datetime
from typing import Dict, Optional

from openai import (
    AsyncOpenAI,
    APITimeoutError,
    APIConnectionError,
    InternalServerError,
    RateLimitError,
)

logger = logging.getLogger(__name__)

# Ошибки, при которых имеет смысл повторить запрос (инфраструктурные)
_TRANSIENT = (APITimeoutError, APIConnectionError, InternalServerError, RateLimitError)

# Модель по умолчанию; переопределяется через OPENAI_VALIDATOR_MODEL
_DEFAULT_MODEL = "gpt-4o-mini"

# Лимит длины транскрипта (символы). gpt-4o-mini держит 128k токенов, поэтому
# 8000 было слишком жёстко и срезало ВТОРУЮ половину диалога — а рубрика
# оценивает именно её («правильный вариант» + разбор итогов в конце).
_MAX_TRANSCRIPT_CHARS = int(os.getenv("OPENAI_VALIDATOR_MAX_CHARS", "40000"))

# Порог прохождения (выносим из «магического» 70 в код в одно место)
_PASS_SCORE = int(os.getenv("OPENAI_VALIDATOR_PASS_SCORE", "70"))

# Переиспользуемый async-клиент: создание на каждый вызов пересоздаёт httpx-пул.
_client: Optional[AsyncOpenAI] = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _client

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

_EMPTY_CRITERIA = {
    "full_cycle": 0,
    "understanding": 0,
    "execution_quality": 0,
    "active_participation": 0,
}

_STAGE_NAMES = {
    "contact": "Вступление в контакт",
    "needs": "Работа с потребностями",
    "presentation": "Презентация",
    "objections": "Работа с возражениями",
    "closing": "Завершение сделки",
}


class ValidationTransientError(Exception):
    """Валидация не прошла из-за временной инфраструктурной ошибки.

    Означает: сессию НЕ нужно помечать failed — пользователь может повторить.
    """


class TrainingValidatorService:
    """Сервис AI-валидации тренировочного диалога"""

    @staticmethod
    async def validate_training(
        transcript: str,
        training_title: str,
        training_description: str,
        training_recommendation: str,
        training_stage: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> Dict:
        """Валидирует транскрипт тренировки через GPT.

        Returns:
            Dict с полями: score, passed, criteria, feedback, details

        Raises:
            ValidationTransientError: временная ошибка инфраструктуры после 3 попыток.
                Caller не должен помечать сессию как failed.
        """
        if not transcript or len(transcript.strip()) < 50:
            return {
                "score": 0,
                "passed": False,
                "criteria": _EMPTY_CRITERIA.copy(),
                "feedback": "Тренировка не пройдена — диалог слишком короткий или отсутствует.",
                "details": "Транскрипт пуст или содержит менее 50 символов. Пройдите тренировку полностью.",
            }

        model = os.getenv("OPENAI_VALIDATOR_MODEL", _DEFAULT_MODEL)

        # Усечение с сохранением ХВОСТА (а не начала): концовка диалога несёт
        # «правильный вариант» и выводы, по которым и выставляется балл.
        trimmed = transcript
        if len(trimmed) > _MAX_TRANSCRIPT_CHARS:
            trimmed = trimmed[-_MAX_TRANSCRIPT_CHARS:]

        prompt = VALIDATION_PROMPT.format(
            training_title=training_title,
            training_description=training_description,
            training_recommendation=training_recommendation,
            training_stage=_STAGE_NAMES.get(training_stage, training_stage or "Не указан"),
            system_prompt=system_prompt or "(стандартный промпт тренера)",
            transcript=trimmed,
        )

        client = _get_client()
        last_exc: Optional[Exception] = None

        for attempt in range(3):
            try:
                response = await client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=0.3,
                    timeout=60.0,
                )

                raw = response.choices[0].message.content
                result = json.loads(raw)

                score = max(0, min(100, int(result.get("score", 0))))
                result["score"] = score
                result["passed"] = score >= _PASS_SCORE

                # Логируем usage для наблюдаемости (токены и модель)
                usage = response.usage
                logger.info(
                    "Validation complete",
                    extra={
                        "score": score,
                        "passed": result["passed"],
                        "model": model,
                        "input_tokens": usage.prompt_tokens if usage else None,
                        "output_tokens": usage.completion_tokens if usage else None,
                    },
                )
                return result

            except _TRANSIENT as exc:
                last_exc = exc
                if attempt < 2:
                    delay = 2 ** (attempt + 1)  # 2s → 4s
                    logger.warning(
                        "Transient validation error, retrying",
                        extra={
                            "attempt": attempt + 1,
                            "delay_s": delay,
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        },
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "Validation failed after 3 attempts",
                        extra={"error_type": type(exc).__name__, "error": str(exc)},
                        exc_info=True,
                    )
                    raise ValidationTransientError(
                        f"Validation service unavailable after 3 attempts: {type(exc).__name__}"
                    ) from exc

            except Exception as exc:
                # Non-transient: AuthenticationError, BadRequestError, JSONDecodeError, etc.
                # Не имеет смысла повторять — возвращаем score=0 немедленно.
                logger.error(
                    "Non-transient validation error",
                    extra={"error_type": type(exc).__name__, "error": str(exc)},
                    exc_info=True,
                )
                return {
                    "score": 0,
                    "passed": False,
                    "criteria": _EMPTY_CRITERIA.copy(),
                    "feedback": "Не удалось выполнить валидацию. Обратитесь к администратору.",
                    "details": "Ошибка конфигурации сервиса валидации.",
                }

        # Ветка недостижима, но удовлетворяет type-checker
        raise ValidationTransientError("Unexpected exit from retry loop")  # pragma: no cover

    @staticmethod
    async def _resolve_transcript(
        db, session_id: int, client_transcript: str, expected_messages: int = 0
    ) -> str:
        """Возвращает СЕРВЕРНЫЙ транскрипт из сохранённых сообщений сессии —
        источник правды, который нельзя подделать с клиента.

        Клиентский `client_transcript` используется ТОЛЬКО как fallback, если в
        БД реально нет реплик (например, не голосовая/ручная сессия).

        Защита от гонки последней реплики (план, раздел C): делаем одну короткую
        повторную попытку, если в БД пусто ИЛИ реплик меньше, чем заявил клиент
        (`expected_messages` = user_responses + ai_questions) — фоновые сохранения
        WebSocket могли ещё не закоммититься. Клиентский счётчик используется лишь
        как ХИНТ для ретрая, а не как оценка, поэтому подделать балл им нельзя.
        """
        try:
            from voice_assistant.db_service import VoiceTrainingDBService as _DB
        except ImportError:
            from db_service import VoiceTrainingDBService as _DB

        def _line_count(t: str) -> int:
            return t.count("\n") + 1 if t.strip() else 0

        server = _DB.build_transcript(db, session_id)
        if not server.strip() or (expected_messages and _line_count(server) < expected_messages):
            await asyncio.sleep(0.4)
            server = _DB.build_transcript(db, session_id)

        return server if server.strip() else (client_transcript or "")

    @staticmethod
    async def validate_and_complete_training(
        db,
        session_id: int,
        training_id: int,
        transcript: str,
        system_prompt: Optional[str] = None,
    ) -> Dict:
        """Валидирует тренировку и обновляет статусы в БД.

        При ValidationTransientError (временная недоступность OpenAI):
        - НЕ делает commit — сессия остаётся в статусе "active"
        - Возвращает dict с validation_error=True
        - Пользователь может повторить попытку
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
                "details": f"training_id={training_id} не существует.",
                "training_completed": False,
                "plan_completed": False,
            }

        # Транскрипт берём из БД (серверный источник правды), клиентский — fallback
        effective_transcript = await TrainingValidatorService._resolve_transcript(
            db, session_id, transcript
        )

        # Попытка валидации — может поднять ValidationTransientError
        try:
            validation_result = await TrainingValidatorService.validate_training(
                transcript=effective_transcript,
                training_title=training.title,
                training_description=training.description,
                training_recommendation=training.recommendation,
                training_stage=training.stage,
                system_prompt=system_prompt,
            )
        except ValidationTransientError as exc:
            # Инфраструктурная ошибка: НЕ записываем результат в БД.
            # Сессия остаётся "active" — пользователь может повторить.
            logger.error(
                "Validation transient error — session left active",
                extra={"session_id": session_id, "training_id": training_id, "error": str(exc)},
            )
            return {
                "score": 0,
                "passed": False,
                "criteria": _EMPTY_CRITERIA.copy(),
                "feedback": "Сервис проверки временно недоступен. Попробуйте завершить тренировку ещё раз через несколько минут.",
                "details": "Временная ошибка AI-валидатора.",
                "training_completed": False,
                "plan_completed": False,
                "validation_error": True,
            }

        return TrainingValidatorService.persist_validation(
            db, session_id, training_id, validation_result
        )

    @staticmethod
    def persist_validation(
        db, session_id: int, training_id: int, validation_result: Dict
    ) -> Dict:
        """Идемпотентно записывает результат валидации в БД.

        ВАЖНО: вызывающий код ДОЛЖЕН держать блокировку строки сессии
        (SELECT ... FOR UPDATE). Gate `was_completed` и инкремент плана обязаны
        выполняться атомарно — иначе гонка автозавершения и ручной кнопки дважды
        увеличит plan.completed_trainings и дважды разблокирует следующий шаг.

        Синхронный метод: короткая транзакция, БЕЗ удержания соединения на время
        LLM-вызова (см. план, раздел D). LLM выполняется ДО вызова этого метода.
        """
        try:
            from models import TrainingSession, Training, User
        except ImportError:
            from app.models import TrainingSession, Training, User

        training = db.query(Training).filter_by(id=training_id).first()
        if not training:
            validation_result["training_completed"] = False
            validation_result["plan_completed"] = False
            return validation_result

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

        # Уже завершалась раньше? Тогда счётчик плана не инкрементим повторно
        was_completed = training.status == "completed"

        if passed:
            training.status = "completed"
            training.completed_at = training.completed_at or datetime.utcnow()

            plan = training.plan
            if plan:
                if not was_completed:
                    plan.completed_trainings += 1

                try:
                    from services.training_plan_service import TrainingPlanService
                except ImportError:
                    from app.services.training_plan_service import TrainingPlanService
                TrainingPlanService.unlock_next_training(db, plan.id)

                if plan.completed_trainings >= plan.total_trainings:
                    plan.status = "completed"
        elif not was_completed:
            # Не понижаем статус уже завершённой тренировки при неудачной пересдаче
            training.status = "available"

        db.commit()

        # Фиксируем результаты ДО refresh_streak: он делает повторный db.commit(),
        # который истекает ORM-объекты (expire_on_commit), и последующее обращение
        # к training.plan.status стало бы хрупким lazy-reload.
        plan_completed = bool(training.plan and training.plan.status == "completed")
        completed_user_id = session.user_id if session else None

        validation_result["training_completed"] = passed
        validation_result["plan_completed"] = plan_completed

        # Стрик пользователя (хранимый, TZ-устойчивый) — пересчитываем после коммита
        try:
            from services.streak_service import refresh_streak
        except ImportError:
            from app.services.streak_service import refresh_streak
        try:
            if completed_user_id:
                u = db.query(User).get(completed_user_id)
                if u:
                    refresh_streak(db, u)
        except Exception:
            logger.exception("refresh_streak failed after training completion")

        logger.info(
            "Validation persisted",
            extra={
                "training_id": training_id,
                "session_id": session_id,
                "score": score,
                "passed": passed,
            },
        )

        return validation_result
