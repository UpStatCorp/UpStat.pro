"""Тесты оркестратора LocalRealtimeDriver (voice_assistant/realtime/driver.py).

Все внешние блоки (STT/LLM/TTS/send/хуки) — фейки, поэтому проверяем именно связки
и последовательность protocol-событий, без сети и ключей. Async гоняем через
asyncio.run (без зависимости от pytest-asyncio).
"""
import asyncio

import numpy as np
import pytest

from voice_assistant.realtime.driver import LocalRealtimeDriver
from voice_assistant.realtime.segmenter import UtteranceSegmenter, energy_classifier
from voice_assistant.realtime.audio import (
    float32_to_b64,
    WIRE_SAMPLE_RATE,
)

WIRE = WIRE_SAMPLE_RATE


# ── фейки ─────────────────────────────────────────────────────────────────────

class Recorder:
    def __init__(self):
        self.events = []

    async def __call__(self, ev):
        self.events.append(ev)

    def types(self):
        return [e["type"] for e in self.events]

    def first(self, t):
        return next((e for e in self.events if e["type"] == t), None)


class FakeSTT:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def transcribe(self, audio, language="ru"):
        self.calls.append((len(audio), language))
        return self.text


class FakeDialogue:
    def __init__(self, reply):
        self.reply = reply
        self.inputs = []

    async def get_response(self, text):
        self.inputs.append(text)
        return self.reply


class FakeTTS:
    def __init__(self, n_chunks=2):
        self.calls = []
        self._chunks = [np.full(160, 0.1, dtype=np.float32) for _ in range(n_chunks)]

    async def synthesize(self, text):
        self.calls.append(text)
        for c in self._chunks:
            yield c


def _seg():
    return UtteranceSegmenter(
        is_speech=energy_classifier(0.01),
        frame_ms=30, start_ms=90, silence_ms=300, min_utter_ms=100,
    )


def _wire_tone(ms, amp=0.3, freq=300.0):
    n = int(WIRE * ms / 1000)
    t = np.arange(n, dtype=np.float32) / WIRE
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _wire_silence(ms):
    return np.zeros(int(WIRE * ms / 1000), dtype=np.float32)


async def _feed(driver, wire_audio, chunk_ms=50):
    chunk = int(WIRE * chunk_ms / 1000)
    for i in range(0, len(wire_audio), chunk):
        b64 = float32_to_b64(wire_audio[i:i + chunk])
        await driver.handle_audio_b64(b64)
    # турны теперь фоновые — дожидаемся их завершения
    await driver.wait_idle()


def _drive(**kw):
    rec = Recorder()
    stt = kw.pop("stt", FakeSTT("привет тренер"))
    dlg = kw.pop("dialogue", FakeDialogue("Здравствуйте! Начнём тренировку."))
    tts = kw.pop("tts", FakeTTS())
    kw.setdefault("turn_cooldown_ms", 0)  # без cooldown в тестах (детерминизм)
    driver = LocalRealtimeDriver(
        send=rec, stt=stt, dialogue=dlg, tts=tts, segmenter=_seg(), **kw
    )
    return rec, stt, dlg, tts, driver


# ── тесты ─────────────────────────────────────────────────────────────────────

def test_full_turn_event_sequence():
    rec, stt, dlg, tts, driver = _drive()
    audio = np.concatenate([_wire_silence(150), _wire_tone(300), _wire_silence(600)])
    asyncio.run(_feed(driver, audio))

    types = rec.types()
    assert types[0] == "input_audio_buffer.speech_started"
    assert "user_text" in types
    assert "status" in types
    assert "ai_text" in types
    assert "response.audio.delta" in types
    assert "response.audio.done" in types
    # терминальное событие турна — status: completed (сбрасывает состояние фронта)
    assert rec.events[-1] == {"type": "status", "status": "completed",
                              "response_id": rec.first("response.audio.done")["response_id"]}
    # порядок: user_text до ai_text до audio.done
    assert types.index("user_text") < types.index("ai_text") < types.index("response.audio.done")

    # связки: STT получил аудио, LLM получил транскрипт, TTS — ответ
    assert stt.calls and stt.calls[0][0] > 0
    assert dlg.inputs == ["привет тренер"]
    assert tts.calls == ["Здравствуйте! Начнём тренировку."]
    assert rec.first("user_text")["text"] == "привет тренер"
    assert rec.first("ai_text")["text"] == "Здравствуйте! Начнём тренировку."


def test_response_id_consistent_across_turn():
    rec, *_ , driver = _drive()
    audio = np.concatenate([_wire_silence(150), _wire_tone(300), _wire_silence(600)])
    asyncio.run(_feed(driver, audio))
    rid = rec.first("status")["response_id"]
    assert rid
    assert rec.first("response.audio.delta")["response_id"] == rid
    assert rec.first("response.audio.done")["response_id"] == rid


def test_half_duplex_ignores_audio_while_speaking():
    rec, stt, dlg, tts, driver = _drive()
    driver._speaking = True  # эмулируем «ИИ говорит»

    async def go():
        await _feed(driver, np.concatenate([_wire_tone(300), _wire_silence(600)]))

    asyncio.run(go())
    assert rec.events == []          # ничего не отправлено
    assert stt.calls == []           # STT не вызывался


def test_empty_transcript_skips_reply():
    rec, stt, dlg, tts, driver = _drive(stt=FakeSTT("   "))
    audio = np.concatenate([_wire_silence(150), _wire_tone(300), _wire_silence(600)])
    asyncio.run(_feed(driver, audio))
    types = rec.types()
    assert "input_audio_buffer.speech_started" in types
    assert "user_text" not in types      # пустой STT → нет реплики юзера
    assert "ai_text" not in types
    assert dlg.inputs == []              # LLM не дёргали


def test_stage_action_invoked_after_audio():
    rec, stt, dlg, tts, driver = _drive(
        dialogue=FakeDialogue("Готово. [STAGE_COMPLETE]"),
    )
    actions = []

    async def on_stage(a):
        actions.append((a, list(rec.types())))

    driver.on_stage_action = on_stage
    driver.process_ai_reply = lambda t: (t.replace("[STAGE_COMPLETE]", "").strip(), "next_stage")

    audio = np.concatenate([_wire_silence(150), _wire_tone(300), _wire_silence(600)])
    asyncio.run(_feed(driver, audio))

    assert actions and actions[0][0] == "next_stage"
    # переход вызван ПОСЛЕ response.audio.done
    assert "response.audio.done" in actions[0][1]
    # тег вырезан из текста для пользователя
    assert rec.first("ai_text")["text"] == "Готово."


def test_kickoff_speaks_without_user_text():
    rec, stt, dlg, tts, driver = _drive(dialogue=FakeDialogue("Добро пожаловать!"))
    asyncio.run(driver.kickoff("Поприветствуй пользователя"))
    types = rec.types()
    assert "user_text" not in types
    assert "ai_text" in types
    assert "response.audio.done" in types
    assert types[-1] == "status"   # терминальный completed
    assert dlg.inputs == ["Поприветствуй пользователя"]


def test_redact_applied_to_user_and_ai_text():
    rec, stt, dlg, tts, driver = _drive(
        stt=FakeSTT("мой телефон PHONE"),
        dialogue=FakeDialogue("ваш PHONE записан"),
    )
    driver.redact = lambda t: t.replace("PHONE", "***")
    audio = np.concatenate([_wire_silence(150), _wire_tone(300), _wire_silence(600)])
    asyncio.run(_feed(driver, audio))
    assert rec.first("user_text")["text"] == "мой телефон ***"
    assert rec.first("ai_text")["text"] == "ваш *** записан"


def test_llm_failure_does_not_crash_turn():
    class BoomDialogue:
        async def get_response(self, text):
            raise RuntimeError("LLM down")

    rec, stt, _, tts, driver = _drive(dialogue=BoomDialogue())
    audio = np.concatenate([_wire_silence(150), _wire_tone(300), _wire_silence(600)])
    asyncio.run(_feed(driver, audio))   # не должно бросить
    types = rec.types()
    assert "user_text" in types          # STT прошёл
    assert "ai_text" not in types        # ответа нет
    assert "response.audio.done" in types  # турн корректно завершён


def test_ai_text_saved_to_db_is_redacted():
    # регресс из ревью (HIGH #2): ответ ИИ должен уходить в БД РЕДАКТИРОВАННЫМ, не только на клиент.
    saved = []

    rec, stt, dlg, tts, driver = _drive(dialogue=FakeDialogue("звоните на PHONE"))
    driver.redact = lambda t: t.replace("PHONE", "***")

    async def _save_ai(t):
        saved.append(t)

    driver.on_ai_text = _save_ai
    audio = np.concatenate([_wire_silence(150), _wire_tone(300), _wire_silence(600)])
    asyncio.run(_feed(driver, audio))
    assert saved == ["звоните на ***"]           # в «БД» — редактированный текст
    assert rec.first("ai_text")["text"] == "звоните на ***"


def test_handle_audio_returns_before_turn_completes():
    # HIGH #1: чтение сокета не должно блокироваться на весь турн. Медленный TTS —
    # handle_audio_b64 обязан вернуться, а событие ai_text появиться только после wait_idle.
    class SlowTTS(FakeTTS):
        async def synthesize(self, text):
            await asyncio.sleep(0.05)
            for c in self._chunks:
                yield c

    rec, stt, dlg, tts, driver = _drive(tts=SlowTTS())
    audio = np.concatenate([_wire_silence(150), _wire_tone(300), _wire_silence(600)])

    async def go():
        chunk = int(WIRE * 50 / 1000)
        for i in range(0, len(audio), chunk):
            await driver.handle_audio_b64(float32_to_b64(audio[i:i + chunk]))
        # турн ещё идёт (SlowTTS спит) — done ещё не пришёл
        assert "response.audio.done" not in rec.types()
        assert driver._speaking is True
        await driver.wait_idle()
        assert "response.audio.done" in rec.types()

    asyncio.run(go())


def test_cooldown_drops_immediate_followup():
    rec, stt, dlg, tts, driver = _drive(turn_cooldown_ms=10000)  # длинный cooldown
    audio = np.concatenate([_wire_silence(150), _wire_tone(300), _wire_silence(600)])
    asyncio.run(_feed(driver, audio))
    assert stt.calls and len(stt.calls) == 1
    # сразу после турна — новая речь должна быть отброшена (cooldown активен)
    asyncio.run(_feed(driver, np.concatenate([_wire_tone(300), _wire_silence(600)])))
    assert len(stt.calls) == 1   # второй STT не случился
