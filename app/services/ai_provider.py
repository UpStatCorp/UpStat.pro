"""
Единая точка конфигурации LLM-провайдера для всего AI-слоя.

Позволяет переключать провайдера (OpenAI / YandexGPT / GigaChat / self-host vLLM)
и модели через переменные окружения — без правок бизнес-кода.
См. AI_LAYER_RF_MIGRATION_PLAN.md, этапы 1–2.

Переменные окружения:
  LLM_PROVIDER      openai | yandex | gigachat | vllm   (по умолчанию openai)
  OPENAI_BASE_URL   базовый URL OpenAI-совместимого API (пусто => дефолт провайдера)
  OPENAI_API_KEY    ключ OpenAI / vLLM
  LLM_MODEL_MAIN    основная модель   (по умолчанию gpt-4o; для yandex => yandexgpt)
  LLM_MODEL_MINI    лёгкая модель     (по умолчанию gpt-4o-mini; yandex => yandexgpt-lite)
  LLM_MODEL_VISION  vision-модель     (по умолчанию gpt-4o)
  LLM_TIMEOUT_SECONDS / LLM_MAX_RETRIES  сетевые защиты (общие для всех клиентов)

  # YandexGPT (LLM_PROVIDER=yandex):
  YANDEX_API_KEY    API-ключ сервисного аккаунта (роль ai.languageModels.user)
  YANDEX_FOLDER_ID  идентификатор каталога (folder) — подставляется в имя модели

  # STT (Whisper-фолбэк) намеренно отделён от LLM, чтобы смена LLM-провайдера
  # не ломала распознавание. Заменяется в этапе 3.
  OPENAI_STT_API_KEY / OPENAI_STT_BASE_URL  (по умолчанию — как у OpenAI)

Использование:
  from services.ai_provider import get_llm_client, model_main
  client = get_llm_client()
  client.chat.completions.create(model=model_main(), messages=[...])
"""

from __future__ import annotations

import os

from openai import OpenAI, AsyncOpenAI

# OpenAI-совместимый endpoint YandexGPT.
_YANDEX_ENDPOINT = "https://llm.api.cloud.yandex.net/v1"

# Маппинг привычных OpenAI-имён на короткие имена моделей YandexGPT —
# чтобы даже со «старым» LLM_MODEL_MAIN=gpt-4o в yandex-режиме собралось валидное имя.
_YANDEX_ALIASES = {
    "gpt-4o": "yandexgpt",
    "gpt-4o-mini": "yandexgpt-lite",
    "gpt-4o-vision": "yandexgpt",
}


def provider() -> str:
    return os.getenv("LLM_PROVIDER", "openai").strip().lower()


def _base_url() -> str | None:
    """Базовый URL OpenAI-совместимого API. Приоритет — явный OPENAI_BASE_URL,
    иначе дефолт провайдера (yandex => endpoint Яндекса), иначе None (OpenAI)."""
    url = os.getenv("OPENAI_BASE_URL", "").strip()
    if url:
        return url
    if provider() == "yandex":
        return _YANDEX_ENDPOINT
    return None


def _api_key() -> str:
    """Ключ API. Для yandex — YANDEX_API_KEY. Для self-host с base_url и без ключа
    подставляем непустую заглушку (SDK требует непустой api_key)."""
    if provider() == "yandex":
        key = os.getenv("YANDEX_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
        return key or "EMPTY"
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key and _base_url():
        return "EMPTY"
    return key


def _timeout() -> float:
    return float(os.getenv("LLM_TIMEOUT_SECONDS", "120"))


def _max_retries() -> int:
    return int(os.getenv("LLM_MAX_RETRIES", "3"))


def get_llm_client(api_key: str | None = None) -> OpenAI:
    """Синхронный OpenAI-совместимый клиент с base_url/timeout/retries из ENV.
    api_key можно передать явно (иначе берётся из конфигурации провайдера)."""
    return OpenAI(
        api_key=(api_key or _api_key()),
        base_url=_base_url(),
        timeout=_timeout(),
        max_retries=_max_retries(),
    )


def get_async_llm_client(api_key: str | None = None) -> AsyncOpenAI:
    """Асинхронный OpenAI-совместимый клиент с base_url/timeout/retries из ENV.
    api_key можно передать явно (иначе берётся из конфигурации провайдера)."""
    return AsyncOpenAI(
        api_key=(api_key or _api_key()),
        base_url=_base_url(),
        timeout=_timeout(),
        max_retries=_max_retries(),
    )


def effective_api_key() -> str:
    """Итоговый ключ для текущего провайдера (yandex => YANDEX_API_KEY и т.д.).
    Пригодно для guard-проверок «ключ задан?» вне фабрики."""
    key = _api_key()
    return "" if key == "EMPTY" else key


def get_stt_client() -> OpenAI:
    """Отдельный клиент для OpenAI Whisper (STT-фолбэк). Намеренно НЕ переключается
    вместе с LLM: смена LLM_PROVIDER на yandex не должна ломать распознавание.
    Полноценная замена STT — в этапе 3."""
    return OpenAI(
        api_key=os.getenv("OPENAI_STT_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip(),
        base_url=os.getenv("OPENAI_STT_BASE_URL", "").strip() or None,
        timeout=float(os.getenv("STT_TIMEOUT_SECONDS", "120")),
        max_retries=int(os.getenv("STT_MAX_RETRIES", "2")),
    )


def _resolve_model(name: str) -> str:
    """Для yandex собирает полное имя модели gpt://<folder>/<model>/latest.
    Полный URI (gpt://... или ds://...) пропускается как есть. Для остальных
    провайдеров возвращает имя без изменений."""
    name = (name or "").strip()
    if provider() != "yandex":
        return name
    if name.startswith(("gpt://", "ds://")):
        return name
    short = _YANDEX_ALIASES.get(name, name)
    folder = os.getenv("YANDEX_FOLDER_ID", "").strip()
    return f"gpt://{folder}/{short}/latest"


def resolve_model(name: str) -> str:
    """Публичный резолвер имени модели (для кода вне этого модуля, напр. voice_assistant,
    где имя берётся из своего конфига GPT_MODEL). В yandex-режиме собирает gpt://…/latest."""
    return _resolve_model(name)


def model_main() -> str:
    """Основная модель (аналог gpt-4o)."""
    default = "yandexgpt" if provider() == "yandex" else "gpt-4o"
    return _resolve_model(os.getenv("LLM_MODEL_MAIN", default))


def model_mini() -> str:
    """Лёгкая/дешёвая модель (аналог gpt-4o-mini)."""
    default = "yandexgpt-lite" if provider() == "yandex" else "gpt-4o-mini"
    return _resolve_model(os.getenv("LLM_MODEL_MINI", default))


def model_vision() -> str:
    """Мультимодальная (vision) модель. ВНИМАНИЕ: YandexGPT через этот endpoint
    vision не поддерживает — в yandex-режиме анализ изображений нужно отключать
    или выносить на отдельный провайдер (см. план §6.4)."""
    return _resolve_model(os.getenv("LLM_MODEL_VISION", "gpt-4o"))


# ── Возможности провайдера (каветы YandexGPT/self-host) ───────────────────────
# Все флаги — трёхпозиционные (auto|on|off): auto => дефолт по провайдеру,
# on/off => жёсткий форс из .env. Смысл: один код работает и на OpenAI (КЗ), и на
# YandexGPT (РФ); после PoC любой квет чинится сменой ОДНОЙ переменной окружения,
# без правок кода. См. AI_LAYER_RF_MIGRATION_PLAN.md §6.1, §6.4.

def _cap_flag(env_name: str, provider_default: bool) -> bool:
    """Трёхпозиционный ENV-флаг: on/true/1/yes => True, off/false/0/no => False,
    auto/пусто/иное => provider_default."""
    val = os.getenv(env_name, "auto").strip().lower()
    if val in ("on", "true", "1", "yes"):
        return True
    if val in ("off", "false", "0", "no"):
        return False
    return provider_default


def supports_json_mode() -> bool:
    """Поддерживает ли провайдер response_format={"type":"json_object"}.
    auto: включено (OpenAI/vLLM — гарантированно; YandexGPT — по докам да). Если PoC
    покажет отказ у YandexGPT — LLM_JSON_MODE=off (тогда работает фолбэк extract_json)."""
    return _cap_flag("LLM_JSON_MODE", True)


def json_mode_kwargs() -> dict:
    """kwargs для chat.completions.create: {'response_format': {...}} или {}.
    Разворачивается через ** в местах вызова. При LLM_JSON_MODE=off параметр просто
    не отправляется — ответ парсится устойчиво через extract_json()."""
    return {"response_format": {"type": "json_object"}} if supports_json_mode() else {}


def vision_enabled() -> bool:
    """Доступен ли анализ изображений (vision). auto: OpenAI/vLLM — да, YandexGPT — нет
    (endpoint vision не поддерживает). Форс: VISION_ENABLED=on|off."""
    return _cap_flag("VISION_ENABLED", provider() not in ("yandex",))


def max_output_tokens_cap() -> int | None:
    """Потолок max_tokens для ответа. auto: OpenAI/vLLM — без потолка (None), YandexGPT —
    8000 (лимит ниже gpt-4o). Явно: LLM_MAX_OUTPUT_TOKENS=<число> (0 => без потолка)."""
    raw = os.getenv("LLM_MAX_OUTPUT_TOKENS", "").strip()
    if raw:
        n = int(raw)
        return None if n <= 0 else n
    return 8000 if provider() == "yandex" else None


def clamp_max_tokens(n: int) -> int:
    """Ограничивает запрошенный max_tokens потолком провайдера (max_output_tokens_cap)."""
    cap = max_output_tokens_cap()
    return min(n, cap) if cap else n


def extract_json(raw: str):
    """Устойчивый парсинг JSON из ответа LLM. Снимает markdown-ограждение ```json … ```
    и обрезает до первого сбалансированного объекта/массива. Нужно, когда json-mode
    выключен (напр. YandexGPT без response_format) и модель добавляет обрамление/пояснения.
    Возвращает то же, что json.loads (dict/list). Бросает исключение, если JSON не найден."""
    import json as _json

    if raw is None:
        raise ValueError("empty LLM response")
    s = raw.strip()
    # снять ограждение ```json ... ``` или ``` ... ```
    if s.startswith("```"):
        s = s[3:]
        if s[:4].lower() == "json":
            s = s[4:]
        end = s.rfind("```")
        if end != -1:
            s = s[:end]
        s = s.strip()
    try:
        return _json.loads(s)
    except _json.JSONDecodeError:
        # найти первый сбалансированный { … } или [ … ], игнорируя скобки внутри строк
        start = next((i for i, ch in enumerate(s) if ch in "{["), -1)
        if start == -1:
            raise
        open_ch = s[start]
        close_ch = "}" if open_ch == "{" else "]"
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return _json.loads(s[start:i + 1])
        raise
