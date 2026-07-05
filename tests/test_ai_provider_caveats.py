"""Тесты каветов провайдера в services.ai_provider (без ключей и сети).

Покрывают трёхпозиционные флаги (auto|on|off), потолок max_tokens и устойчивый
парсинг JSON (extract_json). Цель — гарантировать, что один код работает и под
OpenAI (КЗ), и под YandexGPT (РФ), а любой квет чинится сменой ОДНОЙ переменной.
См. AI_LAYER_RF_MIGRATION_PLAN.md §6.1, §6.4.
"""
import json

import pytest

from services.ai_provider import (
    supports_json_mode,
    json_mode_kwargs,
    vision_enabled,
    max_output_tokens_cap,
    clamp_max_tokens,
    extract_json,
)

# Переменные, которые тесты трогают — сбрасываем перед каждым тестом,
# чтобы окружение разработчика не влияло на результат.
_ENV_KEYS = ("LLM_PROVIDER", "LLM_JSON_MODE", "VISION_ENABLED", "LLM_MAX_OUTPUT_TOKENS")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    yield


# ── json-mode ────────────────────────────────────────────────────────────────

def test_json_mode_auto_default_on(monkeypatch):
    # auto (по умолчанию) => включено для любого провайдера
    assert supports_json_mode() is True
    assert json_mode_kwargs() == {"response_format": {"type": "json_object"}}


def test_json_mode_off_omits_param(monkeypatch):
    monkeypatch.setenv("LLM_JSON_MODE", "off")
    assert supports_json_mode() is False
    assert json_mode_kwargs() == {}


def test_json_mode_explicit_on(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "yandex")
    monkeypatch.setenv("LLM_JSON_MODE", "on")
    assert supports_json_mode() is True
    assert "response_format" in json_mode_kwargs()


# ── vision ───────────────────────────────────────────────────────────────────

def test_vision_openai_default_enabled(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert vision_enabled() is True


def test_vision_yandex_default_disabled(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "yandex")
    assert vision_enabled() is False


def test_vision_force_on_for_yandex(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "yandex")
    monkeypatch.setenv("VISION_ENABLED", "on")
    assert vision_enabled() is True


# ── max_tokens cap ───────────────────────────────────────────────────────────

def test_max_tokens_openai_no_cap(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert max_output_tokens_cap() is None
    assert clamp_max_tokens(12000) == 12000


def test_max_tokens_yandex_default_cap(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "yandex")
    assert max_output_tokens_cap() == 8000
    assert clamp_max_tokens(12000) == 8000
    assert clamp_max_tokens(4096) == 4096  # ниже потолка — не трогаем


def test_max_tokens_explicit_override(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "5000")
    assert max_output_tokens_cap() == 5000
    assert clamp_max_tokens(12000) == 5000


def test_max_tokens_explicit_zero_means_no_cap(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "yandex")
    monkeypatch.setenv("LLM_MAX_OUTPUT_TOKENS", "0")
    assert max_output_tokens_cap() is None
    assert clamp_max_tokens(12000) == 12000


# ── extract_json ─────────────────────────────────────────────────────────────

def test_extract_json_plain():
    assert extract_json('{"score": 85}') == {"score": 85}


def test_extract_json_fenced_json():
    raw = '```json\n{"a": 1, "b": [1, 2]}\n```'
    assert extract_json(raw) == {"a": 1, "b": [1, 2]}


def test_extract_json_fenced_plain():
    raw = '```\n{"ok": true}\n```'
    assert extract_json(raw) == {"ok": True}


def test_extract_json_with_prose_prefix_and_suffix():
    raw = 'Вот результат анализа:\n{"stage_scores": {"x": 3}}\nНадеюсь, помог!'
    assert extract_json(raw) == {"stage_scores": {"x": 3}}


def test_extract_json_array():
    raw = 'Ответ: [1, 2, 3] — всё.'
    assert extract_json(raw) == [1, 2, 3]


def test_extract_json_braces_inside_strings():
    # Скобки внутри строкового значения не должны сбивать балансировку.
    raw = '{"text": "цена {350} руб. [скидка]", "n": 1}'
    assert extract_json(raw) == {"text": "цена {350} руб. [скидка]", "n": 1}


def test_extract_json_matches_json_loads_on_clean_input():
    payload = {"recommendations": [{"stage": "opening", "text": "hi"}], "n": 2}
    raw = json.dumps(payload, ensure_ascii=False)
    assert extract_json(raw) == payload


def test_extract_json_raises_when_no_json():
    with pytest.raises(Exception):
        extract_json("тут нет никакого json вообще")
