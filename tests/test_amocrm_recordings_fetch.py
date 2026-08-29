"""
Выборка записей amoCRM: справочники, прогресс и отсутствие ветки /calls.

На проде выборка 227 записей заняла 5 минут 15 секунд, а синхронизация
сущностей с теми же справочниками — 7 секунд. Разница в том, что записи
запрашивали /users/{id} и /leads/{id} на КАЖДОЕ примечание.
"""

# Порядок импортов значим: переменные окружения выставляются до импорта
# app-модулей (database.py читает DATABASE_URL на импорте).
# isort: skip_file

import os

import pytest
from cryptography.fernet import Fernet

os.environ["DATABASE_URL"] = "postgresql://unused:unused@localhost/unused"
os.environ.setdefault("CRM_ENCRYPTION_KEY", Fernet.generate_key().decode())

from services.crm_service import AmoCRMService


class _StubIntegration:
    crm_type = "amocrm"
    crm_domain = "example"
    access_token = None
    refresh_token = None


def _note(note_id, entity_id, user_id=555, duration=60):
    return {
        "id": note_id,
        "entity_id": entity_id,
        "note_type": "call_in",
        "created_at": 1700000000,
        "responsible_user_id": user_id,
        "params": {"link": f"https://example/rec{note_id}.mp3", "duration": duration},
    }


class _CountingService(AmoCRMService):
    """Считает обращения по эндпоинтам, ответы задаются наперёд."""

    def __init__(self, notes_by_type):
        super().__init__(_StubIntegration())
        self.access_token = "token"
        self.calls = []
        self._notes_by_type = notes_by_type

    async def _make_api_request(self, endpoint, method="GET", **kwargs):
        self.calls.append(endpoint)
        if endpoint == "/users":
            return {"_embedded": {"users": [{"id": 555, "name": "Пётр Иванов"}]}}
        for entity_type, notes in self._notes_by_type.items():
            if endpoint == f"/{entity_type}/notes":
                page = (kwargs.get("params") or {})
                # params здесь — список кортежей; страница всегда первая
                return {"_embedded": {"notes": notes}}
        if endpoint.startswith("/leads/"):
            return {"id": int(endpoint.rsplit("/", 1)[1]), "name": "Сделка"}
        if endpoint.startswith("/contacts/"):
            return {"id": int(endpoint.rsplit("/", 1)[1]), "name": "Контакт"}
        return None


# ── Ветка /calls удалена ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_calls_endpoint_is_never_requested():
    """
    GET /api/v4/calls отвечает 405 Method Not Allowed — это эндпоинт для
    добавления звонков. Ветка удалена, запроса быть не должно.
    """
    service = _CountingService({"leads": [], "contacts": []})
    await service.get_recordings(db=None, initial_sync_completed=True)

    assert "/calls" not in service.calls


# ── Справочник пользователей — один запрос на прогон ─────────────────────

@pytest.mark.asyncio
async def test_user_directory_is_fetched_once_not_per_note():
    """Раньше здесь был /users/{id} на каждое примечание, по 2–7 секунд."""
    notes = [_note(i, entity_id=900 + i) for i in range(1, 11)]
    service = _CountingService({"leads": notes, "contacts": []})

    result = await service.get_recordings(db=None, initial_sync_completed=True)

    assert len(result) == 10
    assert service.calls.count("/users") == 1
    assert not [c for c in service.calls if c.startswith("/users/")]
    assert all(r["manager_name"] == "Пётр Иванов" for r in result)


@pytest.mark.asyncio
async def test_lead_name_is_requested_once_per_deal_not_per_call():
    """
    У одной сделки обычно несколько звонков. Название запрашивается на
    сделку, а не на запись.
    """
    notes = [_note(1, 900), _note(2, 900), _note(3, 900), _note(4, 901)]
    service = _CountingService({"leads": notes, "contacts": []})

    await service.get_recordings(db=None, initial_sync_completed=True)

    lead_lookups = [c for c in service.calls
                    if c.startswith("/leads/") and c != "/leads/notes"]
    assert sorted(lead_lookups) == ["/leads/900", "/leads/901"]


# ── Прогресс по ходу выборки ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_progress_is_reported_during_fetch():
    """
    Записи сохраняются одной пачкой в конце, поэтому без колбэка
    пользователь несколько минут смотрит на неподвижное окно.
    """
    service = _CountingService({"leads": [_note(1, 900), _note(2, 901)],
                                "contacts": [_note(3, 800)]})
    seen = []

    await service.get_recordings(db=None, initial_sync_completed=True,
                                 on_progress=lambda pages, collected: seen.append((pages, collected)))

    assert seen, "прогресс должен приходить по ходу выборки"
    pages = [p for p, _ in seen]
    collected = [c for _, c in seen]
    assert pages == sorted(pages), "счётчик страниц не должен убывать"
    assert collected == sorted(collected), "счётчик записей не должен убывать"
    assert collected[-1] == 3, "итог прогресса совпадает с числом записей"


@pytest.mark.asyncio
async def test_broken_progress_callback_does_not_break_sync():
    """Прогресс вспомогательный: его падение не должно ронять выборку."""
    service = _CountingService({"leads": [_note(1, 900)], "contacts": []})

    def _boom(pages, collected):
        raise RuntimeError("подписчик упал")

    result = await service.get_recordings(db=None, initial_sync_completed=True,
                                          on_progress=_boom)
    assert len(result) == 1


@pytest.mark.asyncio
async def test_works_without_progress_callback():
    service = _CountingService({"leads": [_note(1, 900)], "contacts": []})
    result = await service.get_recordings(db=None, initial_sync_completed=True)
    assert len(result) == 1
