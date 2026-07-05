"""
Тест выходного контракта SpeechKit TTS: конвертация LPCM (signed 16-bit LE),
которую отдаёт SpeechKit при format=lpcm, в numpy float32 с нормализацией —
тот же формат чанков, что у OpenAI/ElevenLabs путей. Сеть не вызывается.
"""

import numpy as np

from voice_assistant.tts_response import _pcm16_to_float32


def _pack(samples):
    return np.array(samples, dtype=np.int16).tobytes()


def test_pcm16_to_float32_basic_and_normalized():
    # int16 сэмплы; максимум по модулю = 32767 => после нормализации пик = 0.9
    pcm = _pack([0, 16383, -16384, 32767])
    arr = _pcm16_to_float32(pcm)
    assert arr.dtype == np.float32
    assert arr.shape == (4,)
    # нормализация до 0.9 по пику
    assert abs(float(np.max(np.abs(arr))) - 0.9) < 1e-4
    # знак сохранён
    assert arr[1] > 0 and arr[2] < 0
    assert abs(arr[0]) < 1e-6


def test_pcm16_to_float32_empty():
    out = _pcm16_to_float32(b"")
    assert out.dtype == np.float32
    assert out.size == 0


def test_pcm16_to_float32_silence_no_div_by_zero():
    # тишина (все нули) — не должно быть деления на ноль
    arr = _pcm16_to_float32(_pack([0, 0, 0]))
    assert arr.size == 3
    assert float(np.max(np.abs(arr))) == 0.0
