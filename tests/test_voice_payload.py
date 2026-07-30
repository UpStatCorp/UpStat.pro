"""Правила сборки объекта session.voice для Azure Voice Live.

Гейт по типу голоса — не косметика: на preview-версии API передача temperature
обычному neural-голосу приводила к отклонению session.update, то есть к сессии,
где ИИ молча не отвечает. Правила проверены запросами к живому ресурсу
(swedencentral, api-version 2026-07-15), здесь они зафиксированы без сети.
"""

import pytest

from voice_assistant.azure_voice_live import build_voice_payload, is_hd_voice

MAI_RU = "ru-RU-Lev:MAI-Voice-2-Flash"
DRAGON_HD = "en-US-Ava:DragonHDLatestNeural"
DRAGON_OMNI = "en-US-Andrew:DragonHDOmniLatestNeural"
PLAIN_NEURAL = "ru-RU-DmitryNeural"


@pytest.mark.parametrize("name", [MAI_RU, DRAGON_HD, DRAGON_OMNI])
def test_hd_voices_recognised(name):
    assert is_hd_voice(name) is True


@pytest.mark.parametrize("name", [PLAIN_NEURAL, "ru-RU-SvetlanaNeural", "", None])
def test_plain_voices_are_not_hd(name):
    assert is_hd_voice(name) is False


def test_type_is_always_azure_standard():
    assert build_voice_payload(PLAIN_NEURAL)["type"] == "azure-standard"


def test_hd_voice_gets_temperature_and_rate():
    payload = build_voice_payload(MAI_RU, temperature=0.85, rate="1.05")
    assert payload["temperature"] == 0.85
    assert payload["rate"] == "1.05"


def test_plain_voice_never_gets_temperature():
    """Ключевой инвариант: обычный neural-голос не должен получить temperature."""
    payload = build_voice_payload(PLAIN_NEURAL, temperature=0.85, rate="1.05")
    assert "temperature" not in payload
    # rate при этом допустим — он поддержан любыми standard-голосами.
    assert payload["rate"] == "1.05"


def test_fallback_voice_payload_is_bare():
    """Так конфигурация пересобирается при откате на запасной голос."""
    payload = build_voice_payload(PLAIN_NEURAL, temperature=None, rate=None, style=None)
    assert payload == {"name": PLAIN_NEURAL, "type": "azure-standard"}


def test_style_passed_through_when_set():
    payload = build_voice_payload(MAI_RU, style="encouraging")
    assert payload["style"] == "encouraging"


class TestVoiceChoiceWhitelist:
    """Выбор голоса пользователем идёт по ключу, а не по имени голоса.

    Это граница доверия: если бы сервер принимал имя голоса с фронта, клиент мог
    бы подставить произвольный — включая платный custom voice, который
    тарифицируется отдельно.
    """

    def test_known_keys_resolve(self):
        from voice_assistant.config import resolve_voice_choice

        assert resolve_voice_choice("male")[0] == "ru-RU-DmitryNeural"
        assert resolve_voice_choice("female")[0] == "ru-RU-SvetlanaNeural"

    def test_keys_are_case_and_space_tolerant(self):
        from voice_assistant.config import resolve_voice_choice

        assert resolve_voice_choice(" FEMALE ")[1] == "female"

    @pytest.mark.parametrize(
        "hostile",
        [
            "en-US-CustomExpensiveNeural",   # попытка подставить имя голоса напрямую
            "ru-RU-DmitryNeural",            # даже валидное имя не является ключом
            "",
            None,
            "../../etc/passwd",
        ],
    )
    def test_anything_but_a_known_key_is_rejected(self, hostile):
        from voice_assistant.config import resolve_voice_choice

        assert resolve_voice_choice(hostile) == (None, None)

    def test_voice_key_for_reverse_lookup(self):
        from voice_assistant.config import voice_key_for

        assert voice_key_for("ru-RU-SvetlanaNeural") == "female"
        assert voice_key_for("en-US-Ava:DragonHDLatestNeural") is None


def test_empty_optionals_are_omitted():
    """Пустые значения не должны превращаться в null-поля в session.update."""
    payload = build_voice_payload(MAI_RU, temperature=None, rate="", style="")
    assert set(payload) == {"name", "type"}
