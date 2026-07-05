"""
Тест ВЫХОДНОГО контракта адаптера SpeechKit: результат распознавания →
{"text","words"} → _words_to_turns (диалог со спикерами).

Проверяет, что words из SpeechKit корректно разворачиваются в реплики
speaker_1/speaker_2 тем же кодом, что и для ElevenLabs. Сеть не вызывается.

ВНИМАНИЕ: тест фиксирует ВЫХОДНОЙ контракт (что pipeline получит корректные
words). ВХОДНУЮ форму ответа SpeechKit v3 нужно подтвердить на PoC с реальным
ключом — при расхождении правится только _words_from_v3_result().
"""

import os

# database.py требует DATABASE_URL при импорте pipeline (соединение не создаётся).
os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost:5432/db")

from services.speechkit_stt import parse_recognition, _ms_to_sec  # noqa: E402
from services.pipeline import _words_to_turns  # noqa: E402


# Синтетический ответ SpeechKit v3 (два спикера через channelTag).
SAMPLE_V3 = {
    "result": [
        {"final": {
            "channelTag": "1",
            "alternatives": [{
                "text": "Здравствуйте это компания",
                "words": [
                    {"text": "Здравствуйте", "startTimeMs": "100", "endTimeMs": "600"},
                    {"text": "это", "startTimeMs": "600", "endTimeMs": "800"},
                    {"text": "компания", "startTimeMs": "800", "endTimeMs": "1200"},
                ],
            }],
        }},
        {"final": {
            "channelTag": "2",
            "alternatives": [{
                "text": "Да слушаю вас",
                "words": [
                    {"text": "Да", "startTimeMs": "1500", "endTimeMs": "1700"},
                    {"text": "слушаю", "startTimeMs": "1700", "endTimeMs": "2100"},
                    {"text": "вас", "startTimeMs": "2100", "endTimeMs": "2300"},
                ],
            }],
        }},
        {"final": {
            "channelTag": "1",
            "alternatives": [{
                "text": "Хочу предложить услугу",
                "words": [
                    {"text": "Хочу", "startTimeMs": "2400", "endTimeMs": "2700"},
                    {"text": "предложить", "startTimeMs": "2700", "endTimeMs": "3200"},
                    {"text": "услугу", "startTimeMs": "3200", "endTimeMs": "3600"},
                ],
            }],
        }},
    ]
}


def test_ms_to_sec():
    assert _ms_to_sec("100") == 0.1
    assert _ms_to_sec("1500") == 1.5
    assert _ms_to_sec(None) == 0.0
    assert _ms_to_sec("bad") == 0.0


def test_parse_recognition_words_contract():
    """parse_recognition даёт words с ключами speaker_id/text/start/end."""
    out = parse_recognition(SAMPLE_V3)
    assert set(out.keys()) == {"text", "words"}
    assert len(out["words"]) == 9  # 3 + 3 + 3
    w = out["words"][0]
    assert set(w.keys()) == {"speaker_id", "text", "start", "end"}
    assert w["speaker_id"] == "speaker_1"
    assert w["text"] == "Здравствуйте"
    assert w["start"] == 0.1 and w["end"] == 0.6
    # два разных спикера присутствуют
    speakers = {x["speaker_id"] for x in out["words"]}
    assert speakers == {"speaker_1", "speaker_2"}
    assert "Здравствуйте" in out["text"] and "слушаю" in out["text"]


def test_words_to_turns_from_speechkit():
    """Слова SpeechKit разворачиваются в диалог 2 спикеров тем же кодом, что и ElevenLabs."""
    out = parse_recognition(SAMPLE_V3)
    dialogue = _words_to_turns(out["words"])

    # Два спикера
    labels = {s["label"] for s in dialogue["speakers"]}
    assert labels == {"speaker_1", "speaker_2"}

    turns = dialogue["turns"]
    # 3 реплики: speaker_1, speaker_2, speaker_1 (смена спикера => новая реплика)
    assert [t["speaker"] for t in turns] == ["speaker_1", "speaker_2", "speaker_1"]
    assert turns[0]["text"] == "Здравствуйте это компания"
    assert turns[1]["text"] == "Да слушаю вас"
    assert turns[2]["text"] == "Хочу предложить услугу"
    # таймкоды проставлены
    assert turns[0]["start"] == 0.1
    assert turns[1]["start"] == 1.5


def test_empty_payload():
    out = parse_recognition({})
    assert out == {"text": "", "words": []}
    assert _words_to_turns([])["turns"] == []
