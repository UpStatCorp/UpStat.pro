"""
Реестр SKU → capabilities и хелперы проверки доступа.

Источник истины — user.product_mode (денормализованное поле).

Политика дефолтов:
  - NULL product_mode   → FULL (обратная совместимость для СУЩЕСТВУЮЩИХ юзеров до v2)
  - product_mode="free" → FREE (новые самостоятельные регистрации без инвайта)
  - product_mode="full" → FULL (явное назначение через Sale Manager)
  - product_mode="train"→ TRAIN_RU / TRAIN_GLOBAL

ВАЖНО: новые самостоятельные регистрации (auth.py/google_oauth.py) ДОЛЖНЫ
получать product_mode="free", а не NULL. NULL → FULL оставлен только ради
обратной совместимости с уже существующими записями в БД.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.models import User

# ─── SKU-реестр ──────────────────────────────────────────────────────────────
# Ключи capabilities:
#   call_analysis     — загрузка/пайплайн анализа звонков, chat, chat_trener
#   crm               — CRM-интеграции, вебхуки
#   team_analytics    — командная аналитика по звонкам
#   owner_dashboard   — дашборд владельца (multi-team)
#   progress          — прогресс по анализам звонков
#   voice_training    — голосовые тренировки (WebSocket)
#   training_catalog  — каталог тренировок свободного выбора
#   training_program  — программа/стрик тренировок
#   train_report      — упрощённый отчёт РОПу по тренировкам

SKU_CAPABILITIES: dict[str, set[str]] = {
    "FULL": {
        "call_analysis",
        "crm",
        "team_analytics",
        "owner_dashboard",
        "progress",
        "voice_training",
        "training_catalog",
        "training_program",
        "train_report",
    },
    "TRAIN_RU": {
        "voice_training",
        "training_catalog",
        "training_program",
        "train_report",
    },
    "TRAIN_GLOBAL": {
        "voice_training",
        "training_catalog",
        "training_program",
        "train_report",
    },
    # FREE: нет платных функций; пользователь видит только базовый дашборд
    # и должен получить SKU от Sale Manager.
    "FREE": set(),
}

# product_mode → SKU по умолчанию
_MODE_TO_SKU: dict[str, str] = {
    "full": "FULL",
    "train": "TRAIN_RU",
    "free": "FREE",
}


def get_capabilities(user: "User") -> set[str]:
    """
    Возвращает набор capabilities пользователя.

    Приоритет:
      1. Organization.capabilities_override (JSON-патч поверх SKU)
      2. SKU организации (через user.organization.sku)
      3. user.product_mode → дефолтный SKU
      4. NULL product_mode → FULL (обратная совместимость)
    """
    # Определяем базовый SKU
    sku = _resolve_sku(user)

    base_caps: set[str] = set(SKU_CAPABILITIES.get(sku, SKU_CAPABILITIES["FULL"]))

    # Применяем capabilities_override из организации
    override = _get_override(user)
    if override:
        for key, value in override.items():
            if value:
                base_caps.add(key)
            else:
                base_caps.discard(key)

    return base_caps


def has_capability(user: "User", key: str) -> bool:
    """Проверяет, есть ли у пользователя capability key."""
    return key in get_capabilities(user)


def _resolve_sku(user: "User") -> str:
    """Определяет SKU пользователя."""
    # Приоритет 1: SKU из организации
    try:
        if user.organization and user.organization.sku:
            return user.organization.sku
    except Exception:
        pass

    # Приоритет 2: product_mode пользователя
    mode = getattr(user, "product_mode", None)
    if mode in _MODE_TO_SKU:
        return _MODE_TO_SKU[mode]

    # Дефолт: FULL (обратная совместимость для существующих NULL-юзеров)
    return "FULL"


def _get_override(user: "User") -> Optional[dict]:
    """Читает capabilities_override из организации пользователя."""
    try:
        if user.organization and user.organization.capabilities_override:
            return json.loads(user.organization.capabilities_override)
    except Exception:
        pass
    return None


def effective_product_mode(user: "User") -> str:
    """Возвращает эффективный режим пользователя: 'full' или 'train'."""
    caps = get_capabilities(user)
    if "call_analysis" in caps:
        return "full"
    return "train"
