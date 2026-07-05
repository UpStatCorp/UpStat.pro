"""FastAPI-обвязка локального realtime-режима (USE_AZURE_VOICE_LIVE=false).

Собирает сессию (capacity + БД + этапы + промпт), строит блоки STT/LLM/TTS,
поднимает LocalRealtimeDriver и крутит цикл сообщений клиента. Полностью
повторяет protocol-контракт фронта, поэтому voice-training.js не требует правок.

БД НЕ прибивается к сокету: сохранения — фоном через _fire_save (короткая run_db
сессия), как и в Azure-пути. STT-модель (faster-whisper) шарится между сессиями —
иначе 100+ юзеров = 100 загрузок модели.

End-to-end проверяется с ключами (YandexGPT/SpeechKit или whisper_local офлайн);
ядро (audio/VAD/driver) покрыто юнит-тестами.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from ..session_manager import get_session_manager, UserSession
from ..db_service import VoiceTrainingDBService, run_db
from ..config import get_system_prompt, GPT_MODEL, SAMPLE_RATE
from ..gpt_logic import GPTDialogue
from ..stt_reactive import STTEngine
from ..tts_response import TTSEngine
from ..websocket_handler import _fire_save, _drain_saves
from .driver import LocalRealtimeDriver

try:
    from services.pii_redactor import redact_pii
except ImportError:
    from app.services.pii_redactor import redact_pii

try:
    from services.training_stages_service import (
        load_stages, strip_tags, has_stage_complete, has_training_complete,
    )
except ImportError:
    from app.services.training_stages_service import (
        load_stages, strip_tags, has_stage_complete, has_training_complete,
    )

logger = logging.getLogger(__name__)

_MAX_AUDIO_CHUNK_B64 = int(os.getenv("MAX_AUDIO_CHUNK_B64", str(64 * 1024)))
_MAX_SESSION_SECONDS = int(os.getenv("MAX_VOICE_SESSION_SECONDS", str(60 * 60)))

# Разделяемый STT-движок (одна faster-whisper модель на процесс). Строится лениво в
# ПОТОКЕ (asyncio.to_thread) — загрузка whisper_local модели синхронно в event loop
# заблокировала бы ВСЕ сессии на секунды. Lock защищает от гонки первых подключений.
_shared_stt: Optional[STTEngine] = None
_stt_lock: Optional[asyncio.Lock] = None


async def _get_stt_async() -> STTEngine:
    global _shared_stt, _stt_lock
    if _shared_stt is not None:
        return _shared_stt
    if _stt_lock is None:
        _stt_lock = asyncio.Lock()
    async with _stt_lock:
        if _shared_stt is None:
            _shared_stt = await asyncio.to_thread(STTEngine)
    return _shared_stt


def _role_reminder(ai_role: Optional[str]) -> str:
    role_lower = (ai_role or "").lower()
    if "клиент" in role_lower:
        return (
            "ТЫ КЛИЕНТ (покупатель). Пользователь — МЕНЕДЖЕР (продавец). "
            "НЕ предлагай скидки, НЕ рассказывай про акции, НЕ говори от лица компании-продавца. "
            "Ты потенциальный покупатель — есть сомнения, задаёшь вопросы про продукт. Ты НЕ продавец!"
        )
    return (
        "ТЫ МЕНЕДЖЕР ПО ПРОДАЖАМ. Пользователь — КЛИЕНТ (покупатель). "
        "НЕ говори 'я сомневаюсь' / 'подумать надо' — это реплики клиента. "
        "Ты продавец — предлагаешь, задаёшь вопросы, ведёшь к сделке."
    )


def _stage_kickoff_instruction(stage, total: int) -> str:
    if getattr(stage, "first_line_template", None):
        return (
            stage.first_line_template
            + "\n\nПОСЛЕ того как произнесёшь эту вступительную реплику — ЖДИ ответа пользователя. "
            "НЕ продолжай говорить, НЕ повторяй вступление."
        )
    return (
        f"Начни этап #{stage.number} строго по шагу 1 инструкций. "
        "В ОДНОЙ реплике: короткая связка-переход + тренировочное действие. "
        "Не повторяй что уже говорил. Соблюдай роль этапа."
    )


def _initial_kickoff_instruction(user_session: UserSession) -> str:
    if user_session.stages:
        return _stage_kickoff_instruction(user_session.stages[0], len(user_session.stages))
    return (
        "Кратко поприветствуй пользователя на русском языке и начни тренировку "
        "строго по инструкциям сессии. Сначала выступи как тренер-инструктор."
    )


async def handle_local_realtime_connection(
    websocket: WebSocket,
    user_id: int,
    training_id: int,
    existing_db_session_id: Optional[int] = None,
):
    """Локальный STT→LLM→TTS realtime (без Azure). Сокет уже принят роутером."""
    session_manager = get_session_manager()
    user_session: Optional[UserSession] = None
    driver: Optional[LocalRealtimeDriver] = None
    kickoff_task: Optional[asyncio.Task] = None

    try:
        user_session = await session_manager.create_session(user_id, training_id)
        if not user_session:
            await websocket.send_json({
                "type": "error",
                "message": "⚠️ Достигнут лимит одновременных пользователей. Попробуйте позже.",
            })
            await websocket.close(code=1008, reason="Server capacity reached")
            return

        ws_session_id = user_session.session_id

        # Создание/реконнект записи в БД (одна короткая run_db сессия) — как в Azure-пути.
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
        user_session.db_session_id = db_session_id
        user_session.websocket = websocket
        logger.info(
            f"✅ Локальный realtime: user_id={user_id}, training_id={training_id}, "
            f"session={ws_session_id} (db={db_session_id}, reconnect={is_reconnect})"
        )

        # Локаль + данные тренировки (плоские значения — ORM detached после закрытия сессии).
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
                logger.warning(f"⚠️ Не удалось определить локаль: {e}")
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
                logger.warning(f"⚠️ Не удалось загрузить тренировку: {e}")
            return locale, info

        user_locale, training_info = await run_db(_load_setup)
        session_instructions = get_system_prompt(user_locale)

        stages: list = []
        if training_info and training_info.get("stage"):
            try:
                stages = load_stages(training_info["stage"], locale=user_locale)
            except Exception as e:
                logger.warning(f"⚠️ Ошибка загрузки этапов: {e}")

        if stages:
            user_session.stages = stages
            user_session.current_stage_index = 0
            session_instructions = stages[0].prompt
            logger.info(
                f"🎯 Многоэтапная тренировка: {len(stages)} этапов, старт с #{stages[0].number} "
                f"(роль ИИ: {stages[0].ai_role})"
            )
        elif training_info and training_info.get("recommendation"):
            session_instructions = get_system_prompt(user_locale) + (
                f"\n\n===== КОНТЕКСТ ТЕКУЩЕЙ ТРЕНИРОВКИ =====\n"
                f"Тема тренировки: {training_info['title']}\n"
                f"Проблема менеджера: {training_info['description']}\n"
                f"Что нужно отработать: {training_info['recommendation']}\n"
                f"Этап продаж: {training_info['stage'] or 'не указан'}\n"
                f"========================================\n\n"
                f"ВАЖНО: Адаптируй тренировку под эту проблему, примеры под тему "
                f"\"{training_info['title']}\"."
            )

        # Блоки STT/LLM/TTS. Ошибка (нет ключей) → чистое сообщение клиенту, а не краш.
        try:
            stt = await _get_stt_async()
            dialogue = GPTDialogue(model=GPT_MODEL, system_prompt=session_instructions)
            tts = TTSEngine()
        except Exception as e:
            logger.error(f"❌ Не удалось инициализировать AI-блоки локального режима: {e}", exc_info=True)
            await websocket.send_json({
                "type": "error",
                "message": "⚠️ Голосовой режим не сконфигурирован (проверьте ключи STT/LLM/TTS).",
            })
            await websocket.close(code=1011, reason="Local voice not configured")
            return

        language = "en" if (user_locale or "ru").lower() == "en" else "ru"

        def _process_reply(text: str):
            action = None
            if user_session.stages:
                if has_training_complete(text):
                    action = "complete_training"
                elif has_stage_complete(text):
                    action = "next_stage"
                text = strip_tags(text)
            return text, action

        async def _save_user(t: str):
            _fire_save(user_session, "user", t)

        async def _save_ai(t: str):
            _fire_save(user_session, "assistant", t)

        driver = LocalRealtimeDriver(
            send=websocket.send_json,
            stt=stt,
            dialogue=dialogue,
            tts=tts,
            session_id=ws_session_id,
            language=language,
            tts_sample_rate=SAMPLE_RATE,
            on_user_text=_save_user,
            on_ai_text=_save_ai,
            process_ai_reply=_process_reply,
            redact=redact_pii,
        )

        async def _stage_action(action: str):
            await _local_stage_action(action, user_session, websocket, dialogue, driver)

        driver.on_stage_action = _stage_action

        # Подтверждения подключения (те же события, что ждёт фронт).
        await websocket.send_json({
            "type": "connected",
            "session_id": ws_session_id,
            "db_session_id": db_session_id,
            "message": "✅ Подключение установлено (локальный режим)",
        })
        await websocket.send_json({"type": "session.created"})

        if user_session.stages:
            first = user_session.stages[0]
            await websocket.send_json({
                "type": "stage_changed",
                "stage_number": first.number,
                "total_stages": len(user_session.stages),
                "ai_role": first.ai_role,
                "ai_role_description": first.ai_role_description,
                "training_type": first.training_type,
            })

        # Стартовая реплика ИИ — фоном, чтобы не блокировать чтение сообщений.
        kickoff_task = asyncio.create_task(driver.kickoff(_initial_kickoff_instruction(user_session)))

        # ── основной цикл сообщений клиента ────────────────────────────────────
        session_start = datetime.now(timezone.utc)
        try:
            async for message in websocket.iter_text():
                if (datetime.now(timezone.utc) - session_start).total_seconds() > _MAX_SESSION_SECONDS:
                    await websocket.send_json({"type": "error", "message": "Максимальная продолжительность тренировки достигнута."})
                    break
                try:
                    data = json.loads(message) if message else {}
                except (json.JSONDecodeError, ValueError):
                    continue

                mt = data.get("type")
                if mt in ("input_audio_buffer.append", "audio", "audio_data"):
                    audio_b64 = data.get("audio") or data.get("audio_data", "")
                    if audio_b64 and len(audio_b64) <= _MAX_AUDIO_CHUNK_B64:
                        await driver.handle_audio_b64(audio_b64)
                elif mt == "ping":
                    await websocket.send_json({"type": "pong", "ts": data.get("ts")})
                elif mt in ("stop", "response.cancel"):
                    # half-duplex v1: активное прерывание (barge-in) не поддержано — тихо игнорируем.
                    pass
                elif mt == "end_session":
                    break
        except WebSocketDisconnect as wsd:
            logger.info(f"🔌 Пользователь {user_id} отключился (code={wsd.code})")

    except Exception as e:
        logger.error(f"❌ Ошибка локального realtime для user_id={user_id}: {e}", exc_info=True)
        try:
            await websocket.send_json({"type": "error", "message": f"Ошибка сервера: {str(e)}"})
        except Exception:
            pass
    finally:
        if driver:
            driver.close()
        if kickoff_task:
            kickoff_task.cancel()
            try:
                await kickoff_task
            except (asyncio.CancelledError, Exception):
                pass
        if user_session:
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
        except Exception:
            pass


async def _local_stage_action(
    action: str,
    user_session: UserSession,
    websocket: WebSocket,
    dialogue: GPTDialogue,
    driver: LocalRealtimeDriver,
):
    """Локальный аналог _handle_stage_action: переход этапа/финал БЕЗ Azure.

    ВАЖНО: вызывается из driver._run_turn ПОД его lock, поэтому следующую реплику
    ИИ (kickoff) НЕ ждём здесь, а планируем задачей — иначе self-deadlock на lock.
    """
    if not user_session.stages:
        return
    user_session.is_switching_stage = True
    try:
        if action == "complete_training":
            await websocket.send_json({
                "type": "training_completed",
                "message": "Все этапы тренировки пройдены",
                "total_stages": len(user_session.stages),
            })
            return

        if action == "next_stage":
            next_index = user_session.current_stage_index + 1
            if next_index >= len(user_session.stages):
                await websocket.send_json({
                    "type": "training_completed",
                    "message": "Все этапы тренировки пройдены",
                    "total_stages": len(user_session.stages),
                })
                return

            next_stage = user_session.stages[next_index]
            user_session.current_stage_index = next_index
            logger.info(
                f"➡️ Локальный переход к этапу #{next_stage.number}/{len(user_session.stages)} "
                f"(роль ИИ: {next_stage.ai_role})"
            )

            # Меняем системный промпт диалога + добавляем system-напоминание роли.
            dialogue.system_prompt = next_stage.prompt
            if dialogue.conversation_history and dialogue.conversation_history[0].get("role") == "system":
                dialogue.conversation_history[0]["content"] = next_stage.prompt
            reminder = (
                f"⚠️ СМЕНА КОНТЕКСТА ТРЕНИРОВКИ — ЭТАП {next_stage.number} ИЗ {len(user_session.stages)}.\n\n"
                f"{_role_reminder(next_stage.ai_role)}\n\n"
                f"Если в предыдущих этапах ты играл другую роль — ЗАБУДЬ её. "
                f"Следуй ТОЛЬКО инструкциям текущего этапа."
            )
            dialogue.conversation_history.append({"role": "system", "content": reminder})

            await websocket.send_json({
                "type": "stage_changed",
                "stage_number": next_stage.number,
                "total_stages": len(user_session.stages),
                "ai_role": next_stage.ai_role,
                "ai_role_description": next_stage.ai_role_description,
                "training_type": next_stage.training_type,
            })

            # Старт реплики нового этапа — отдельной ТРЕКАЕМОЙ задачей (после освобождения
            # lock драйвера; отменится в driver.close при дисконнекте).
            trigger = _stage_kickoff_instruction(next_stage, len(user_session.stages))
            driver.schedule_kickoff(trigger)
    finally:
        user_session.is_switching_stage = False
