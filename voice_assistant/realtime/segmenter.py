"""Server-side VAD: нарезка непрерывного аудиопотока на реплики пользователя.

В Azure Voice Live детекцией начала/конца речи занимался сам Azure и слал
input_audio_buffer.speech_started/stopped. Локально это делаем мы. Сегментатор
принимает поток float32-сэмплов (push) и выдаёт события:

  ("speech_started", None)          — пользователь заговорил (после дебаунса);
  ("speech_ended", utterance_f32)   — реплика завершена (после паузы), с аудио реплики.

Логика turn-taking (дебаунс старта + hangover тишины + max-длина) — здесь и покрыта
тестами. Классификатор кадра «речь/тишина» вынесен в предикат (по умолчанию —
энергия RMS, детерминированно и без внешних зависимостей; для продакшена можно
подменить на webrtcvad, не трогая логику).

Пороги настраиваются через .env (LOCAL_VAD_*), дефолты подобраны под PCM16→float32.
"""
from __future__ import annotations

import os
from typing import Callable, List, Optional, Tuple

import numpy as np

from .audio import INTERNAL_SAMPLE_RATE

EVENT_SPEECH_STARTED = "speech_started"
EVENT_SPEECH_ENDED = "speech_ended"

Event = Tuple[str, Optional[np.ndarray]]


def _rms(frame: np.ndarray) -> float:
    if frame.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(frame.astype(np.float64) ** 2)))


def energy_classifier(threshold: float) -> Callable[[np.ndarray], bool]:
    """Классификатор кадра по RMS-энергии: rms(frame) > threshold => речь."""
    def _is_speech(frame: np.ndarray) -> bool:
        return _rms(frame) > threshold
    return _is_speech


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


class UtteranceSegmenter:
    """Накопитель речи с детекцией границ реплик.

    Параметры (все — через .env, дефолты в скобках):
      LOCAL_VAD_FRAME_MS (30)          — размер кадра анализа, мс.
      LOCAL_VAD_RMS_THRESHOLD (0.015)  — порог энергии для дефолтного классификатора.
      LOCAL_VAD_START_MS (150)         — сколько речи подряд нужно, чтобы засчитать старт
                                         (дебаунс от щелчков).
      LOCAL_VAD_SILENCE_MS (600)       — пауза после речи, завершающая реплику (hangover).
      LOCAL_VAD_MAX_UTTER_MS (25000)   — жёсткий потолок длины реплики (защита от «вечной» речи).
      LOCAL_VAD_MIN_UTTER_MS (200)     — короче этого — реплика отбрасывается (шум).
    """

    def __init__(
        self,
        sample_rate: int = INTERNAL_SAMPLE_RATE,
        is_speech: Optional[Callable[[np.ndarray], bool]] = None,
        frame_ms: Optional[int] = None,
        start_ms: Optional[int] = None,
        silence_ms: Optional[int] = None,
        max_utter_ms: Optional[int] = None,
        min_utter_ms: Optional[int] = None,
    ):
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms if frame_ms is not None else _env_int("LOCAL_VAD_FRAME_MS", 30)
        self.frame_size = max(1, int(sample_rate * self.frame_ms / 1000))
        threshold = _env_float("LOCAL_VAD_RMS_THRESHOLD", 0.015)
        self.is_speech = is_speech or energy_classifier(threshold)

        start_ms = start_ms if start_ms is not None else _env_int("LOCAL_VAD_START_MS", 150)
        silence_ms = silence_ms if silence_ms is not None else _env_int("LOCAL_VAD_SILENCE_MS", 600)
        max_ms = max_utter_ms if max_utter_ms is not None else _env_int("LOCAL_VAD_MAX_UTTER_MS", 25000)
        # Дефолт 500 мс: STTEngine.transcribe отбрасывает аудио короче 0.5 с
        # (stt_reactive.py) — реплики короче не имеет смысла гонять в STT.
        min_ms = min_utter_ms if min_utter_ms is not None else _env_int("LOCAL_VAD_MIN_UTTER_MS", 500)

        self.start_frames = max(1, round(start_ms / self.frame_ms))
        self.silence_frames = max(1, round(silence_ms / self.frame_ms))
        self.max_frames = max(1, round(max_ms / self.frame_ms))
        self.min_samples = int(sample_rate * min_ms / 1000)

        self._leftover = np.zeros(0, dtype=np.float32)
        self._active = False
        self._utterance: List[np.ndarray] = []
        self._onset: List[np.ndarray] = []   # речь до подтверждения старта (pre-roll)
        self._silence_run = 0                 # кадров тишины подряд внутри реплики
        self._active_frames = 0

    def reset(self) -> None:
        """Полный сброс состояния (напр. при смене этапа/прерывании)."""
        self._leftover = np.zeros(0, dtype=np.float32)
        self._active = False
        self._utterance = []
        self._onset = []
        self._silence_run = 0
        self._active_frames = 0

    def push(self, samples: np.ndarray) -> List[Event]:
        """Добавляет сэмплы (float32) и возвращает список произошедших событий."""
        events: List[Event] = []
        if samples is None or len(samples) == 0:
            return events

        buf = np.concatenate([self._leftover, samples.astype(np.float32, copy=False)])
        n_full = len(buf) // self.frame_size
        self._leftover = buf[n_full * self.frame_size:]

        for i in range(n_full):
            frame = buf[i * self.frame_size:(i + 1) * self.frame_size]
            self._process_frame(frame, events)
        return events

    def _process_frame(self, frame: np.ndarray, events: List[Event]) -> None:
        speech = self.is_speech(frame)

        if not self._active:
            if speech:
                self._onset.append(frame)
                if len(self._onset) >= self.start_frames:
                    # Старт подтверждён — реплика включает накопленный онсет.
                    self._active = True
                    self._utterance = list(self._onset)
                    self._active_frames = len(self._onset)
                    self._onset = []
                    self._silence_run = 0
                    events.append((EVENT_SPEECH_STARTED, None))
            else:
                # Разрыв до подтверждения — сбрасываем онсет.
                self._onset = []
            return

        # Активная реплика.
        self._utterance.append(frame)
        self._active_frames += 1
        if speech:
            self._silence_run = 0
        else:
            self._silence_run += 1
            if self._silence_run >= self.silence_frames:
                self._finalize(events)
                return

        if self._active_frames >= self.max_frames:
            self._finalize(events)

    def _finalize(self, events: List[Event]) -> None:
        utter = (
            np.concatenate(self._utterance)
            if self._utterance
            else np.zeros(0, dtype=np.float32)
        )
        self._active = False
        self._utterance = []
        self._onset = []
        self._silence_run = 0
        self._active_frames = 0
        # Слишком короткая реплика (шум/щелчок) — старт уже отдали, но контент отбрасываем,
        # чтобы драйвер не гонял пустой STT. Отдаём событие с пустым массивом как маркер.
        if len(utter) < self.min_samples:
            events.append((EVENT_SPEECH_ENDED, np.zeros(0, dtype=np.float32)))
        else:
            events.append((EVENT_SPEECH_ENDED, utter))

    def flush(self) -> List[Event]:
        """Завершает незакрытую реплику (напр. при конце потока/паузе)."""
        events: List[Event] = []
        if self._active:
            self._finalize(events)
        return events

    @property
    def active(self) -> bool:
        return self._active
