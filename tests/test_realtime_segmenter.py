"""Тесты server-side VAD сегментатора (voice_assistant/realtime/segmenter.py).

Синтетическое аудио (тишина + тональные всплески) → проверяем корректность
границ реплик, дебаунс старта, hangover тишины, max/min длину и flush.
Классификатор задаём явно (energy 0.01), чтобы тест не зависел от .env.
"""
import numpy as np
import pytest

from voice_assistant.realtime.segmenter import (
    UtteranceSegmenter,
    energy_classifier,
    EVENT_SPEECH_STARTED,
    EVENT_SPEECH_ENDED,
)

SR = 16000
FRAME_MS = 30


def _seg(**kw):
    params = dict(
        sample_rate=SR,
        is_speech=energy_classifier(0.01),
        frame_ms=FRAME_MS,
        start_ms=150,     # 5 кадров
        silence_ms=600,   # 20 кадров
        max_utter_ms=25000,
        min_utter_ms=200,
    )
    params.update(kw)
    return UtteranceSegmenter(**params)


def _tone(ms, amp=0.3, freq=300.0):
    n = int(SR * ms / 1000)
    t = np.arange(n, dtype=np.float32) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _silence(ms):
    return np.zeros(int(SR * ms / 1000), dtype=np.float32)


def _types(events):
    return [e[0] for e in events]


def test_pure_silence_no_events():
    seg = _seg()
    events = seg.push(_silence(2000))
    assert events == []
    assert not seg.active


def test_single_utterance_start_and_end():
    seg = _seg()
    audio = np.concatenate([_silence(300), _tone(400), _silence(900)])
    events = seg.push(audio)
    assert _types(events) == [EVENT_SPEECH_STARTED, EVENT_SPEECH_ENDED]
    # реплика не пустая
    _, utter = events[1]
    assert utter is not None and utter.size > 0


def test_two_utterances():
    seg = _seg()
    audio = np.concatenate([
        _silence(200), _tone(400), _silence(900),
        _tone(400), _silence(900),
    ])
    events = seg.push(audio)
    assert _types(events) == [
        EVENT_SPEECH_STARTED, EVENT_SPEECH_ENDED,
        EVENT_SPEECH_STARTED, EVENT_SPEECH_ENDED,
    ]


def test_short_blip_below_start_debounce_ignored():
    seg = _seg()
    # 60 мс речи (2 кадра) < start 150 мс (5 кадров) → старт не подтверждён
    audio = np.concatenate([_silence(200), _tone(60), _silence(900)])
    events = seg.push(audio)
    assert events == []
    assert not seg.active


def test_chunked_push_matches_single_push():
    audio = np.concatenate([_silence(300), _tone(400), _silence(900)])
    # цельным куском
    whole = _seg().push(audio)
    # по 50 мс (как приходит с провода)
    seg = _seg()
    chunk = int(SR * 50 / 1000)
    got = []
    for i in range(0, len(audio), chunk):
        got.extend(seg.push(audio[i:i + chunk]))
    assert _types(got) == _types(whole)


def test_flush_finalizes_open_utterance():
    seg = _seg()
    # речь без завершающей тишины
    events = seg.push(np.concatenate([_silence(200), _tone(400)]))
    assert _types(events) == [EVENT_SPEECH_STARTED]
    assert seg.active
    flushed = seg.flush()
    assert _types(flushed) == [EVENT_SPEECH_ENDED]
    assert not seg.active


def test_max_utterance_forces_finalize():
    # max = 300 мс: длинная речь принудительно закрывается
    seg = _seg(start_ms=60, silence_ms=600, max_utter_ms=300)
    events = seg.push(_tone(2000))
    assert EVENT_SPEECH_STARTED in _types(events)
    assert EVENT_SPEECH_ENDED in _types(events)


def test_min_utterance_emits_empty_marker():
    # старт подтверждается за 1 кадр (30 мс), но min=5000 мс → контент отбрасывается,
    # speech_ended приходит с пустым массивом-маркером.
    seg = _seg(start_ms=30, min_utter_ms=5000)
    audio = np.concatenate([_tone(90), _silence(900)])
    events = seg.push(audio)
    assert _types(events) == [EVENT_SPEECH_STARTED, EVENT_SPEECH_ENDED]
    _, utter = events[1]
    assert utter is not None and utter.size == 0


def test_reset_clears_state():
    seg = _seg()
    seg.push(np.concatenate([_silence(200), _tone(400)]))
    assert seg.active
    seg.reset()
    assert not seg.active
    # после reset тишина не даёт событий
    assert seg.push(_silence(900)) == []
