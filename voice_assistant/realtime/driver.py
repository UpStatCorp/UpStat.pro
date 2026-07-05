"""Оркестратор локального realtime-диалога (half-duplex v1).

Заменяет Azure Voice Live: принимает аудио-чанки от клиента, сам детектит границы
реплик (server-VAD), гоняет цикл STT → LLM → TTS и эмитит те же protocol-события,
что ждёт фронт (voice-training.js). Half-duplex: пока говорит ИИ, вход микрофона
игнорируется — реплики строго по очереди (barge-in — отдельным шагом).

Драйвер намеренно развязан от FastAPI/БД/этапов через инъекцию зависимостей и
хуков, поэтому покрывается юнит-тестами без сети и ключей (tests/test_realtime_driver.py):

  send              async (dict) -> None     — отправка события клиенту (websocket.send_json).
  stt               .transcribe(np.float32, language) -> str   (синхронно; крутим в потоке).
  dialogue          async .get_response(text) -> str           (GPTDialogue, ведёт историю).
  tts               async-gen .synthesize(text) -> yields np.float32 @ tts_sample_rate.

Хуки (все опциональны):
  on_user_text(text)          async — напр. сохранить реплику юзера в БД.
  on_ai_text(text)            async — напр. сохранить реплику ИИ в БД.
  process_ai_reply(text)      -> (clean_text, stage_action|None) — снять теги этапов.
  on_stage_action(action)     async — выполнить переход этапа/финал тренировки.
  redact(text)                -> text — редактирование ПД перед отправкой/сохранением.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Awaitable, Callable, Optional, Set, Tuple

import numpy as np

from .audio import wire_b64_to_internal, internal_to_wire_b64, INTERNAL_SAMPLE_RATE
from .segmenter import (
    UtteranceSegmenter,
    EVENT_SPEECH_STARTED,
    EVENT_SPEECH_ENDED,
)

logger = logging.getLogger("voice.local_realtime")

SendFn = Callable[[dict], Awaitable[None]]


async def _noop_text(_text: str) -> None:
    return None


async def _noop_action(_action: str) -> None:
    return None


def _default_process_reply(text: str) -> Tuple[str, Optional[str]]:
    return text, None


class LocalRealtimeDriver:
    def __init__(
        self,
        *,
        send: SendFn,
        stt,
        dialogue,
        tts,
        session_id: str = "local",
        language: str = "ru",
        segmenter: Optional[UtteranceSegmenter] = None,
        tts_sample_rate: int = INTERNAL_SAMPLE_RATE,
        on_user_text: Optional[Callable[[str], Awaitable[None]]] = None,
        on_ai_text: Optional[Callable[[str], Awaitable[None]]] = None,
        process_ai_reply: Optional[Callable[[str], Tuple[str, Optional[str]]]] = None,
        on_stage_action: Optional[Callable[[str], Awaitable[None]]] = None,
        redact: Optional[Callable[[str], str]] = None,
        turn_cooldown_ms: Optional[int] = None,
    ):
        self.send = send
        self.stt = stt
        self.dialogue = dialogue
        self.tts = tts
        self.session_id = session_id
        self.language = language
        self.segmenter = segmenter or UtteranceSegmenter()
        self.tts_sample_rate = tts_sample_rate

        self.on_user_text = on_user_text or _noop_text
        self.on_ai_text = on_ai_text or _noop_text
        self.process_ai_reply = process_ai_reply or _default_process_reply
        self.on_stage_action = on_stage_action or _noop_action
        self.redact = redact or (lambda t: t)

        self._speaking = False           # half-duplex: True пока идёт реплика ИИ
        self._resp_counter = 0
        self._closed = False
        # Пост-реплика cooldown: короткое окно после ответа ИИ, когда микрофон ещё
        # игнорируется — чтобы «хвост» эха/буфер транспорта не распознался как реплика.
        if turn_cooldown_ms is None:
            try:
                turn_cooldown_ms = int(os.getenv("LOCAL_TURN_COOLDOWN_MS", "").strip() or 400)
            except ValueError:
                turn_cooldown_ms = 400
        self._cooldown_s = max(0.0, turn_cooldown_ms / 1000.0)
        self._cooldown_until = 0.0
        # Турны гоняем фоновыми задачами, чтобы НЕ блокировать чтение сокета
        # (иначе ping не отвечается и входной звук буферизуется в транспорте).
        self._turn_tasks: Set["asyncio.Task"] = set()
        # Lock создаём лениво внутри цикла событий: на Python 3.9 asyncio.Lock()
        # в __init__ (вне running loop) падает с "no current event loop".
        self._lock: Optional[asyncio.Lock] = None

    def _get_lock(self) -> asyncio.Lock:
        """Ленивое создание Lock внутри running loop (совместимо с Python 3.9)."""
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    # ── вход от клиента ────────────────────────────────────────────────────────

    async def handle_audio_b64(self, audio_b64: str) -> None:
        """Обрабатывает входящий чанк input_audio_buffer.append (base64 PCM16@24k).
        Быстрый путь: декод + VAD; сам турн уходит в фоновую задачу (не блокирует чтение)."""
        if self._closed or self._speaking or self._in_cooldown():
            # Half-duplex: пока ИИ говорит/думает или в cooldown — микрофон игнорируем.
            return
        if not audio_b64:
            return
        try:
            internal = wire_b64_to_internal(audio_b64)
        except Exception as e:  # повреждённый чанк не должен ронять сессию
            logger.warning(f"Не удалось декодировать аудио-чанк: {e}")
            return
        events = self.segmenter.push(internal)
        await self._handle_events(events)

    def _in_cooldown(self) -> bool:
        return time.monotonic() < self._cooldown_until

    async def _handle_events(self, events) -> None:
        for name, payload in events:
            if name == EVENT_SPEECH_STARTED:
                await self._safe_send({"type": "input_audio_buffer.speech_started"})
            elif name == EVENT_SPEECH_ENDED:
                if payload is None or payload.size == 0:
                    continue
                self._start_turn(payload)

    def _start_turn(self, utterance: np.ndarray) -> None:
        """Запускает обработку реплики фоновой задачей. _speaking выставляем СИНХРОННО,
        чтобы последующие чанки того же push/цикла сразу отбрасывались (half-duplex)."""
        if self._closed or self._speaking:
            return
        self._speaking = True
        task = asyncio.create_task(self._run_turn(utterance))
        self._turn_tasks.add(task)
        task.add_done_callback(self._turn_tasks.discard)

    async def wait_idle(self) -> None:
        """Дожидается завершения всех фоновых турнов (для тестов/дренажа)."""
        while self._turn_tasks:
            await asyncio.gather(*list(self._turn_tasks), return_exceptions=True)

    # ── одна реплика: STT → LLM → TTS ──────────────────────────────────────────

    async def _run_turn(self, utterance: np.ndarray) -> None:
        async with self._get_lock():
            self._speaking = True
            try:
                # 1) STT
                text = await self._transcribe(utterance)
                text = self.redact(text or "").strip()
                if not text:
                    return
                await self._safe_send({"type": "user_text", "text": text})
                await self._safe_hook(self.on_user_text, text)

                # 2) LLM
                self._resp_counter += 1
                response_id = f"local-{self.session_id}-{self._resp_counter}"
                await self._safe_send({
                    "type": "status", "status": "thinking",
                    "message": "🤔 Думаю...", "response_id": response_id,
                })
                raw_reply = await self._generate(text)
                clean_reply, stage_action = self.process_ai_reply(raw_reply or "")
                # ПД редактируем и для клиента, И для БД (озвучиваем оригинал — как Azure).
                red_reply = self.redact(clean_reply) if clean_reply else ""

                if red_reply and red_reply.strip():
                    await self._safe_send({"type": "ai_text", "text": red_reply})
                    await self._safe_hook(self.on_ai_text, red_reply)

                # 3) TTS → стрим аудио клиенту
                await self._speak(clean_reply, response_id)
                await self._safe_send({"type": "response.audio.done", "response_id": response_id})
                # Терминальное событие: фронт сбрасывает activeResponseId и микрофон.
                await self._safe_send({"type": "status", "status": "completed", "response_id": response_id})

                # 4) переход этапа — уже после того, как аудио доиграно
                if stage_action:
                    await self._safe_hook(self.on_stage_action, stage_action)
            except Exception as e:
                logger.error(f"Ошибка обработки реплики: {e}", exc_info=True)
                await self._safe_send({"type": "error", "message": "Ошибка обработки реплики."})
            finally:
                self._speaking = False
                self._cooldown_until = time.monotonic() + self._cooldown_s
                # чистим всё, что накопилось в сегментаторе, пока говорил ИИ
                self.segmenter.reset()

    async def _transcribe(self, utterance: np.ndarray) -> str:
        try:
            return await asyncio.to_thread(self.stt.transcribe, utterance, self.language)
        except Exception as e:
            logger.error(f"Ошибка STT: {e}", exc_info=True)
            return ""

    async def _generate(self, text: str) -> str:
        try:
            return await self.dialogue.get_response(text)
        except Exception as e:
            logger.error(f"Ошибка LLM: {e}", exc_info=True)
            return ""

    async def _speak(self, text: str, response_id: str) -> None:
        if not text or not text.strip():
            return
        try:
            async for chunk in self.tts.synthesize(text):
                if self._closed:
                    break
                if chunk is None or len(chunk) == 0:
                    continue
                delta = internal_to_wire_b64(chunk, src_rate=self.tts_sample_rate)
                if delta:
                    await self._safe_send({
                        "type": "response.audio.delta",
                        "delta": delta,
                        "response_id": response_id,
                    })
        except Exception as e:
            logger.error(f"Ошибка TTS: {e}", exc_info=True)

    # ── стартовая реплика ИИ (ИИ говорит первым) ───────────────────────────────

    async def kickoff(self, instruction: str) -> None:
        """Просит ИИ произнести стартовую реплику (приветствие/инструктаж) без ввода юзера."""
        async with self._get_lock():
            self._speaking = True
            try:
                self._resp_counter += 1
                response_id = f"local-{self.session_id}-{self._resp_counter}"
                raw_reply = await self._generate(instruction)
                clean_reply, stage_action = self.process_ai_reply(raw_reply or "")
                red_reply = self.redact(clean_reply) if clean_reply else ""
                if red_reply and red_reply.strip():
                    await self._safe_send({"type": "ai_text", "text": red_reply})
                    await self._safe_hook(self.on_ai_text, red_reply)
                await self._speak(clean_reply, response_id)
                await self._safe_send({"type": "response.audio.done", "response_id": response_id})
                await self._safe_send({"type": "status", "status": "completed", "response_id": response_id})
                if stage_action:
                    await self._safe_hook(self.on_stage_action, stage_action)
            except Exception as e:
                logger.error(f"Ошибка стартовой реплики: {e}", exc_info=True)
            finally:
                self._speaking = False
                self._cooldown_until = time.monotonic() + self._cooldown_s
                self.segmenter.reset()

    def schedule_kickoff(self, instruction: str) -> "asyncio.Task":
        """Планирует стартовую реплику фоновой задачей (трекается и отменяется в close).
        Использовать при смене этапа: вызов из-под lock драйвера напрямую await'ить
        kickoff нельзя (self-deadlock) — только планировать."""
        task = asyncio.create_task(self.kickoff(instruction))
        self._turn_tasks.add(task)
        task.add_done_callback(self._turn_tasks.discard)
        return task

    def close(self) -> None:
        self._closed = True
        # Отменяем незавершённые фоновые турны/kickoff'ы, чтобы не висели после дисконнекта.
        for task in list(self._turn_tasks):
            task.cancel()

    # ── защита от падений на send/hook ─────────────────────────────────────────

    async def _safe_send(self, event: dict) -> None:
        if self._closed:
            return
        try:
            await self.send(event)
        except Exception as e:
            logger.warning(f"Не удалось отправить событие '{event.get('type')}': {e}")

    async def _safe_hook(self, hook: Callable[[str], Awaitable[None]], arg: str) -> None:
        try:
            await hook(arg)
        except Exception as e:
            logger.warning(f"Хук завершился ошибкой: {e}")
