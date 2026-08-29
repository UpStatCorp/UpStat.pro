"""
Подключение CRM: нормализация домена и проверка OAuth-кредов.

Оба блокера из CARRYOVER §9. Нормализация Bitrix24 знала одну зону из
двенадцати и молча уводила пользователя на чужой портал; у amoCRM
нормализации не было вообще. Пустые креды не проверялись нигде — в БД
появлялась мёртвая интеграция и ссылка без client_id.
"""

# Порядок импортов значим: переменные окружения выставляются до импорта
# app-модулей (database.py читает DATABASE_URL на импорте).
# isort: skip_file

import os

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

# Присваиваем, а не setdefault: CI задаёт DATABASE_URL=sqlite:///, который
# app/database.py отвергает на импорте. Подключения по этому URL не будет —
# create_engine ленивый, а тесты работают на своём SQLite-движке.
os.environ["DATABASE_URL"] = "postgresql://unused:unused@localhost/unused"
os.environ.setdefault("CRM_ENCRYPTION_KEY", Fernet.generate_key().decode())

from routers.crm_integration import (
    _normalize_amocrm_domain, _normalize_bitrix24_domain, _require_oauth_credentials,
)

# Зоны из allowlist вебхук-подключения — тот же список должен работать в OAuth.
BITRIX24_ZONES = [
    "ru", "com", "eu", "de", "fr", "es", "pl", "in", "ua", "by", "kz", "ltd", "site",
]


# ── Bitrix24: домен сохраняется как есть ─────────────────────────────────

@pytest.mark.parametrize("zone", BITRIX24_ZONES)
def test_bitrix24_zone_is_preserved(zone):
    """
    Раньше .replace() оставлял зону внутри имени: acme.bitrix24.kz
    превращался в acme.bitrix24.kz.bitrix24.ru.
    """
    assert _normalize_bitrix24_domain(f"acme.bitrix24.{zone}") == f"acme.bitrix24.{zone}"


def test_bitrix24_com_is_not_silently_turned_into_ru():
    """
    Самый опасный прежний случай: acme.bitrix24.com молча становился
    acme.bitrix24.ru — это ЧУЖОЙ портал, а не опечатка.
    """
    assert _normalize_bitrix24_domain("acme.bitrix24.com") == "acme.bitrix24.com"


def test_bitrix24_bare_name_gets_default_zone():
    """Ввод одного имени портала — самый частый случай, достраивается до .ru."""
    assert _normalize_bitrix24_domain("acme") == "acme.bitrix24.ru"


@pytest.mark.parametrize("value", [
    "https://acme.bitrix24.ru/crm/deal/",
    "http://acme.bitrix24.ru",
    "  ACME.bitrix24.RU  ",
    "acme.bitrix24.ru/",
])
def test_bitrix24_accepts_url_and_untidy_input(value):
    assert _normalize_bitrix24_domain(value) == "acme.bitrix24.ru"


@pytest.mark.parametrize("value", [
    "",
    "   ",
    "acme.bitrix24.com.br",   # раньше становился acme.br.bitrix24.ru
    "acme.example.com",
    "evil.com/acme.bitrix24.ru",
])
def test_bitrix24_rejects_bad_input(value):
    with pytest.raises(HTTPException) as exc:
        _normalize_bitrix24_domain(value)
    assert exc.value.status_code == 400


# ── amoCRM: в crm_domain должен лежать только поддомен ───────────────────

@pytest.mark.parametrize("value", [
    "acme",
    "acme.amocrm.ru",
    "https://acme.amocrm.ru/leads",
    "  ACME.amocrm.RU ",
])
def test_amocrm_returns_bare_subdomain(value):
    """
    base_url собирается как https://{crm_domain}.amocrm.ru, поэтому полный
    домен во вводе давал acme.amocrm.ru.amocrm.ru.
    """
    assert _normalize_amocrm_domain(value) == "acme"


def test_amocrm_com_is_rejected_not_coerced():
    """
    Зона .com не приводится к .ru молча: базовый URL жёстко .amocrm.ru,
    и приведение отправило бы запросы на чужой аккаунт.
    """
    with pytest.raises(HTTPException) as exc:
        _normalize_amocrm_domain("acme.amocrm.com")
    assert exc.value.status_code == 400


@pytest.mark.parametrize("value", ["", "   ", "acme.example.com"])
def test_amocrm_rejects_bad_input(value):
    with pytest.raises(HTTPException):
        _normalize_amocrm_domain(value)


# ── Пустые OAuth-креды ───────────────────────────────────────────────────

@pytest.mark.parametrize("client_id,client_secret", [
    ("", ""),
    ("", "secret"),
    ("id", ""),
    ("   ", "secret"),
])
def test_missing_credentials_are_rejected(client_id, client_secret):
    """На проде BITRIX24_CLIENT_ID/SECRET пустые — это должно быть видно сразу."""
    with pytest.raises(HTTPException) as exc:
        _require_oauth_credentials("Bitrix24", client_id, client_secret)
    assert exc.value.status_code == 503
    assert "поддержку" in exc.value.detail


def test_present_credentials_pass():
    assert _require_oauth_credentials("AmoCRM", "id", "secret") is None
