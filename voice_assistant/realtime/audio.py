"""Аудио-конвертация на границе браузера ↔ локальный конвейер.

Контракт провода (совпадает с Azure Voice Live и фронтом voice-training.js):
  вход и выход — base64(PCM16 little-endian, mono) при WIRE_SAMPLE_RATE = 24000 Гц.
Внутренний конвейер STT работает при INTERNAL_SAMPLE_RATE = 16000 (SAMPLE_RATE).

Все функции чистые и покрыты тестами (tests/test_realtime_audio.py). Ресемплинг —
линейная интерполяция (тот же метод, что в app/static/js/audio-processor.js:
достаточно для речи, без зависимости от scipy).
"""
from __future__ import annotations

import base64

import numpy as np

# Частота на проводе — та, что шлёт/ждёт браузер (AudioWorklet и playAudioChunks = 24000).
WIRE_SAMPLE_RATE = 24000
# Внутренняя частота конвейера (Whisper/SpeechKit STT) = config.SAMPLE_RATE. Берём из
# конфига, чтобы не рассинхронизироваться с TTS (все провайдеры ресемплят в SAMPLE_RATE).
try:
    from ..config import SAMPLE_RATE as INTERNAL_SAMPLE_RATE
except Exception:  # pragma: no cover — на случай импорта вне пакета
    INTERNAL_SAMPLE_RATE = 16000

_INT16_MAX = 32767.0


def pcm16_to_float32(pcm: bytes) -> np.ndarray:
    """PCM16 LE (bytes) → float32 [-1..1]. Пустой/нечётный хвост обрабатывается безопасно."""
    if not pcm:
        return np.zeros(0, dtype=np.float32)
    # np.frombuffer требует длину кратную 2 (int16); отбросим нечётный байт-хвост.
    if len(pcm) % 2:
        pcm = pcm[:-1]
    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / _INT16_MAX


def float32_to_pcm16(audio: np.ndarray) -> bytes:
    """float32 [-1..1] → PCM16 LE (bytes). Клиппинг до [-1,1] перед квантованием."""
    if audio is None or len(audio) == 0:
        return b""
    clipped = np.clip(audio, -1.0, 1.0)
    return (clipped * _INT16_MAX).round().astype("<i2").tobytes()


def b64_to_float32(data: str) -> np.ndarray:
    """base64(PCM16 LE) → float32. Пустая строка → пустой массив."""
    if not data:
        return np.zeros(0, dtype=np.float32)
    return pcm16_to_float32(base64.b64decode(data))


def float32_to_b64(audio: np.ndarray) -> str:
    """float32 → base64(PCM16 LE). Пустой массив → пустая строка."""
    raw = float32_to_pcm16(audio)
    return base64.b64encode(raw).decode("ascii") if raw else ""


def resample_linear(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Линейный ресемплинг float32. src_rate == dst_rate → возврат как есть.
    Пустой вход → пустой массив. Тот же алгоритм, что в audio-processor.js."""
    if audio is None or len(audio) == 0:
        return np.zeros(0, dtype=np.float32)
    if src_rate == dst_rate:
        return audio.astype(np.float32, copy=False)
    n_out = int(round(len(audio) * dst_rate / src_rate))
    if n_out <= 0:
        return np.zeros(0, dtype=np.float32)
    # Позиции выходных сэмплов в координатах входа: [0 .. len-1].
    src_idx = np.arange(n_out, dtype=np.float64) * (src_rate / dst_rate)
    xp = np.arange(len(audio), dtype=np.float64)
    return np.interp(src_idx, xp, audio.astype(np.float64)).astype(np.float32)


def wire_b64_to_internal(data: str) -> np.ndarray:
    """Входящий чанк от браузера: base64(PCM16@24k) → float32@16k для STT."""
    return resample_linear(b64_to_float32(data), WIRE_SAMPLE_RATE, INTERNAL_SAMPLE_RATE)


def internal_to_wire_b64(audio: np.ndarray, src_rate: int = INTERNAL_SAMPLE_RATE) -> str:
    """Исходящий аудио-чанк TTS: float32@src_rate → base64(PCM16@24k) для фронта."""
    return float32_to_b64(resample_linear(audio, src_rate, WIRE_SAMPLE_RATE))
