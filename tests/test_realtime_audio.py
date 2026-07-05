"""Тесты аудио-конвертации локального realtime (voice_assistant/realtime/audio.py).

Проверяют контракт провода PCM16@24k ⇄ float32@16k без сети и ключей.
"""
import base64

import numpy as np
import pytest

from voice_assistant.realtime.audio import (
    pcm16_to_float32,
    float32_to_pcm16,
    b64_to_float32,
    float32_to_b64,
    resample_linear,
    wire_b64_to_internal,
    internal_to_wire_b64,
    WIRE_SAMPLE_RATE,
    INTERNAL_SAMPLE_RATE,
)


def test_pcm16_roundtrip_within_quantization():
    orig = np.array([0.0, 0.5, -0.5, 0.9, -0.9], dtype=np.float32)
    back = pcm16_to_float32(float32_to_pcm16(orig))
    assert back.shape == orig.shape
    assert np.max(np.abs(back - orig)) < 1e-3  # ошибка квантования int16


def test_pcm16_empty_and_odd_tail():
    assert pcm16_to_float32(b"") .size == 0
    assert float32_to_pcm16(np.zeros(0, dtype=np.float32)) == b""
    # нечётный байтовый хвост не должен ронять frombuffer
    assert pcm16_to_float32(b"\x01\x02\x03").size == 1


def test_float32_to_pcm16_clips():
    loud = np.array([2.0, -2.0], dtype=np.float32)
    pcm = float32_to_pcm16(loud)
    ints = np.frombuffer(pcm, dtype="<i2")
    assert ints[0] == 32767 and ints[1] == -32767


def test_b64_roundtrip():
    orig = np.array([0.1, -0.2, 0.3], dtype=np.float32)
    s = float32_to_b64(orig)
    assert isinstance(s, str)
    # это валидный base64
    base64.b64decode(s)
    back = b64_to_float32(s)
    assert np.max(np.abs(back - orig)) < 1e-3


def test_b64_empty():
    assert float32_to_b64(np.zeros(0, dtype=np.float32)) == ""
    assert b64_to_float32("").size == 0


def test_resample_identity():
    a = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    out = resample_linear(a, 16000, 16000)
    assert np.array_equal(out, a)


def test_resample_downsample_length():
    a = np.ones(24000, dtype=np.float32)
    out = resample_linear(a, 24000, 16000)
    assert out.shape[0] == 16000
    # константа остаётся константой при линейной интерполяции
    assert np.allclose(out, 1.0)


def test_resample_upsample_length():
    a = np.ones(16000, dtype=np.float32)
    out = resample_linear(a, 16000, 24000)
    assert out.shape[0] == 24000


def test_resample_empty():
    assert resample_linear(np.zeros(0, dtype=np.float32), 24000, 16000).size == 0


def test_wire_helpers_shapes():
    # 240 мс речи на проводе (24k) → внутренние 16k
    wire = np.zeros(int(WIRE_SAMPLE_RATE * 0.24), dtype=np.float32)
    b64 = float32_to_b64(wire)
    internal = wire_b64_to_internal(b64)
    assert internal.shape[0] == int(INTERNAL_SAMPLE_RATE * 0.24)
    # обратно на провод
    out_b64 = internal_to_wire_b64(internal, src_rate=INTERNAL_SAMPLE_RATE)
    out = b64_to_float32(out_b64)
    assert out.shape[0] == int(WIRE_SAMPLE_RATE * 0.24)
