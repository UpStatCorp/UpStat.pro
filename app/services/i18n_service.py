"""
Лёгкий i18n-сервис для train-платформы.

Дизайн (намеренно без Babel/.mo и runtime-зависимостей):
  • Исходный язык интерфейса — русский. Строки в шаблонах остаются русскими
    и являются ключами перевода. Для локали "ru" перевод — это сама строка
    (1:1), поэтому поведение для существующих RU-пользователей не меняется
    ни на байт.
  • Для локали "en" строка ищется в каталоге app/i18n/en.json. Если перевода
    нет — возвращается исходная (русская) строка как фоллбэк. Это безопасно:
    отсутствие перевода не ломает страницу.

Локаль определяется по SKU пользователя:
  • TRAIN_GLOBAL → "en"
  • всё остальное (TRAIN_RU / FULL / FREE / NULL) → "ru" (fail-safe)

ВАЖНО (AI-поведение): локаль системного промпта и этапных .txt строго
гейтится отдельно (см. load_stages(..., locale) и config.get_system_prompt).
EN-вариант применяется ТОЛЬКО если EN-контент явно создан; иначе работает
текущий RU-контент без изменений.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from functools import lru_cache
from typing import Optional

logger = logging.getLogger(__name__)

SUPPORTED_LOCALES = ("ru", "en")
DEFAULT_LOCALE = "ru"

_I18N_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "i18n")

# Названия месяцев для локализованного форматирования дат (без зависимости от
# системной локали ОС, которая в Docker-образе может отсутствовать).
_MONTHS = {
    "ru": [
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    ],
    "en": [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ],
}


@lru_cache(maxsize=len(SUPPORTED_LOCALES))
def _load_catalog(locale: str) -> dict:
    """Загружает JSON-каталог переводов для локали (кэшируется)."""
    path = os.path.join(_I18N_DIR, f"{locale}.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            logger.warning("i18n catalog %s is not an object, ignoring", path)
    except FileNotFoundError:
        pass
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load i18n catalog %s: %s", path, exc)
    return {}


def normalize_locale(locale: Optional[str]) -> str:
    """Приводит произвольное значение к поддерживаемой локали."""
    if locale and locale.lower() in SUPPORTED_LOCALES:
        return locale.lower()
    return DEFAULT_LOCALE


def resolve_locale(user) -> str:
    """
    Определяет локаль пользователя по его SKU.

    EN — только для TRAIN_GLOBAL. Любой сбой/неизвестность → "ru" (fail-safe),
    чтобы существующие RU-пользователи никогда не получили неожиданный язык.
    """
    if user is None:
        return DEFAULT_LOCALE
    try:
        from services.capability_service import _resolve_sku
    except ImportError:
        try:
            from app.services.capability_service import _resolve_sku
        except ImportError:
            return DEFAULT_LOCALE
    try:
        if _resolve_sku(user) == "TRAIN_GLOBAL":
            return "en"
    except Exception:
        pass
    return DEFAULT_LOCALE


def translate(message: str, locale: str) -> str:
    """Переводит строку. Для ru возвращает исходник; для en — из каталога."""
    locale = normalize_locale(locale)
    if locale == DEFAULT_LOCALE or not message:
        return message
    return _load_catalog(locale).get(message, message)


def localize_date(value, locale: str, with_year: bool = True) -> str:
    """
    Локализованное форматирование даты без зависимости от системной локали.

    ru: «17 июня 2026»   en: «June 17, 2026»
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        value = value.date()
    if not isinstance(value, date):
        return str(value)

    locale = normalize_locale(locale)
    month = _MONTHS[locale][value.month - 1]
    if locale == "en":
        s = f"{month} {value.day}"
        return f"{s}, {value.year}" if with_year else s
    s = f"{value.day} {month}"
    return f"{s} {value.year}" if with_year else s


def make_jinja_translator():
    """
    Возвращает context-aware функцию перевода для Jinja2.

    Локаль берётся из контекста рендеринга (`user`/`current_user`), поэтому
    не нужен ни глобальный contextvar, ни middleware: каждый шаблон уже
    получает пользователя.
    """
    from jinja2 import pass_context

    @pass_context
    def _gettext(ctx, message: str) -> str:
        user = ctx.get("user") or ctx.get("current_user")
        loc = ctx.get("locale") or (resolve_locale(user) if user else DEFAULT_LOCALE)
        return translate(message, loc)

    return _gettext


def make_jinja_dateformat():
    """Возвращает context-aware фильтр локализованной даты для Jinja2."""
    from jinja2 import pass_context

    @pass_context
    def _localdate(ctx, value, with_year: bool = True) -> str:
        user = ctx.get("user") or ctx.get("current_user")
        loc = ctx.get("locale") or (resolve_locale(user) if user else DEFAULT_LOCALE)
        return localize_date(value, loc, with_year=with_year)

    return _localdate
