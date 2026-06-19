"""
Тесты i18n-сервиса train-платформы.

Фокус — на двух критичных свойствах:
  1. Локаль определяется по SKU: EN только для TRAIN_GLOBAL, иначе RU (fail-safe).
  2. Для RU переводы 1:1 (поведение существующих пользователей не меняется),
     отсутствующие EN-переводы безопасно фоллбэчат на исходную строку.
"""

from datetime import date, datetime
from unittest.mock import patch

import pytest
from services import i18n_service as i18n


# --------------------------------------------------------------------------- #
# normalize_locale
# --------------------------------------------------------------------------- #
class TestNormalizeLocale:
    def test_supported_lowercased(self):
        assert i18n.normalize_locale("EN") == "en"
        assert i18n.normalize_locale("ru") == "ru"

    def test_unsupported_falls_back_to_ru(self):
        assert i18n.normalize_locale("de") == "ru"
        assert i18n.normalize_locale("") == "ru"
        assert i18n.normalize_locale(None) == "ru"


# --------------------------------------------------------------------------- #
# resolve_locale — по SKU
# --------------------------------------------------------------------------- #
class TestResolveLocale:
    def test_none_user_is_ru(self):
        assert i18n.resolve_locale(None) == "ru"

    def test_train_global_is_en(self):
        with patch("services.capability_service._resolve_sku", return_value="TRAIN_GLOBAL"):
            assert i18n.resolve_locale(object()) == "en"

    @pytest.mark.parametrize("sku", ["TRAIN_RU", "FULL", "FREE", "UNKNOWN"])
    def test_non_global_sku_is_ru(self, sku):
        with patch("services.capability_service._resolve_sku", return_value=sku):
            assert i18n.resolve_locale(object()) == "ru"

    def test_exception_in_sku_resolution_is_ru(self):
        # fail-safe: любой сбой не должен выдавать неожиданный язык RU-юзерам
        with patch("services.capability_service._resolve_sku", side_effect=RuntimeError):
            assert i18n.resolve_locale(object()) == "ru"


# --------------------------------------------------------------------------- #
# translate
# --------------------------------------------------------------------------- #
class TestTranslate:
    def test_ru_is_identity(self):
        # RU — исходный язык: возвращаем строку как есть, без обращения к каталогу
        assert i18n.translate("Главная", "ru") == "Главная"
        assert i18n.translate("Любая строка", "ru") == "Любая строка"

    def test_en_uses_catalog(self):
        assert i18n.translate("Главная", "en") == "Home"
        assert i18n.translate("Настройки", "en") == "Settings"

    def test_en_missing_key_falls_back_to_source(self):
        assert i18n.translate("Нет в каталоге", "en") == "Нет в каталоге"

    def test_empty_message(self):
        assert i18n.translate("", "en") == ""


# --------------------------------------------------------------------------- #
# localize_date
# --------------------------------------------------------------------------- #
class TestLocalizeDate:
    def test_ru_format(self):
        assert i18n.localize_date(date(2026, 6, 17), "ru") == "17 июня 2026"

    def test_en_format(self):
        assert i18n.localize_date(date(2026, 6, 17), "en") == "June 17, 2026"

    def test_without_year(self):
        assert i18n.localize_date(date(2026, 1, 5), "ru", with_year=False) == "5 января"
        assert i18n.localize_date(date(2026, 1, 5), "en", with_year=False) == "January 5"

    def test_datetime_input(self):
        assert i18n.localize_date(datetime(2026, 12, 31, 23, 59), "en") == "December 31, 2026"

    def test_none_is_empty(self):
        assert i18n.localize_date(None, "ru") == ""


# --------------------------------------------------------------------------- #
# Jinja-интеграция
# --------------------------------------------------------------------------- #
class TestJinjaTranslator:
    def _env(self):
        from jinja2 import Environment

        env = Environment()
        env.globals["_"] = i18n.make_jinja_translator()
        env.filters["localdate"] = i18n.make_jinja_dateformat()
        return env

    def test_translator_uses_user_locale_en(self):
        env = self._env()
        tmpl = env.from_string("{{ _('Главная') }}")
        with patch("services.capability_service._resolve_sku", return_value="TRAIN_GLOBAL"):
            assert tmpl.render(user=object()) == "Home"

    def test_translator_uses_user_locale_ru(self):
        env = self._env()
        tmpl = env.from_string("{{ _('Главная') }}")
        with patch("services.capability_service._resolve_sku", return_value="TRAIN_RU"):
            assert tmpl.render(user=object()) == "Главная"

    def test_explicit_locale_in_context_wins(self):
        env = self._env()
        tmpl = env.from_string("{{ _('Главная') }}")
        # locale в контексте имеет приоритет над user
        assert tmpl.render(locale="en") == "Home"

    def test_no_user_defaults_ru(self):
        env = self._env()
        tmpl = env.from_string("{{ _('Главная') }}")
        assert tmpl.render() == "Главная"

    def test_localdate_filter(self):
        env = self._env()
        tmpl = env.from_string("{{ d | localdate }}")
        assert tmpl.render(locale="en", d=date(2026, 6, 17)) == "June 17, 2026"
