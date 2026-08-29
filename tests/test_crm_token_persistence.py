"""
Сохранение CRM-токенов при обновлении по 401.

Проверяем то, что раньше молча не работало: refresh_access_token коммитил
в НОВУЮ сессию SessionLocal(), а self.integration принадлежал другой —
commit() для него no-op, новый токен в БД не попадал. У amoCRM refresh
одноразовый, поэтому потерянный токен означает мёртвую интеграцию.

Каждая проверка читает значение ИЗ БД отдельной сессией после закрытия
рабочей: только так видно, что запись действительно произошла, а не
осталась в identity map.

Как и tests/test_run_db_concurrency.py, используем файловый SQLite со своим
engine — проверяется механика сессий SQLAlchemy, а не драйвер PostgreSQL.
"""

# Порядок импортов значим: переменные окружения выставляются до импорта
# app-модулей (database.py читает DATABASE_URL на импорте).
# isort: skip_file

import os
import tempfile

import pytest
from cryptography.fernet import Fernet

# Должны быть выставлены ДО импорта app-модулей: database.py читает
# DATABASE_URL на импорте, crm_service — CRM_ENCRYPTION_KEY при шифровании.
os.environ.setdefault("DATABASE_URL", "postgresql://unused:unused@localhost/unused")
os.environ["CRM_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from models import CRMIntegration
from services.crm_service import AmoCRMService


@pytest.fixture
def sessions():
    """Своя БД с одной таблицей crm_integrations и фабрика сессий к ней."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    # Только нужная таблица: FK на users в SQLite не проверяется.
    CRMIntegration.__table__.create(engine)
    try:
        yield sessionmaker(bind=engine)
    finally:
        engine.dispose()
        os.unlink(path)


def _make_integration(SessionMaker, *, access="old_access", refresh="old_refresh"):
    """Заводит интеграцию с зашифрованными токенами, возвращает её id."""
    cipher = Fernet(os.environ["CRM_ENCRYPTION_KEY"].encode())
    db = SessionMaker()
    try:
        integration = CRMIntegration(
            user_id=1,
            crm_type="amocrm",
            crm_name="test",
            crm_domain="example",
            access_token=cipher.encrypt(access.encode()).decode(),
            refresh_token=cipher.encrypt(refresh.encode()).decode(),
            is_active=True,
        )
        db.add(integration)
        db.commit()
        return integration.id
    finally:
        db.close()


def _tokens_in_db(SessionMaker, integration_id):
    """Читает токены из БД НОВОЙ сессией и расшифровывает."""
    cipher = Fernet(os.environ["CRM_ENCRYPTION_KEY"].encode())
    db = SessionMaker()
    try:
        row = db.query(CRMIntegration).get(integration_id)
        return (
            cipher.decrypt(row.access_token.encode()).decode(),
            cipher.decrypt(row.refresh_token.encode()).decode(),
        )
    finally:
        db.close()


def test_tokens_reach_db_when_owner_session_passed(sessions):
    """Основной случай: сервис создан с сессией-владельцем → токены в БД."""
    integration_id = _make_integration(sessions)

    owner = sessions()
    try:
        integration = owner.query(CRMIntegration).get(integration_id)
        service = AmoCRMService(integration, owner)
        service.save_tokens(owner, access_token="new_access",
                            refresh_token="new_refresh", expires_in=3600)
    finally:
        owner.close()

    assert _tokens_in_db(sessions, integration_id) == ("new_access", "new_refresh")


def test_tokens_reach_db_when_owner_session_never_commits(sessions):
    """
    Негативный случай — тот самый, на котором ломалось.

    Сессия-владелец сама не коммитится и закрывается: если сохранение ушло
    в чужую сессию, значение в БД останется прежним.
    """
    integration_id = _make_integration(sessions)

    owner = sessions()
    try:
        integration = owner.query(CRMIntegration).get(integration_id)
        service = AmoCRMService(integration, owner)
        service.save_tokens(owner, access_token="new_access",
                            refresh_token="new_refresh")
        # Никакого owner.commit() — сохранение обязано было произойти внутри.
    finally:
        owner.close()

    assert _tokens_in_db(sessions, integration_id) == ("new_access", "new_refresh")


def test_tokens_reach_db_via_merge_when_session_is_foreign(sessions):
    """
    Сервис создан без сессии-владельца (self.db is None), сохранение идёт
    в постороннюю сессию: merge должен привести объект в неё, иначе commit
    снова окажется no-op.
    """
    integration_id = _make_integration(sessions)

    owner = sessions()
    foreign = sessions()
    try:
        integration = owner.query(CRMIntegration).get(integration_id)
        service = AmoCRMService(integration)  # владелец не передан
        service.save_tokens(foreign, access_token="new_access",
                            refresh_token="new_refresh")
    finally:
        foreign.close()
        owner.close()

    assert _tokens_in_db(sessions, integration_id) == ("new_access", "new_refresh")


def test_refresh_token_preserved_when_crm_returns_none(sessions):
    """
    CRM не вернула новый refresh_token → прежний обязан уцелеть и в БД,
    и в памяти сервиса. Затирание на None убивало интеграцию amoCRM:
    прежний refresh уже погашен на стороне CRM, нового нет.
    """
    integration_id = _make_integration(sessions, refresh="one_time_refresh")

    owner = sessions()
    try:
        integration = owner.query(CRMIntegration).get(integration_id)
        service = AmoCRMService(integration, owner)
        service.save_tokens(owner, access_token="new_access", refresh_token=None)
        assert service.refresh_token == "one_time_refresh"
    finally:
        owner.close()

    access, refresh = _tokens_in_db(sessions, integration_id)
    assert access == "new_access"
    assert refresh == "one_time_refresh"


# ── Путь обновления по 401: сессия берётся из _token_refresh_session ──────
#
# Выше проверено само сохранение. Здесь — то место, где оно ломалось:
# ветка 401 в _make_api_request открывала свою SessionLocal() вместо сессии,
# которой принадлежит integration.

class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Подменяет httpx.AsyncClient: любой POST отдаёт заданный ответ токен-эндпоинта."""

    payload: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        return _FakeResponse(self.payload)


@pytest.fixture
def fake_http(monkeypatch):
    import services.crm_service as crm_service

    def _install(payload):
        _FakeAsyncClient.payload = payload
        monkeypatch.setattr(crm_service.httpx, "AsyncClient", _FakeAsyncClient)

    return _install


def _make_integration_with_creds(SessionMaker):
    """Интеграция с зашифрованными client_id/secret — их читает refresh_access_token."""
    cipher = Fernet(os.environ["CRM_ENCRYPTION_KEY"].encode())
    enc = lambda v: cipher.encrypt(v.encode()).decode()  # noqa: E731
    db = SessionMaker()
    try:
        integration = CRMIntegration(
            user_id=1,
            crm_type="amocrm",
            crm_name="test",
            crm_domain="example",
            client_id=enc("cid"),
            client_secret=enc("csecret"),
            access_token=enc("old_access"),
            refresh_token=enc("one_time_refresh"),
            is_active=True,
        )
        db.add(integration)
        db.commit()
        return integration.id
    finally:
        db.close()


@pytest.mark.asyncio
async def test_refresh_via_token_session_writes_to_db(sessions, fake_http):
    """
    Сервис создан с сессией-владельцем; обновление идёт так же, как в ветке
    401 у _make_api_request. Владелец сам не коммитится. Новый токен обязан
    оказаться в БД — прежде он терялся в отдельной SessionLocal().
    """
    fake_http({"access_token": "refreshed_access",
               "refresh_token": "refreshed_refresh",
               "expires_in": 86400})
    integration_id = _make_integration_with_creds(sessions)

    owner = sessions()
    try:
        integration = owner.query(CRMIntegration).get(integration_id)
        service = AmoCRMService(integration, owner)

        with service._token_refresh_session() as db:
            assert db is owner, "должна использоваться сессия-владелец"
            assert await service.refresh_access_token(db) is True
    finally:
        owner.close()

    assert _tokens_in_db(sessions, integration_id) == ("refreshed_access", "refreshed_refresh")


@pytest.mark.asyncio
async def test_refresh_without_new_refresh_token_keeps_old(sessions, fake_http):
    """amoCRM не прислала новый refresh — прежний остаётся в БД."""
    fake_http({"access_token": "refreshed_access", "expires_in": 86400})
    integration_id = _make_integration_with_creds(sessions)

    owner = sessions()
    try:
        integration = owner.query(CRMIntegration).get(integration_id)
        service = AmoCRMService(integration, owner)
        with service._token_refresh_session() as db:
            assert await service.refresh_access_token(db) is True
    finally:
        owner.close()

    assert _tokens_in_db(sessions, integration_id) == ("refreshed_access", "one_time_refresh")
