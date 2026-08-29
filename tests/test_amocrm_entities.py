"""
Синхронизация сущностей amoCRM: правило закрытия сделки, маппинг полей,
справочники и привязка записей.

Приоритет проверок — шесть полей, которые читает CRM-аналитика
(analytics.py): closed, is_won, close_date, opportunity, created_at,
assigned_by_name. Остальные поля заполняются попутно.
"""

# Порядок импортов значим: переменные окружения выставляются до импорта
# app-модулей (database.py читает DATABASE_URL на импорте).
# isort: skip_file

import os
import tempfile
from datetime import datetime

import pytest
from cryptography.fernet import Fernet

# Присваиваем, а не setdefault: CI задаёт DATABASE_URL=sqlite:///, который
# app/database.py отвергает на импорте. Подключения по этому URL не будет —
# create_engine ленивый, а тесты работают на своём SQLite-движке.
os.environ["DATABASE_URL"] = "postgresql://unused:unused@localhost/unused"
os.environ.setdefault("CRM_ENCRYPTION_KEY", Fernet.generate_key().decode())

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from models import CRMContact, CRMDeal, CRMRecording
import services.crm_service as crm_service
from services.crm_service import (
    AMOCRM_STATUS_LOST, AMOCRM_STATUS_WON, _amocrm_outcome,
    link_amocrm_recordings_to_entities, sync_amocrm_deals,
)


# ── Правило закрытия сделки ──────────────────────────────────────────────

def test_won_status_closes_deal_as_won():
    assert _amocrm_outcome(AMOCRM_STATUS_WON) == (True, True)


def test_lost_status_closes_deal_as_lost():
    assert _amocrm_outcome(AMOCRM_STATUS_LOST) == (True, False)


@pytest.mark.parametrize("status_id", [1, 42, 1234567, "89"])
def test_any_other_status_means_deal_in_progress(status_id):
    """
    Открытая сделка: is_won именно None, а не False. Аналитика считает
    выигрыши по is_won == True, и «ещё не выиграна» не должна попадать
    в проигрыши.
    """
    assert _amocrm_outcome(status_id) == (False, None)


def test_missing_status_does_not_close_deal():
    assert _amocrm_outcome(None) == (False, None)


# ── Инфраструктура для проверки маппинга ─────────────────────────────────

@pytest.fixture
def sessions():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    for model in (CRMDeal, CRMContact, CRMRecording):
        model.__table__.create(engine)
    try:
        yield sessionmaker(bind=engine)
    finally:
        engine.dispose()
        os.unlink(path)


class _StubIntegration:
    crm_type = "amocrm"
    crm_domain = "example"
    access_token = None
    refresh_token = None


class _FakeAmoService:
    """
    Отдаёт заранее заданные ответы по эндпоинтам, считая обращения.

    Счётчик нужен, чтобы проверить главное свойство справочников: один
    запрос на прогон, а не запрос на каждую сделку.
    """

    def __init__(self, responses):
        self.integration = _StubIntegration()
        self.responses = responses
        self.calls = []

    async def _make_api_request(self, endpoint, params=None, **kwargs):
        self.calls.append(endpoint)
        value = self.responses.get(endpoint)
        if callable(value):
            return value(params or {})
        return value


def _lead(**over):
    item = {
        "id": 100,
        "name": "Поставка станков",
        "price": 250000,
        "status_id": AMOCRM_STATUS_WON,
        "pipeline_id": 7,
        "responsible_user_id": 55,
        "created_at": 1700000000,
        "updated_at": 1700003600,
        "closed_at": 1700007200,
        "_embedded": {"contacts": [{"id": 900, "is_main": True}],
                      "companies": [{"id": 300}]},
    }
    item.update(over)
    return item


def _service_with_leads(leads):
    return _FakeAmoService({
        "/users": {"_embedded": {"users": [{"id": 55, "name": "Пётр Иванов"}]}},
        "/leads/pipelines": {"_embedded": {"pipelines": [{
            "id": 7, "name": "Продажи",
            "_embedded": {"statuses": [
                {"id": AMOCRM_STATUS_WON, "name": "Успешно реализовано"},
                {"id": 1, "name": "Первичный контакт"},
            ]},
        }]}},
        "/leads": {"_embedded": {"leads": leads}},
    })


# ── Шесть полей, на которых держится аналитика ───────────────────────────

@pytest.mark.asyncio
async def test_deal_maps_fields_used_by_analytics(sessions):
    service = _service_with_leads([_lead()])
    db = sessions()
    try:
        assert await sync_amocrm_deals(service, db, integration_id=1) == 1
        deal = db.query(CRMDeal).one()

        assert deal.closed is True
        assert deal.is_won is True
        assert deal.close_date == datetime.fromtimestamp(1700007200)
        assert deal.opportunity == 250000
        assert deal.created_at == datetime.fromtimestamp(1700000000)
        assert deal.assigned_by_name == "Пётр Иванов"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_deal_maps_names_and_links(sessions):
    """Попутные поля: воронка, статус и связанные сущности."""
    service = _service_with_leads([_lead()])
    db = sessions()
    try:
        await sync_amocrm_deals(service, db, integration_id=1)
        deal = db.query(CRMDeal).one()

        assert deal.bitrix_id == 100
        assert deal.title == "Поставка станков"
        assert deal.stage_name == "Успешно реализовано"
        assert deal.category_name == "Продажи"
        assert deal.contact_id == 900   # is_main
        assert deal.company_id == 300
    finally:
        db.close()


@pytest.mark.asyncio
async def test_open_deal_is_not_counted_as_lost(sessions):
    service = _service_with_leads([_lead(status_id=1, closed_at=0)])
    db = sessions()
    try:
        await sync_amocrm_deals(service, db, integration_id=1)
        deal = db.query(CRMDeal).one()
        assert deal.closed is False
        assert deal.is_won is None
        assert deal.close_date is None
    finally:
        db.close()


@pytest.mark.asyncio
async def test_repeated_sync_updates_instead_of_duplicating(sessions):
    db = sessions()
    try:
        await sync_amocrm_deals(_service_with_leads([_lead()]), db, integration_id=1)
        await sync_amocrm_deals(
            _service_with_leads([_lead(name="Переименована", status_id=1)]),
            db, integration_id=1,
        )
        deal = db.query(CRMDeal).one()
        assert deal.title == "Переименована"
        assert deal.closed is False
    finally:
        db.close()


# ── Справочники: один запрос на прогон ───────────────────────────────────

@pytest.mark.asyncio
async def test_reference_data_fetched_once_per_run(sessions):
    """
    Справочники пользователей и воронок читаются один раз, а не на каждую
    сделку: запрос на сущность — это те самые 3.5 минуты на 227 записей
    в выборке звонков.
    """
    leads = [_lead(id=i) for i in range(1, 51)]
    service = _service_with_leads(leads)
    db = sessions()
    try:
        await sync_amocrm_deals(service, db, integration_id=1)
    finally:
        db.close()

    assert service.calls.count("/users") == 1
    assert service.calls.count("/leads/pipelines") == 1


# ── Привязка записей ─────────────────────────────────────────────────────

def _recording(db, meta_json, **over):
    fields = dict(
        integration_id=1, user_id=1, crm_record_id="leads_note_100_5",
        call_date=datetime(2026, 1, 1), crm_metadata_json=meta_json,
    )
    fields.update(over)
    rec = CRMRecording(**fields)
    db.add(rec)
    db.commit()
    return rec


@pytest.mark.asyncio
async def test_linking_uses_metadata_without_api_calls(sessions):
    """
    Привязка идёт по entity_type/entity_id из метаданных примечания.
    Обращений к API быть не должно ни одного: у amoCRM нет метода, который
    здесь запрашивала bitrix-ветка.
    """
    service = _FakeAmoService({})
    db = sessions()
    try:
        db.add(CRMDeal(bitrix_id=100, integration_id=1, contact_id=900))
        db.add(CRMContact(bitrix_id=900, integration_id=1, name="Клиент"))
        db.commit()
        rec = _recording(db, '{"entity_type": "leads", "entity_id": 100}')

        assert await link_amocrm_recordings_to_entities(service, db, 1) == 1

        db.refresh(rec)
        assert rec.deal_id == db.query(CRMDeal).one().id
        # Контакт сделки подтягивается заодно, как в bitrix-ветке.
        assert rec.contact_crm_id == db.query(CRMContact).one().id
        assert service.calls == []
    finally:
        db.close()


@pytest.mark.asyncio
async def test_linking_contact_note(sessions):
    service = _FakeAmoService({})
    db = sessions()
    try:
        db.add(CRMContact(bitrix_id=900, integration_id=1, name="Клиент"))
        db.commit()
        rec = _recording(db, '{"entity_type": "contacts", "entity_id": 900}',
                         crm_record_id="contacts_note_900_5")

        assert await link_amocrm_recordings_to_entities(service, db, 1) == 1
        db.refresh(rec)
        assert rec.contact_crm_id == db.query(CRMContact).one().id
        assert rec.deal_id is None
    finally:
        db.close()


@pytest.mark.asyncio
async def test_linking_skips_records_without_usable_metadata(sessions):
    """
    Записи из /calls метаданных о сущности не несут — их привязка отдельная
    задача. Здесь важно, что они не ломают прогон и не считаются связанными.
    """
    service = _FakeAmoService({})
    db = sessions()
    try:
        _recording(db, '{"source": "widget", "call_result": ""}', crm_record_id="9001")
        _recording(db, None, crm_record_id="9002")
        _recording(db, "не json", crm_record_id="9003")

        assert await link_amocrm_recordings_to_entities(service, db, 1) == 0
    finally:
        db.close()
