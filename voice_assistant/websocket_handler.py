"""
WebSocket обработчик для Azure Voice Live API.
Проксирует соединение между клиентом и Azure Voice Live API.
"""

import asyncio
import base64
import json
import logging
import os
import numpy as np
from datetime import datetime, timezone
from typing import Optional

# Лимит размера одного аудио-чанка (base64). 64 KB в base64 ≈ 48 KB PCM ≈ 1 с при 24 kHz int16.
_MAX_AUDIO_CHUNK_B64 = int(os.getenv("MAX_AUDIO_CHUNK_B64", str(64 * 1024)))

# Максимальная продолжительность голосовой сессии (секунды; дефолт 60 мин).
_MAX_SESSION_SECONDS = int(os.getenv("MAX_VOICE_SESSION_SECONDS", str(60 * 60)))
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from .session_manager import get_session_manager, UserSession
from .db_service import VoiceTrainingDBService, run_db
from .config import (
    USE_AZURE_VOICE_LIVE,
    AZURE_VOICE_LIVE_ENDPOINT,
    AZURE_VOICE_LIVE_API_KEY,
    AZURE_VOICE_LIVE_MODEL,
    AZURE_VOICE_LIVE_API_VERSION,
    AZURE_VOICE_LIVE_VOICE,
    AZURE_VOICE_LIVE_VOICE_FALLBACK,
    AZURE_VOICE_LIVE_VOICE_STYLE,
    AZURE_VOICE_LIVE_VOICE_TEMPERATURE,
    AZURE_VOICE_LIVE_VOICE_RATE,
    resolve_voice_choice,
    voice_key_for,
    voice_choices,
    AZURE_VOICE_LIVE_TRANSCRIPTION_MODEL,
    AZURE_VOICE_LIVE_TRANSCRIPTION_LANGUAGE,
    get_system_prompt,
)
from .azure_voice_live import AzureVoiceLiveConnection, get_azure_token

try:
    from services.pii_redactor import redact_pii
except ImportError:
    from app.services.pii_redactor import redact_pii

try:
    from services.training_stages_service import (
        load_stages,
        strip_tags,
        has_stage_complete,
        has_training_complete,
        build_stage_tools,
        TOOL_COMPLETE_STAGE,
        TOOL_COMPLETE_TRAINING,
        TrainingStage,
    )
except ImportError:
    from app.services.training_stages_service import (
        load_stages,
        strip_tags,
        has_stage_complete,
        has_training_complete,
        build_stage_tools,
        TOOL_COMPLETE_STAGE,
        TOOL_COMPLETE_TRAINING,
        TrainingStage,
    )

logger = logging.getLogger(__name__)


# Требования к языку и тону ответа. Дописываются к инструкциям сессии в обеих
# ветках (одноэтапной и многоэтапной), потому что озвучивает их русский голос.
#
# Блок про тон — не косметика: HD-голоса Azure считывают эмоцию ИЗ ТЕКСТА и
# подстраивают интонацию под неё. Пока модель писала «Привет! Готов? Поехали!»,
# голос честно отыгрывал этот энтузиазм, и тренировка звучала как сказка.
# Замер (3 синтеза одной фразы, разброс длительности): бодрый текст — 10.6%,
# тот же смысл сухим деловым языком — 8.4%. Правка температуры одна проблему
# не решала, потому что половина «эмоций» приходила из самого текста.
_RU_OUTPUT_RULE = (
    "\n\n===== ЯЗЫК ОТВЕТА =====\n"
    "Говори ТОЛЬКО по-русски. Не используй латиницу и английские слова — "
    "твою реплику озвучивает русский синтез речи, и латиница читается неправильно. "
    "Названия и термины передавай по-русски: «сиэрэм» вместо CRM, «имейл» вместо email. "
    "Если по смыслу нужно английское название продукта — произнеси его русскими буквами.\n"
    "\n===== ТОН РЕЧИ =====\n"
    "Пиши СПОКОЙНО и по-деловому — как коллега на рабочей встрече, а не как ведущий "
    "шоу или рассказчик.\n"
    "ЗАПРЕЩЕНО:\n"
    "- Восклицательные знаки. Ставь точку даже там, где просится восклицание.\n"
    "- Бодрые вставки: «Поехали», «Отлично», «Супер», «Здорово», «Класс», «Ну что».\n"
    "- Приподнятые обороты и наигранный энтузиазм.\n"
    "Короткие ровные фразы. Обычная деловая интонация.\n"
)

# Сколько ждать session.updated, прежде чем всё равно начать говорить.
# Раньше здесь стояла безусловная asyncio.sleep(0.4), подобранная на глаз.
_SESSION_CONFIG_TIMEOUT_S = float(os.getenv("VOICE_SESSION_CONFIG_TIMEOUT", "1.5"))


async def _await_session_configured(user_session: UserSession) -> bool:
    """Ждёт подтверждения session.update от Azure. True — дождались.

    По таймауту не падаем: лучше начать тренировку с риском, что конфигурация
    ещё не применилась, чем оставить пользователя в тишине.
    """
    try:
        await asyncio.wait_for(
            user_session.session_configured.wait(), timeout=_SESSION_CONFIG_TIMEOUT_S
        )
        return True
    except asyncio.TimeoutError:
        logger.warning(
            f"⚠️ session.updated не пришёл за {_SESSION_CONFIG_TIMEOUT_S}s — "
            f"начинаем реплику без подтверждения конфигурации"
        )
        return False


def _current_voice(user_session: UserSession) -> str:
    """Голос, которым сессия должна говорить прямо сейчас.

    Приоритет: аварийный фолбэк → выбор пользователя → значение из конфига.
    Нужен потому, что session.update заменяет конфигурацию целиком: при каждой
    переотправке (смена этапа, фолбэк) голос надо подставлять заново.
    """
    if user_session.voice_fallback_used:
        return AZURE_VOICE_LIVE_VOICE_FALLBACK
    return user_session.selected_voice or AZURE_VOICE_LIVE_VOICE


async def _apply_voice_choice(
    azure_connection: "AzureVoiceLiveConnection",
    websocket: WebSocket,
    user_session: UserSession,
    choice: str,
) -> None:
    """Переключает голос по запросу пользователя (сообщение set_voice).

    Клиент присылает только ключ ("male"/"female"); имя голоса берётся из белого
    списка на сервере. Принимать имя голоса с фронта нельзя — им можно было бы
    подставить произвольный, в том числе платный custom-голос.
    """
    voice_name, key = resolve_voice_choice(choice)
    if not voice_name:
        logger.warning(f"⚠️ Неизвестный вариант голоса от клиента: {choice!r}")
        await websocket.send_json({
            "type": "error",
            "message": "Неизвестный вариант голоса.",
        })
        return

    if user_session.selected_voice == voice_name and not user_session.voice_fallback_used:
        return  # уже выбран — молча игнорируем повтор

    user_session.selected_voice = voice_name
    # Пользователь выбрал голос сам — снимаем пометку аварийного отката, чтобы
    # сторож «немого голоса» мог сработать заново уже для нового голоса.
    user_session.voice_fallback_used = False

    if not user_session.session_instructions:
        # Конфигурация сессии ещё не отправлялась: выбор применится при её сборке.
        return

    try:
        await azure_connection.send_session_update(
            instructions=user_session.session_instructions,
            voice_name=voice_name,
            transcription_model=AZURE_VOICE_LIVE_TRANSCRIPTION_MODEL,
            transcription_language=AZURE_VOICE_LIVE_TRANSCRIPTION_LANGUAGE,
            tools=getattr(user_session, "session_tools", None),
            voice_style=AZURE_VOICE_LIVE_VOICE_STYLE,
            voice_temperature=AZURE_VOICE_LIVE_VOICE_TEMPERATURE,
            voice_rate=AZURE_VOICE_LIVE_VOICE_RATE,
        )
    except Exception as e:
        logger.error(f"❌ Не удалось сменить голос на {voice_name}: {e}")
        await websocket.send_json({
            "type": "error",
            "message": "Не удалось сменить голос. Попробуйте ещё раз.",
        })
        return

    logger.info(f"🗣️ Голос переключён пользователем: {key} ({voice_name})")
    # Новый голос применится к СЛЕДУЮЩЕЙ реплике — текущая уже синтезируется
    # старым, прерывать её на полуслове хуже, чем дать договорить.
    await websocket.send_json({
        "type": "voice_changed",
        "voice": key,
        "voice_name": voice_name,
    })


async def _switch_to_fallback_voice(
    azure_connection: "AzureVoiceLiveConnection",
    user_session: UserSession,
) -> None:
    """Пересылает конфигурацию сессии с запасным голосом и повторяет реплику.

    Вызывается, когда основной голос не смог озвучить ответ. Запасной голос —
    обычный neural, поэтому ни temperature, ни style ему не отправляем.
    """
    try:
        await azure_connection.send_session_update(
            instructions=user_session.session_instructions,
            voice_name=AZURE_VOICE_LIVE_VOICE_FALLBACK,
            transcription_model=AZURE_VOICE_LIVE_TRANSCRIPTION_MODEL,
            transcription_language=AZURE_VOICE_LIVE_TRANSCRIPTION_LANGUAGE,
            tools=getattr(user_session, "session_tools", None),
            voice_temperature=None,
            voice_style=None,
        )
    except Exception as e:
        logger.error(f"❌ Не удалось переключиться на запасной голос: {e}")
        return

    # Повторяем ту же реплику — иначе пользователь просто не услышит её.
    last_instructions = getattr(user_session, "last_response_instructions", None)
    if last_instructions:
        try:
            await asyncio.sleep(0.3)  # дать session.update примениться
            await azure_connection.send_response_create(instructions=last_instructions)
            logger.info("🔁 Реплика повторена запасным голосом")
        except Exception as e:
            logger.error(f"❌ Не удалось повторить реплику запасным голосом: {e}")


async def handle_websocket_connection(
    websocket: WebSocket,
    user_id: int,
    training_id: int,
    existing_db_session_id: Optional[int] = None,
):
    """
    Обрабатывает WebSocket подключение для голосовой тренировки с Azure Voice Live API.

    БД НЕ прибивается к соединению: каждая операция выполняется через run_db()
    в собственной короткоживущей сессии вне event loop. Так 100+ сессий не
    исчерпывают пул соединений и не блокируют обработку звука.

    Args:
        websocket: WebSocket соединение
        user_id: ID пользователя
        training_id: ID тренировки
        existing_db_session_id: если передан — пытаемся переиспользовать существующую
                                TrainingSession (реконнект клиента), иначе создаём новую.
    """
    session_manager = get_session_manager()
    user_session: Optional[UserSession] = None
    azure_connection: Optional[AzureVoiceLiveConnection] = None
    
    try:
        # Проверяем, используется ли Azure Voice Live
        if not USE_AZURE_VOICE_LIVE:
            error_msg = (
                "⚠️ Azure Voice Live API не настроен.\n\n"
                "Для работы голосовой тренировки необходимо:\n"
                "1. Установить USE_AZURE_VOICE_LIVE=true в .env файле\n"
                "2. Установить AZURE_VOICE_LIVE_ENDPOINT (URL вашего Azure ресурса)\n"
                "3. Установить AZURE_VOICE_LIVE_API_KEY (API ключ Azure)\n\n"
                "Или установите USE_AZURE_VOICE_LIVE=false для использования локального режима."
            )
            logger.error(error_msg)
            await websocket.send_json({
                "type": "error",
                "message": error_msg
            })
            await websocket.close(code=1008, reason="Azure Voice Live not configured")
            return
        
        if not AZURE_VOICE_LIVE_ENDPOINT:
            error_msg = (
                "⚠️ AZURE_VOICE_LIVE_ENDPOINT не настроен.\n\n"
                "Установите переменную окружения AZURE_VOICE_LIVE_ENDPOINT в .env файле.\n"
                "Пример: AZURE_VOICE_LIVE_ENDPOINT=https://your-resource.cognitiveservices.azure.com/"
            )
            logger.error(error_msg)
            await websocket.send_json({
                "type": "error",
                "message": error_msg
            })
            await websocket.close(code=1008, reason="Azure endpoint not configured")
            return
        
        # Создаём изолированную сессию для пользователя
        user_session = await session_manager.create_session(user_id, training_id)
        
        if not user_session:
            await websocket.send_json({
                "type": "error",
                "message": "⚠️ Достигнут лимит одновременных пользователей. Попробуйте позже."
            })
            await websocket.close(code=1008, reason="Server capacity reached")
            return
        
        # Создаём запись в БД (или переиспользуем существующую при реконнекте).
        # Вся работа — в одной короткоживущей сессии через run_db (вне event loop).
        ws_session_id = user_session.session_id

        def _reconnect_or_create(s):
            try:
                from models import TrainingSession  # type: ignore
            except ImportError:
                from app.models import TrainingSession  # type: ignore

            if existing_db_session_id:
                existing = (
                    s.query(TrainingSession)
                    .filter(TrainingSession.id == existing_db_session_id)
                    .first()
                )
                if (
                    existing
                    and existing.user_id == user_id
                    and existing.training_id == training_id
                    and existing.status in ("active", "in_progress")
                    and existing.completed_at is None
                ):
                    existing.websocket_session_id = ws_session_id
                    s.commit()
                    return existing.id, True

            new_id = VoiceTrainingDBService.create_training_session(
                s, user_id, training_id, ws_session_id
            )
            return new_id, False

        db_session_id, is_reconnect = await run_db(_reconnect_or_create)
        if is_reconnect:
            logger.info(
                f"🔁 Реконнект: переиспользуем TrainingSession id={db_session_id} "
                f"для user_id={user_id}, training_id={training_id}"
            )

        user_session.db_session_id = db_session_id
        user_session.websocket = websocket

        if is_reconnect:
            logger.info(
                f"✅ Пользователь {user_id} переподключён к тренировке {training_id}, "
                f"session={user_session.session_id} (db_session_id={db_session_id})"
            )
        else:
            logger.info(
                f"✅ Пользователь {user_id} подключён к тренировке {training_id}, "
                f"session={user_session.session_id}"
            )
        
        # Получаем Azure токен (если не используется API key)
        azure_token = None
        if not AZURE_VOICE_LIVE_API_KEY:
            logger.info("API ключ не указан, пытаемся получить Azure AD токен")
            azure_token = await get_azure_token(AZURE_VOICE_LIVE_ENDPOINT)
            if not azure_token:
                await websocket.send_json({
                    "type": "error",
                    "message": "⚠️ Не удалось получить Azure токен. Проверьте настройки Azure."
                })
                await websocket.close(code=1008, reason="Azure authentication failed")
                return
            logger.info("✅ Azure AD токен получен")
        else:
            logger.info("Используется API ключ для аутентификации")
            if len(AZURE_VOICE_LIVE_API_KEY) < 10:
                logger.warning(f"⚠️ API ключ слишком короткий ({len(AZURE_VOICE_LIVE_API_KEY)} символов), возможно неправильный")
        
        # Подключаемся к Azure Voice Live API
        logger.info(f"🔌 Подключение к Azure Voice Live: endpoint={AZURE_VOICE_LIVE_ENDPOINT[:50]}..., model={AZURE_VOICE_LIVE_MODEL}")
        try:
            azure_connection = AzureVoiceLiveConnection(
                endpoint=AZURE_VOICE_LIVE_ENDPOINT,
                api_key=AZURE_VOICE_LIVE_API_KEY,
                token=azure_token,
                api_version=AZURE_VOICE_LIVE_API_VERSION,
                model=AZURE_VOICE_LIVE_MODEL
            )
            
            logger.info("📡 Вызываю azure_connection.connect()...")
            await azure_connection.connect()
            logger.info("✅ Azure Voice Live подключен успешно")
        except ConnectionError as e:
            logger.error(f"❌ Ошибка подключения к Azure: {e}", exc_info=True)
            await websocket.send_json({
                "type": "error",
                "message": f"⚠️ Не удалось подключиться к Azure Voice Live API: {str(e)}"
            })
            await websocket.close(code=1011, reason="Azure connection failed")
            return
        except Exception as e:
            logger.error(f"❌ Неожиданная ошибка при подключении к Azure: {e}", exc_info=True)
            await websocket.send_json({
                "type": "error",
                "message": f"⚠️ Ошибка подключения к Azure Voice Live API: {str(e)}"
            })
            await websocket.close(code=1011, reason="Azure connection error")
            return
            await websocket.send_json({
                "type": "error",
                "message": f"⚠️ Не удалось подключиться к Azure: {str(e)}"
            })
            await websocket.close(code=1008, reason="Azure connection failed")
            return
        except Exception as e:
            logger.error(f"Неожиданная ошибка подключения к Azure: {e}", exc_info=True)
            await websocket.send_json({
                "type": "error",
                "message": f"⚠️ Ошибка подключения к Azure: {str(e)}"
            })
            await websocket.close(code=1011, reason="Azure connection error")
            return
        
        # Формируем промпт с учётом конкретной тренировки.
        # Если для Training.stage есть многоэтапная конфигурация
        # (папка app/static/docs/trainings/<stage>/stage_*.txt) — используем её,
        # иначе работаем по старой одно-промптовой схеме.
        # Определяем локаль пользователя (EN только для TRAIN_GLOBAL, иначе RU).
        # Строгий gate: влияет на промпты только если EN-контент явно создан.
        # Локаль пользователя + поля тренировки — одной короткой сессией,
        # извлекаем ТОЛЬКО плоские значения (ORM-объекты detached после закрытия).
        def _load_setup(s):
            locale = "ru"
            try:
                try:
                    from models import User
                except ImportError:
                    from app.models import User
                try:
                    from services.i18n_service import resolve_locale
                except ImportError:
                    from app.services.i18n_service import resolve_locale
                u = s.query(User).filter_by(id=user_id).first()
                if u is not None:
                    locale = resolve_locale(u)
            except Exception as e:
                logger.warning(f"⚠️ Не удалось определить локаль пользователя: {e}")

            info = None
            try:
                try:
                    from models import Training
                except ImportError:
                    from app.models import Training
                tr = s.query(Training).filter_by(id=training_id).first()
                if tr is not None:
                    info = {
                        "title": tr.title,
                        "description": tr.description,
                        "recommendation": tr.recommendation,
                        "stage": tr.stage,
                    }
            except Exception as e:
                logger.warning(f"⚠️ Не удалось загрузить данные тренировки: {e}")
            return locale, info

        user_locale, training_info = await run_db(_load_setup)

        session_instructions = get_system_prompt(user_locale)

        stages: list = []
        if training_info and training_info.get("stage"):
            try:
                stages = load_stages(training_info["stage"], locale=user_locale)
            except Exception as e:
                logger.warning(f"⚠️ Ошибка загрузки этапов тренировки: {e}")

        if stages:
            user_session.stages = stages
            user_session.current_stage_index = 0
            session_instructions = stages[0].prompt
            logger.info(
                f"🎯 Многоэтапная тренировка '{training_info['stage']}': "
                f"{len(stages)} этапов, стартуем с этапа #{stages[0].number} "
                f"(роль ИИ: {stages[0].ai_role})"
            )
        elif training_info and training_info.get("recommendation"):
            training_context = (
                f"\n\n===== КОНТЕКСТ ТЕКУЩЕЙ ТРЕНИРОВКИ =====\n"
                f"Тема тренировки: {training_info['title']}\n"
                f"Проблема менеджера: {training_info['description']}\n"
                f"Что нужно отработать: {training_info['recommendation']}\n"
                f"Этап продаж: {training_info['stage'] or 'не указан'}\n"
                f"========================================\n\n"
                f"ВАЖНО: Адаптируй тренировку именно под эту проблему. "
                f"Используй общую структуру тренировки из основного промпта, "
                f"но примеры и ситуации подбирай под тему \"{training_info['title']}\"."
            )
            session_instructions = get_system_prompt(user_locale) + training_context
            logger.info(f"📋 Промпт дополнен контекстом тренировки: {training_info['title']}")
        
        # Для многоэтапных тренировок регистрируем tool-функции,
        # через которые ИИ будет сигнализировать завершение этапа/тренировки
        # (это скрытый канал — вызовы не озвучиваются голосом).
        # Язык вывода. Голос ru-RU озвучивает латиницу скверно ("CRM" читается
        # по-английски посреди русской фразы, англицизмы звучат чужеродно), а до
        # перехода на русский голос это было не слышно, поэтому требования не было.
        # Добавляем к ЛЮБОЙ ветке выше — включая многоэтапные тренировки, где
        # session_instructions берутся из файла этапа и SYSTEM_PROMPT не участвует.
        session_instructions = session_instructions + _RU_OUTPUT_RULE

        session_tools = build_stage_tools() if user_session.stages else None

        # Инструкции и tools кладём на сессию: они понадобятся, если Azure
        # отклонит основной голос и конфигурацию придётся переслать с запасным
        # (см. обработку error в receive_from_azure).
        user_session.session_instructions = session_instructions
        user_session.session_tools = session_tools

        # Отправляем конфигурацию сессии
        logger.info("Отправка конфигурации сессии в Azure...")
        initial_voice = _current_voice(user_session)
        await azure_connection.send_session_update(
            instructions=session_instructions,
            voice_name=initial_voice,
            transcription_model=AZURE_VOICE_LIVE_TRANSCRIPTION_MODEL,
            transcription_language=AZURE_VOICE_LIVE_TRANSCRIPTION_LANGUAGE,
            tools=session_tools,
            voice_style=AZURE_VOICE_LIVE_VOICE_STYLE,
            voice_temperature=AZURE_VOICE_LIVE_VOICE_TEMPERATURE,
            voice_rate=AZURE_VOICE_LIVE_VOICE_RATE,
        )
        logger.info(f"✅ Конфигурация сессии отправлена в Azure (голос: {initial_voice})")
        
        # Отправляем подтверждение подключения
        await websocket.send_json({
            "type": "connected",
            "session_id": user_session.session_id,
            "db_session_id": user_session.db_session_id,
            "message": "✅ Подключение установлено",
            # Начальное состояние переключателя голоса, чтобы UI показал
            # реально активный голос, а не догадывался.
            "voice": voice_key_for(initial_voice),
            "voice_options": voice_choices(),
        })
        logger.info("✅ Подтверждение подключения отправлено клиенту")

        # Одноэтапная тренировка: ИИ начинает первым, чтобы пользователь слышал отклик.
        #
        # ⚠️ КРИТИЧНО: в Realtime API поле response.instructions НЕ дополняет, а ПОЛНОСТЬЮ
        # ЗАМЕНЯЕТ session.instructions для этого ответа ("If these are set, they will
        # override the Session's configuration for this Response only"). Раньше сюда
        # уходила только короткая фраза «начни тренировку… выступи как тренер-инструктор» —
        # модель генерировала первую реплику ВООБЩЕ БЕЗ промпта тренировки и по-русски
        # понимала «тренировку» буквально (звала на разминку и наклоны головы), после
        # чего фитнес-персона закреплялась в истории диалога на всю сессию.
        # Поэтому в триггер кладём полный промпт сессии + задачу на текущую реплику.
        if not user_session.stages:
            try:
                # Ждём подтверждения конфигурации (session.updated), а не слепую паузу:
                # реплика, отправленная до применения session.update, генерируется по
                # пустой конфигурации — ровно то, из-за чего появлялась «разминка».
                await _await_session_configured(user_session)
                kickoff_instructions = (
                    session_instructions
                    + "\n\n===== ЧТО СДЕЛАТЬ ПРЯМО СЕЙЧАС =====\n"
                    "Это твоя первая реплика в сессии. Кратко поприветствуй пользователя "
                    "на русском языке и начни тренировку строго по инструкциям выше. "
                    "Сначала выступи как тренер-инструктор.\n"
                    "Речь идёт ИСКЛЮЧИТЕЛЬНО о тренировке навыков продаж по инструкциям выше — "
                    "никакой физкультуры, разминок и упражнений для тела."
                )
                # Запоминаем: если голос окажется «немым», сторож повторит эту же
                # реплику запасным голосом.
                user_session.last_response_instructions = kickoff_instructions
                await azure_connection.send_response_create(instructions=kickoff_instructions)
                logger.info("🎙️ Запрошен стартовый ответ ИИ (с полным промптом сессии)")
            except Exception as e:
                logger.warning(f"⚠️ Не удалось запустить стартовую реплику ИИ: {e}")
        
        # Если тренировка многоэтапная — сразу сообщаем клиенту
        # стартовый этап и роль ИИ, чтобы UI отрисовал бейдж/прогресс.
        if user_session.stages:
            first_stage = user_session.stages[0]
            await websocket.send_json({
                "type": "stage_changed",
                "stage_number": first_stage.number,
                "total_stages": len(user_session.stages),
                "ai_role": first_stage.ai_role,
                "ai_role_description": first_stage.ai_role_description,
                "training_type": first_stage.training_type,
            })
        
        # Запускаем задачу для получения сообщений от Azure
        azure_receive_task = asyncio.create_task(
            receive_from_azure(azure_connection, websocket, user_session)
        )
        
        # Основной цикл обработки сообщений от клиента
        session_start = datetime.now(timezone.utc)
        try:
            async for message in websocket.iter_text():
                # Cap по длительности сессии
                elapsed = (datetime.now(timezone.utc) - session_start).total_seconds()
                if elapsed > _MAX_SESSION_SECONDS:
                    logger.warning(f"Session {user_id} exceeded max duration ({_MAX_SESSION_SECONDS}s), closing")
                    await websocket.send_json({"type": "error", "message": "Максимальная продолжительность тренировки достигнута."})
                    break

                try:
                    data = json.loads(message) if message else {}
                except (json.JSONDecodeError, ValueError):
                    logger.warning(f"Invalid JSON from user {user_id}: {message[:100]}")
                    continue

                msg_type = data.get("type")

                if msg_type == "set_voice":
                    # Переключение голоса тренера из настроек. Работает и до
                    # начала разговора, и посреди тренировки — новый голос
                    # применяется со следующей реплики ИИ.
                    await _apply_voice_choice(
                        azure_connection, websocket, user_session, data.get("voice")
                    )

                elif msg_type == "input_audio_buffer.append":
                    # Получили аудио чанк в формате input_audio_buffer.append (как в оригинале)
                    audio_base64 = data.get("audio", "")
                    if audio_base64:
                        if len(audio_base64) > _MAX_AUDIO_CHUNK_B64:
                            logger.warning(f"Audio chunk too large ({len(audio_base64)} b64 bytes) from user {user_id}, skipping")
                            continue
                        logger.debug(f"Audio chunk from user {user_id}: {len(audio_base64)} b64 bytes")
                        try:
                            # Проверяем что соединение с Azure активно
                            if not azure_connection.is_connected:
                                logger.warning("Соединение с Azure разорвано, пропускаем аудио")
                                await websocket.send_json({
                                    "type": "error",
                                    "message": "Соединение с Azure прервано. Переподключение..."
                                })
                                break
                            
                            # Клиент отправляет int16 PCM (AudioWorklet уже конвертировал)
                            # Просто передаем в Azure как есть
                            await azure_connection.send_audio(audio_base64)
                            logger.debug(f"Отправлен аудио чанк в Azure")
                        except ConnectionError as e:
                            logger.error(f"Соединение с Azure разорвано: {e}")
                            await websocket.send_json({
                                "type": "error",
                                "message": "Соединение с Azure прервано"
                            })
                            break
                        except Exception as e:
                            logger.error(f"Ошибка отправки аудио в Azure: {e}", exc_info=True)
                
                elif msg_type == "audio" or msg_type == "audio_data":
                    # Старый формат для обратной совместимости
                    audio_base64 = data.get("audio") or data.get("audio_data", "")
                    if audio_base64:
                        if len(audio_base64) > _MAX_AUDIO_CHUNK_B64:
                            logger.warning(f"Audio chunk (legacy) too large ({len(audio_base64)}) from user {user_id}, skipping")
                            continue
                        logger.debug(f"Audio chunk (legacy) from user {user_id}: {len(audio_base64)} b64 bytes")
                        try:
                            if not azure_connection.is_connected:
                                logger.warning("Соединение с Azure разорвано, пропускаем аудио")
                                break
                            
                            # Конвертируем если нужно (для обратной совместимости)
                            audio_bytes = base64.b64decode(audio_base64)
                            if len(audio_bytes) % 2 == 0:
                                # Вероятно int16
                                await azure_connection.send_audio(audio_base64)
                            else:
                                # Конвертируем float32 -> int16
                                audio_float32 = np.frombuffer(audio_bytes, dtype=np.float32)
                                audio_float32 = np.clip(audio_float32, -1.0, 1.0)
                                audio_int16 = np.round(audio_float32 * 32767).astype(np.int16)
                                audio_int16_base64 = base64.b64encode(audio_int16.tobytes()).decode('utf-8')
                                await azure_connection.send_audio(audio_int16_base64)
                        except Exception as e:
                            logger.error(f"Ошибка отправки аудио в Azure: {e}", exc_info=True)
                
                elif msg_type == "stop":
                    # Пользователь нажал стоп
                    await handle_stop(user_session, websocket, azure_connection)
                
                elif msg_type == "response.cancel":
                    # Клиент хочет прервать текущий ответ ИИ
                    response_id = data.get("response_id")
                    if response_id:
                        logger.info(f"⛔ Получен запрос на отмену ответа от клиента (response_id: {response_id})")
                        try:
                            # Отправляем response.cancel в Azure
                            await azure_connection.send_response_cancel(response_id)
                            logger.info(f"✅ Запрос на отмену отправлен в Azure для response_id: {response_id}")
                            
                            # Подтверждаем клиенту
                            await websocket.send_json({
                                "type": "status",
                                "status": "cancelling",
                                "response_id": response_id,
                                "message": "Прерывание ответа..."
                            })
                        except Exception as e:
                            logger.error(f"❌ Ошибка отправки response.cancel в Azure: {e}", exc_info=True)
                            await websocket.send_json({
                                "type": "error",
                                "message": f"Не удалось прервать ответ: {str(e)}"
                            })
                    else:
                        logger.warning("⚠️ Получен response.cancel без response_id")
                        await websocket.send_json({
                            "type": "error",
                            "message": "response_id не указан для отмены"
                        })
                
                elif msg_type == "text":
                    # Текстовый запрос (для тестирования)
                    # Azure Voice Live не поддерживает текстовые запросы напрямую
                    # Можно отправить как аудио или пропустить
                    logger.warning("Текстовые запросы не поддерживаются Azure Voice Live API")
                
                elif msg_type == "ping":
                    # Heartbeat от клиента — отвечаем pong, чтобы клиент знал,
                    # что соединение живое (используется auto-reconnect логикой).
                    try:
                        await websocket.send_json({
                            "type": "pong",
                            "ts": data.get("ts"),
                        })
                    except Exception as e:
                        logger.warning(f"⚠️ Не удалось отправить pong: {e}")

                elif msg_type == "end_session":
                    # Завершение сессии
                    await handle_end_session(user_session, websocket, azure_connection)
                    break
        
        except WebSocketDisconnect as wsd:
            logger.info(f"🔌 Пользователь {user_id} отключился (code={wsd.code}, reason={wsd.reason!r})")
        
        finally:
            # Отменяем задачу получения сообщений
            azure_receive_task.cancel()
            try:
                await azure_receive_task
            except asyncio.CancelledError:
                pass
    
    except Exception as e:
        logger.error(f"❌ Ошибка WebSocket для user_id={user_id}: {e}", exc_info=True)
        
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"Ошибка сервера: {str(e)}"
            })
        except:
            pass
    
    finally:
        # Закрываем Azure соединение
        if azure_connection:
            await azure_connection.close()

        # Закрываем сессию
        if user_session:
            # Дренируем незавершённые фоновые сохранения, затем помечаем сессию
            # прерванной — но ТОЛЬКО если она ещё не была завершена (guard внутри
            # abort_training_session не даст перетереть "completed").
            if user_session.db_session_id:
                try:
                    await _drain_saves(user_session)
                    await run_db(
                        VoiceTrainingDBService.abort_training_session,
                        user_session.db_session_id,
                    )
                except Exception:
                    logger.warning("abort on disconnect failed", exc_info=True)

            await session_manager.close_session(user_session.session_id)

        try:
            await websocket.close()
        except:
            pass


async def receive_from_azure(
    azure_connection: AzureVoiceLiveConnection,
    websocket: WebSocket,
    user_session: UserSession,
):
    """
    Получает сообщения от Azure Voice Live API и отправляет их клиенту.

    Записи в БД выполняются фоном через _fire_save (каждая в своей короткой
    сессии), а не на общей долгоживущей сессии.

    Args:
        azure_connection: Соединение с Azure
        websocket: WebSocket соединение с клиентом
        user_session: Сессия пользователя
    """
    pending_user_transcript = ""
    current_response_id = None
    current_response_text = ""
    response_transcripts = {}
    # Действие, которое нужно выполнить после того как ИИ доиграет
    # текущую реплику (audio.done). Возможные значения:
    #   None / "next_stage" / "complete_training"
    pending_stage_action = None
    # Счётчик аудио-чанков текущей реплики — питает сторож «немого голоса»
    # в обработчике response.audio.done.
    audio_deltas_in_response = 0

    try:
        while azure_connection.is_connected:
            try:
                message = await azure_connection.recv(timeout=0.5)
                if not message:
                    # Проверяем что соединение еще активно
                    if not azure_connection.is_connected:
                        logger.warning("Соединение с Azure разорвано в receive_from_azure")
                        break
                    continue
            except Exception as recv_error:
                logger.error(f"Ошибка получения сообщения от Azure: {recv_error}")
                if not azure_connection.is_connected:
                    break
                continue
            
            try:
                event = json.loads(message)
                event_type = event.get("type")
                
                # Тип события — только на DEBUG: на 100 сессиях INFO по каждому
                # событию = потоп логов, а полный дамп события содержит сырой
                # (НЕ редактированный) транскрипт — не пишем его ради приватности.
                logger.debug(f"📨 Событие от Azure: {event_type}")
                
                # Проверяем что WebSocket с клиентом еще открыт
                try:
                    # Простая проверка - пытаемся отправить пустое сообщение (не отправится, но проверит состояние)
                    pass  # Пропускаем проверку, так как она может быть дорогой
                except:
                    logger.warning("WebSocket с клиентом закрыт")
                    break
                
                if event_type == "session.created":
                    logger.info("✅ Сессия Azure создана")
                    # Проксируем событие клиенту
                    await websocket.send_json({
                        "type": "session.created"
                    })

                elif event_type == "session.updated":
                    # Подтверждение, что Azure ПРИНЯЛА конфигурацию сессии.
                    # Без этого сигнала стартовая реплика уходила по таймеру и могла
                    # сгенерироваться до применения промпта и голоса.
                    accepted_voice = (event.get("session") or {}).get("voice") or {}
                    logger.info(
                        f"✅ Конфигурация сессии принята Azure "
                        f"(голос: {accepted_voice.get('name')}, "
                        f"style={accepted_voice.get('style')}, "
                        f"rate={accepted_voice.get('rate')})"
                    )
                    user_session.session_configured.set()

                elif event_type == "input_audio_buffer.speech_started":
                    logger.info("🎤 Обнаружена речь пользователя (возможное прерывание)")
                    # Проксируем событие напрямую клиенту (как в оригинале)
                    await websocket.send_json({
                        "type": "input_audio_buffer.speech_started"
                    })
                
                elif event_type == "conversation.item.input_audio_transcription.completed":
                    # Транскрипция речи пользователя завершена
                    user_transcript = event.get("transcript", "")
                    if user_transcript:
                        user_transcript = redact_pii(user_transcript)
                        logger.debug(f"📝 Распознано: '{user_transcript}'")
                        
                        # Отправляем клиенту
                        await websocket.send_json({
                            "type": "user_text",
                            "text": user_transcript
                        })
                        
                        # Сохраняем в БД фоном (своя короткая сессия), задачу трекаем
                        # чтобы дренировать перед завершением сессии.
                        _fire_save(user_session, "user", user_transcript)

                        pending_user_transcript = ""
                
                elif event_type == "conversation.item.input_audio_transcription.delta":
                    # Частичная транскрипция
                    delta = event.get("delta", "")
                    if delta:
                        pending_user_transcript += delta
                
                elif event_type == "response.created":
                    # ИИ начал генерировать ответ
                    response_id = event.get("response", {}).get("id") or event.get("response_id") or event.get("item_id")
                    current_response_id = response_id
                    current_response_text = ""
                    
                    logger.info(f"🤖 ИИ начал генерировать ответ (response_id: {response_id})")
                    
                    # Передаем response_id клиенту для возможности прерывания
                    await websocket.send_json({
                        "type": "status",
                        "status": "thinking",
                        "message": "🤔 Думаю...",
                        "response_id": response_id
                    })
                
                elif event_type == "response.audio_transcript.delta":
                    # Частичный текст ответа ИИ
                    delta = event.get("delta", "")
                    response_id = event.get("response_id") or event.get("item_id")
                    
                    if delta and response_id:
                        if response_id not in response_transcripts:
                            response_transcripts[response_id] = ""
                        response_transcripts[response_id] += delta
                        current_response_text = response_transcripts[response_id]
                
                elif event_type == "response.audio_transcript.done":
                    # Текст ответа ИИ завершён
                    response_id = event.get("response_id") or event.get("item_id")
                    final_text = response_transcripts.get(response_id, current_response_text)
                    
                    if final_text:
                        final_text = redact_pii(final_text)
                        
                        # Многоэтапные тренировки: ИИ помечает завершение этапа
                        # технические тегами [STAGE_COMPLETE] / [TRAINING_COMPLETE].
                        # Распознаём тег, чистим текст для пользователя и
                        # запоминаем действие, которое выполним после audio.done.
                        if user_session.stages:
                            if has_training_complete(final_text):
                                pending_stage_action = "complete_training"
                                logger.info(
                                    f"🏁 Обнаружен тег [TRAINING_COMPLETE] в реплике ИИ — "
                                    f"тренировка будет завершена после доигрывания аудио"
                                )
                            elif has_stage_complete(final_text):
                                pending_stage_action = "next_stage"
                                logger.info(
                                    f"➡️ Обнаружен тег [STAGE_COMPLETE] в реплике ИИ — "
                                    f"переход к следующему этапу после доигрывания аудио"
                                )
                            final_text = strip_tags(final_text)
                        
                        logger.debug(f"💬 ИИ ответил: '{final_text}'")
                        logger.info(f"Ожидаем аудио для response_id: {response_id}")
                        
                        # Отправляем полный текст клиенту (уже без тегов)
                        await websocket.send_json({
                            "type": "ai_text",
                            "text": final_text
                        })
                        
                        # Сохраняем реплику ИИ в БД фоном (своя короткая сессия) + трекинг
                        _fire_save(user_session, "assistant", final_text)
                    else:
                        logger.warning(f"⚠️ response.audio_transcript.done без текста для response_id: {response_id}")
                
                elif event_type == "response.audio.delta":
                    # Аудио чанк ответа ИИ - проксируем напрямую (как в оригинале)
                    audio_data = event.get("delta", "")
                    response_id_for_audio = event.get("response_id") or event.get("item_id") or current_response_id
                    
                    logger.debug(f"🔊 Получен аудио чанк от Azure (длина: {len(audio_data) if audio_data else 0})")
                    if audio_data:
                        audio_deltas_in_response += 1
                        # Проксируем событие напрямую клиенту
                        await websocket.send_json({
                            "type": "response.audio.delta",
                            "delta": audio_data,
                            "response_id": response_id_for_audio,
                            "item_id": event.get("item_id")
                        })
                    else:
                        logger.warning("⚠️ Получен response.audio.delta без данных")
                
                elif event_type == "response.audio.done":
                    # Аудио ответа завершено - проксируем напрямую
                    response_id = event.get("response_id") or event.get("item_id") or current_response_id
                    logger.info(
                        f"✅ Аудио ответа завершено (response_id: {response_id}, "
                        f"чанков: {audio_deltas_in_response})"
                    )

                    # Сторож «немого голоса».
                    #
                    # Проверка на живом ресурсе показала: Azure валидирует имя голоса
                    # только ПО ФОРМЕ. Несуществующая персона с правильным шаблоном
                    # (ru-RU-НетТакого:MAI-Voice-2-Flash) принимается МОЛЧА, без error —
                    # значит фолбэк по событию error на реальный сценарий (preview-голос
                    # убрали из региона) не сработает. Единственный надёжный признак —
                    # реплика сгенерирована, а аудио не пришло ни одного чанка.
                    if (
                        audio_deltas_in_response == 0
                        and not user_session.voice_fallback_used
                        and AZURE_VOICE_LIVE_VOICE_FALLBACK
                        and user_session.session_instructions
                    ):
                        user_session.voice_fallback_used = True
                        logger.error(
                            f"🔇 Реплика без аудио: голос '{AZURE_VOICE_LIVE_VOICE}' не синтезирует. "
                            f"Переключаюсь на запасной '{AZURE_VOICE_LIVE_VOICE_FALLBACK}' и повторяю реплику."
                        )
                        await _switch_to_fallback_voice(azure_connection, user_session)

                    audio_deltas_in_response = 0

                    # Проксируем событие напрямую клиенту
                    await websocket.send_json({
                        "type": "response.audio.done",
                        "response_id": response_id,
                        "item_id": event.get("item_id")
                    })
                    
                    current_response_id = None
                    
                    # Если ИИ в этой реплике пометил завершение этапа —
                    # выполняем переход СЕЙЧАС, после того как пользователь
                    # услышал прощальную фразу полностью.
                    if pending_stage_action and not user_session.is_switching_stage:
                        action = pending_stage_action
                        pending_stage_action = None
                        try:
                            await _handle_stage_action(
                                action=action,
                                user_session=user_session,
                                websocket=websocket,
                                azure_connection=azure_connection,
                            )
                        except Exception as e:
                            logger.error(f"❌ Ошибка обработки перехода этапа: {e}", exc_info=True)
                
                elif event_type == "response.function_call_arguments.done":
                    # ИИ вызвал tool-функцию (скрытый канал Azure Voice Live).
                    # Используем для переключения этапов тренировки —
                    # в отличие от тегов в тексте, это НЕ озвучивается голосом.
                    fn_name = event.get("name") or ""
                    call_id = event.get("call_id") or event.get("item_id")
                    logger.info(
                        f"🔧 ИИ вызвал функцию: {fn_name} "
                        f"(call_id={call_id})"
                    )
                    if user_session.stages and fn_name in (TOOL_COMPLETE_STAGE, TOOL_COMPLETE_TRAINING):
                        if fn_name == TOOL_COMPLETE_TRAINING:
                            pending_stage_action = "complete_training"
                        else:
                            pending_stage_action = "next_stage"
                        logger.info(
                            f"➡️ Запомнено действие '{pending_stage_action}' — "
                            f"выполним после того как ИИ доиграет аудио"
                        )
                    else:
                        logger.warning(
                            f"⚠️ Неизвестный вызов функции '{fn_name}' — игнорируем"
                        )
                
                elif event_type == "response.output_item.done":
                    # Альтернативная форма события — Azure может отдавать function_call
                    # внутри response.output_item.done с item.type="function_call".
                    item = event.get("item") or {}
                    if item.get("type") == "function_call":
                        fn_name = item.get("name") or ""
                        logger.info(
                            f"🔧 ИИ вызвал функцию (через output_item.done): {fn_name}"
                        )
                        if user_session.stages and fn_name in (TOOL_COMPLETE_STAGE, TOOL_COMPLETE_TRAINING):
                            if fn_name == TOOL_COMPLETE_TRAINING:
                                pending_stage_action = "complete_training"
                            else:
                                pending_stage_action = "next_stage"
                            logger.info(
                                f"➡️ Запомнено действие '{pending_stage_action}' — "
                                f"выполним после того как ИИ доиграет аудио"
                            )
                
                elif event_type == "response.cancelled":
                    # Ответ был отменен - проксируем напрямую
                    response_id = event.get("response_id") or event.get("item_id") or current_response_id
                    logger.info(f"⛔ Ответ отменен (response_id: {response_id})")
                    
                    # Проксируем событие напрямую клиенту
                    await websocket.send_json({
                        "type": "response.cancelled",
                        "response_id": response_id,
                        "item_id": event.get("item_id")
                    })
                    
                    current_response_id = None
                
                elif event_type == "error":
                    # Ошибка от Azure
                    error = event.get("error", {})
                    error_message = error.get("message", "Неизвестная ошибка")
                    error_code = error.get("code", "unknown")
                    logger.error(f"❌ Ошибка от Azure: {error_code} - {error_message}")
                    logger.debug(f"Полное событие ошибки: {json.dumps(event, ensure_ascii=False)}")

                    # Конфигурация сессии ещё не подтверждена → вероятнее всего Azure
                    # отклонила именно её (недоступный/preview-голос, снятый из региона).
                    # Раньше это давало сессию, где ИИ молча не отвечает и никто не
                    # понимает почему. Пробуем переслать конфигурацию с запасным голосом.
                    if (
                        not user_session.session_configured.is_set()
                        and not user_session.voice_fallback_used
                        and AZURE_VOICE_LIVE_VOICE_FALLBACK
                        and user_session.session_instructions
                    ):
                        user_session.voice_fallback_used = True
                        logger.warning(
                            f"⚠️ Конфигурация с голосом '{AZURE_VOICE_LIVE_VOICE}' отклонена "
                            f"({error_code}: {error_message}). Повтор с запасным голосом "
                            f"'{AZURE_VOICE_LIVE_VOICE_FALLBACK}'."
                        )
                        try:
                            await azure_connection.send_session_update(
                                instructions=user_session.session_instructions,
                                voice_name=AZURE_VOICE_LIVE_VOICE_FALLBACK,
                                transcription_model=AZURE_VOICE_LIVE_TRANSCRIPTION_MODEL,
                                transcription_language=AZURE_VOICE_LIVE_TRANSCRIPTION_LANGUAGE,
                                tools=getattr(user_session, "session_tools", None),
                                # Запасной голос — обычный neural: ни temperature, ни style.
                                voice_temperature=None,
                                voice_style=None,
                            )
                            continue  # ошибку клиенту не показываем — она обработана
                        except Exception as retry_error:
                            logger.error(f"❌ Фолбэк голоса не сработал: {retry_error}")

                    # Не критичные ошибки не прерывают соединение
                    if error_code in ["rate_limit", "quota_exceeded"]:
                        await websocket.send_json({
                            "type": "error",
                            "message": f"Ошибка Azure: {error_message}. Попробуйте позже."
                        })
                    else:
                        await websocket.send_json({
                            "type": "error",
                            "message": f"Ошибка Azure: {error_message}"
                        })
                
                else:
                    # Неизвестные события — на DEBUG (полный дамп может содержать транскрипт)
                    logger.debug(f"⚠️ Неизвестное событие от Azure: {event_type}")
                    logger.debug(f"Полное событие: {json.dumps(event, ensure_ascii=False)[:1000]}")
            
            except json.JSONDecodeError as e:
                logger.error(f"Ошибка парсинга JSON от Azure: {e}, message: {message[:100]}")
            except Exception as e:
                logger.error(f"Ошибка обработки события от Azure: {e}", exc_info=True)
                # Продолжаем работу, не прерываем соединение
    
    except asyncio.CancelledError:
        logger.info("Задача получения сообщений от Azure отменена")
    except Exception as e:
        logger.error(f"Ошибка в receive_from_azure: {e}", exc_info=True)


def _fire_save(user_session: UserSession, role: str, text: str):
    """Фоновое сохранение реплики в БД (каждая — в своей короткой сессии через
    run_db, вне event loop). Задачу трекаем в user_session.pending_saves, чтобы
    дренировать перед завершением сессии."""
    if not user_session.db_session_id:
        return
    task = asyncio.create_task(
        run_db(
            VoiceTrainingDBService.save_voice_message,
            user_session.db_session_id,
            role,
            text,
        )
    )
    user_session.pending_saves.add(task)

    def _done(t: "asyncio.Task"):
        user_session.pending_saves.discard(t)
        # Достаём исключение (иначе "Task exception was never retrieved") и логируем.
        if not t.cancelled() and t.exception() is not None:
            logger.error("Background voice-save task failed", exc_info=t.exception())

    task.add_done_callback(_done)


async def _drain_saves(user_session: UserSession):
    """Дожидается завершения всех незавершённых фоновых сохранений.

    Вызывается перед завершением/прерыванием сессии, чтобы AI-валидатор видел
    последнюю реплику, а не гонку «сохранение ещё в полёте» (план, раздел C)."""
    pending = list(getattr(user_session, "pending_saves", ()))
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def _handle_stage_action(
    action: str,
    user_session: UserSession,
    websocket: WebSocket,
    azure_connection: AzureVoiceLiveConnection,
):
    """
    Выполняет переход к следующему этапу или завершение всей тренировки.

    Вызывается ТОЛЬКО после того как ИИ доиграл прощальную фразу
    предыдущего этапа (response.audio.done).

    Args:
        action: "next_stage" или "complete_training"
        user_session: сессия пользователя со списком stages и current_stage_index
        websocket: WebSocket клиента — сюда отправляется stage_changed/training_completed
        azure_connection: Azure-соединение, в которое уходит session.update с новым промптом
    """
    if not user_session.stages:
        return
    
    user_session.is_switching_stage = True
    try:
        if action == "complete_training":
            logger.info(
                f"🏁 Завершаем многоэтапную тренировку user_id={user_session.user_id}, "
                f"session={user_session.session_id}"
            )
            await websocket.send_json({
                "type": "training_completed",
                "message": "Все этапы тренировки пройдены",
                "total_stages": len(user_session.stages),
            })
            # Дальше клиент сам вызовет end_session/stop, чтобы корректно
            # сохранить длительность и статистику в БД через handle_end_session.
            return
        
        if action == "next_stage":
            next_index = user_session.current_stage_index + 1
            if next_index >= len(user_session.stages):
                # Этапы кончились, но тег [TRAINING_COMPLETE] не пришёл —
                # на всякий случай завершаем тренировку.
                logger.warning(
                    "⚠️ Получен [STAGE_COMPLETE] но следующего этапа нет — "
                    "трактуем как завершение тренировки"
                )
                await websocket.send_json({
                    "type": "training_completed",
                    "message": "Все этапы тренировки пройдены",
                    "total_stages": len(user_session.stages),
                })
                return
            
            next_stage = user_session.stages[next_index]
            user_session.current_stage_index = next_index
            
            logger.info(
                f"➡️ Переход к этапу #{next_stage.number}/{len(user_session.stages)} "
                f"(роль ИИ: {next_stage.ai_role})"
            )
            
            # Меняем системный промпт у Azure без переподключения.
            # Tools (complete_stage/complete_training) нужно переопределять на
            # каждом этапе, иначе Azure их теряет при session.update.
            # С этого момента актуальные инструкции сессии — промпт нового этапа.
            # Без обновления фолбэк переслал бы конфигурацию со стартовым промптом
            # и откатил бы тренировку на первый этап.
            user_session.session_instructions = next_stage.prompt + _RU_OUTPUT_RULE

            # Голос и его настройки переотправляем те же, что при старте сессии:
            # session.update заменяет конфигурацию целиком, и без явной передачи
            # тон на втором этапе отличался бы от первого.
            await azure_connection.send_session_update(
                instructions=user_session.session_instructions,
                voice_name=_current_voice(user_session),
                transcription_model=AZURE_VOICE_LIVE_TRANSCRIPTION_MODEL,
                transcription_language=AZURE_VOICE_LIVE_TRANSCRIPTION_LANGUAGE,
                tools=build_stage_tools(),
                voice_style=None if user_session.voice_fallback_used else AZURE_VOICE_LIVE_VOICE_STYLE,
                voice_temperature=None if user_session.voice_fallback_used else AZURE_VOICE_LIVE_VOICE_TEMPERATURE,
                voice_rate=AZURE_VOICE_LIVE_VOICE_RATE,
            )
            
            # Сообщаем клиенту чтобы UI обновил роль ИИ и прогресс
            await websocket.send_json({
                "type": "stage_changed",
                "stage_number": next_stage.number,
                "total_stages": len(user_session.stages),
                "ai_role": next_stage.ai_role,
                "ai_role_description": next_stage.ai_role_description,
                "training_type": next_stage.training_type,
            })
            
            # Ждём короткий момент чтобы session.update применился
            await asyncio.sleep(0.4)
            
            # Определяем текст напоминания роли на основе ai_role этапа
            role_lower = (next_stage.ai_role or "").lower()
            if "клиент" in role_lower:
                role_reminder = (
                    "ТЫ КЛИЕНТ (покупатель). "
                    "Пользователь — МЕНЕДЖЕР (продавец). "
                    "НЕ предлагай скидки, НЕ рассказывай про акции, НЕ говори от лица компании-продавца. "
                    "Ты потенциальный покупатель — у тебя есть сомнения, ты задаёшь вопросы про продукт, "
                    "реагируешь на действия менеджера. Ты НЕ продавец!"
                )
            else:
                role_reminder = (
                    "ТЫ МЕНЕДЖЕР ПО ПРОДАЖАМ. "
                    "Пользователь — КЛИЕНТ (покупатель). "
                    "НЕ говори 'я сомневаюсь' / 'подумать надо' — это реплики клиента. "
                    "Ты продавец — ты предлагаешь, задаёшь вопросы, ведёшь к сделке."
                )
            
            # 1) Добавляем явное system-напоминание в историю диалога —
            # это самый сильный сигнал для ИИ при смене этапа, помогает
            # "перебить" предыдущую роль, в которой он был в прошлых этапах.
            try:
                system_reminder = (
                    f"⚠️ СМЕНА КОНТЕКСТА ТРЕНИРОВКИ — ЭТАП {next_stage.number} ИЗ {len(user_session.stages)}.\n\n"
                    f"{role_reminder}\n\n"
                    f"Если в предыдущих этапах ты играл другую роль — ЗАБУДЬ её. "
                    f"С этого момента у тебя НОВАЯ роль, описанная выше. "
                    f"Следуй ТОЛЬКО инструкциям текущего этапа."
                )
                await azure_connection.send_system_item(system_reminder)
            except Exception as e:
                logger.warning(f"⚠️ Не удалось отправить system-напоминание: {e}")
            
            # 2) Форсируем старт реплики ИИ — чтобы новый этап начался
            # автоматически, без ожидания пользователя.
            try:
                # Если для этого этапа задан шаблон первой реплики —
                # вкладываем его в триггер; это сильно снижает импровизацию
                # и предотвращает конфликт с промптом (дубли вступления).
                #
                # ⚠️ response.instructions ЗАМЕНЯЕТ session.instructions для этого ответа
                # (см. комментарий у стартовой реплики), поэтому промпт этапа обязан
                # уходить вместе с триггером — иначе реплика этапа генерируется вслепую.
                if next_stage.first_line_template:
                    stage_task = (
                        next_stage.first_line_template
                        + "\n\nПОСЛЕ того как произнесёшь эту вступительную реплику — "
                        "ЖДИ ответа пользователя. НЕ продолжай говорить, "
                        "НЕ повторяй вступление даже если пользователь "
                        "ответил коротко или непонятно."
                    )
                else:
                    stage_task = (
                        f"Начни этап #{next_stage.number} строго по шагу 1 инструкций выше. "
                        "В ОДНОЙ реплике: короткая связка-переход + тренировочное действие. "
                        "Не повторяй что уже говорил. Соблюдай роль — см. system item выше."
                    )
                trigger_instructions = (
                    next_stage.prompt
                    + "\n\n===== ЧТО СДЕЛАТЬ ПРЯМО СЕЙЧАС =====\n"
                    + stage_task
                )
                user_session.last_response_instructions = trigger_instructions
                await azure_connection.send_response_create(
                    instructions=trigger_instructions
                )
                logger.info(
                    f"✅ Форсирован старт реплики ИИ для этапа #{next_stage.number} "
                    f"(роль: {next_stage.ai_role}, "
                    f"есть шаблон: {bool(next_stage.first_line_template)})"
                )
            except Exception as e:
                logger.warning(
                    f"⚠️ Не удалось форсировать старт реплики ИИ: {e}. "
                    f"ИИ начнёт этап когда пользователь скажет что-нибудь."
                )
    finally:
        user_session.is_switching_stage = False


async def handle_stop(user_session: UserSession, websocket: WebSocket, azure_connection: Optional[AzureVoiceLiveConnection] = None):
    """
    Обрабатывает команду остановки.
    
    Args:
        user_session: Сессия пользователя
        websocket: WebSocket соединение
        azure_connection: Соединение с Azure (опционально)
    """
    logger.info(f"⏹️ Остановка (session={user_session.session_id})")
    
    # Отправляем команду отмены в Azure если есть активный ответ
    if azure_connection:
        try:
            await azure_connection.send({
                "type": "response.cancel",
                "event_id": ""
            })
        except:
            pass
    
    await websocket.send_json({
        "type": "stopped",
        "message": "Остановлено"
    })


async def handle_end_session(user_session: UserSession, websocket: WebSocket, azure_connection: Optional[AzureVoiceLiveConnection] = None):
    """
    Обрабатывает завершение сессии тренировки.

    Args:
        user_session: Сессия пользователя
        websocket: WebSocket соединение
        azure_connection: Соединение с Azure (опционально)
    """
    if not user_session.db_session_id:
        return

    try:
        # Сначала дренируем фоновые сохранения, чтобы посчитать реплики корректно
        await _drain_saves(user_session)

        session_db_id = user_session.db_session_id
        created_at = user_session.created_at

        def _finalize(s):
            try:
                from models import VoiceTrainingMessage
            except ImportError:
                from app.models import VoiceTrainingMessage

            messages = s.query(VoiceTrainingMessage).filter(
                VoiceTrainingMessage.session_id == session_db_id
            ).all()
            user_responses = sum(1 for m in messages if m.role == "user")
            ai_questions = sum(1 for m in messages if m.role == "assistant")
            duration = int((datetime.utcnow() - created_at).total_seconds())

            VoiceTrainingDBService.complete_training_session(
                s, session_db_id, duration, user_responses, ai_questions
            )
            return duration, len(messages)

        duration, messages_count = await run_db(_finalize)

        logger.info(f"✅ Сессия {user_session.session_id} завершена: {duration}s, {messages_count} сообщений")

        await websocket.send_json({
            "type": "session_ended",
            "duration": duration,
            "messages_count": messages_count
        })

    except Exception as e:
        logger.error(f"❌ Ошибка завершения сессии: {e}")
