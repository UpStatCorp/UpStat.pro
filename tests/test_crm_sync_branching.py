"""
Ветвление синхронизации по типу CRM и склейка URL у amoCRM.

Прежнее поведение: full_crm_sync гнал для любой CRM набор стадий, написанных
под Bitrix24. Для amoCRM все они падали, except Exception писал 0, и результат
{'deals': 0, ...} показывался пользователю как успешная синхронизация.
"""

# Порядок импортов значим: переменные окружения выставляются до импорта
# app-модулей (database.py читает DATABASE_URL на импорте).
# isort: skip_file

import os

import pytest
from cryptography.fernet import Fernet

os.environ.setdefault("DATABASE_URL", "postgresql://unused:unused@localhost/unused")
os.environ.setdefault("CRM_ENCRYPTION_KEY", Fernet.generate_key().decode())

import services.crm_service as crm_service
from services.crm_service import (
    STAGE_UNSUPPORTED, _ENTITY_STAGE_NAMES, _stages_for, full_crm_sync,
)


class _StubIntegration:
    def __init__(self, crm_type, crm_domain="example"):
        self.crm_type = crm_type
        self.crm_domain = crm_domain
        self.access_token = None
        self.refresh_token = None


class _StubService:
    def __init__(self, crm_type):
        self.integration = _StubIntegration(crm_type)


# ── B. Ветвление ─────────────────────────────────────────────────────────

def test_bitrix24_keeps_all_stages():
    assert [name for name, _ in _stages_for("bitrix24")] == list(_ENTITY_STAGE_NAMES)
    assert [name for name, _ in _stages_for("bitrix24_webhook")] == list(_ENTITY_STAGE_NAMES)


def test_amocrm_has_its_own_stages():
    """
    У amoCRM свой набор: сделки, контакты, компании и привязка. Лидов,
    товаров и активностей у неё нет — эти стадии не запускаются.
    """
    names = [name for name, _ in _stages_for("amocrm")]
    assert names == ["deals", "contacts", "companies", "linking"]
    assert "leads" not in names
    assert "activities" not in names
    assert "products" not in names


def test_unknown_crm_type_is_explicit_error():
    """Не молчаливый пропуск: неизвестный тип должен быть виден сразу."""
    with pytest.raises(ValueError):
        _stages_for("salesforce")


# ── C. Честный результат ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_amocrm_marks_missing_entities_unsupported(monkeypatch):
    """
    Выполненные стадии дают счётчик, а сущности, которых у amoCRM нет,
    помечены unsupported — не нулями, которые читаются как «данных нет».
    """
    async def _count(service, db, integration_id):
        return 3

    monkeypatch.setattr(crm_service, "_amocrm_stages",
                        lambda: [("deals", _count), ("contacts", _count),
                                 ("companies", _count), ("linking", _count)])

    results = await full_crm_sync(_StubService("amocrm"), db=None, integration_id=1)

    assert results["deals"] == 3
    assert results["stages"]["deals"] == "ok"
    assert results["stages"]["leads"] == STAGE_UNSUPPORTED
    assert results["stages"]["products"] == STAGE_UNSUPPORTED
    assert results["stages"]["activities"] == STAGE_UNSUPPORTED
    assert "leads" not in results, "у неподдерживаемой стадии счётчика быть не должно"


@pytest.mark.asyncio
async def test_failed_stage_has_status_but_no_count(monkeypatch):
    """Упавшая стадия помечается failed и не выдаёт себя за нулевой результат."""
    async def _ok(service, db, integration_id):
        return 7

    async def _boom(service, db, integration_id):
        raise RuntimeError("api down")

    monkeypatch.setattr(crm_service, "_bitrix24_stages",
                        lambda: [("deals", _ok), ("leads", _boom)])

    results = await full_crm_sync(_StubService("bitrix24"), db=None, integration_id=1)

    assert results["deals"] == 7
    assert results["stages"]["deals"] == "ok"
    assert results["stages"]["leads"] == "failed"
    assert "leads" not in results, "упавшая стадия не должна выдавать себя за ноль"


# ── D. Склейка base_url и endpoint ───────────────────────────────────────

class _CapturingClient:
    """Запоминает URL запроса; ответ не важен."""

    seen = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, url, **kwargs):
        _CapturingClient.seen.append(url)

        class _R:
            def raise_for_status(self_inner):
                return None

            def json(self_inner):
                return {}

        return _R()


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/calls", "calls"])
async def test_amocrm_url_has_exactly_one_slash(monkeypatch, endpoint):
    """
    Общий код синхронизации передаёт endpoint без слеша ("crm.activity.get"),
    внутренние вызовы — со слешем ("/calls"). Оба должны давать один слеш:
    склейка без разделителя давала ".../api/v4crm.activity.get" и 404.
    """
    monkeypatch.setattr(crm_service.httpx, "AsyncClient", _CapturingClient)
    _CapturingClient.seen = []

    service = crm_service.AmoCRMService(_StubIntegration("amocrm"))
    service.access_token = "token"

    await service._make_api_request(endpoint)

    assert _CapturingClient.seen == ["https://example.amocrm.ru/api/v4/calls"]


# ── Сводка для UI ────────────────────────────────────────────────────────

def test_entity_support_summary():
    """
    _entity_support переводит статусы стадий в то, что показывается
    пользователю. Пустой словарь и «всё unsupported» — это 'none':
    для amoCRM стадий нет вовсе, и это не успех.
    """
    from routers.crm_integration import _entity_support

    assert _entity_support({}) == "none"
    assert _entity_support({n: STAGE_UNSUPPORTED for n in _ENTITY_STAGE_NAMES}) == "none"
    assert _entity_support({"deals": "ok", "leads": "failed"}) == "partial"
    assert _entity_support({"deals": "ok", "leads": "ok"}) == "full"
    # Часть сущностей CRM не поддерживает: это не сбой, повтор не поможет.
    assert _entity_support({"deals": "ok", "leads": STAGE_UNSUPPORTED}) == "limited"
    # Сбой важнее ограничения: его показываем в первую очередь.
    assert _entity_support(
        {"deals": "failed", "leads": STAGE_UNSUPPORTED}
    ) == "partial"
