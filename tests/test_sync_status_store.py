"""
Хранилище статуса синхронизации: Redis вместо памяти процесса + блокировка.

Проблема подтверждена на проде дважды: индикатор показывал done при
работающей задаче и running при завершённой, потому что POST и GET
попадали в разные uvicorn-воркеры. Пользователь нажал «синхронизировать»
повторно, и два прогона пошли параллельно.

Redis подменяем заглушкой: проверяем поведение хранилища, а не драйвер.
"""

import json

import pytest
from services.sync_status import SyncStatusStore


class _FakeRedis:
    """Минимальный Redis: get/setex/set(nx,ex)/delete/exists."""

    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def setex(self, key, ttl, value):
        self.data[key] = value
        return True

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.data:
            return None          # реальный Redis возвращает None, когда NX не сработал
        self.data[key] = value
        return True

    def delete(self, key):
        return bool(self.data.pop(key, None))

    def exists(self, key):
        return 1 if key in self.data else 0


@pytest.fixture
def shared_redis():
    return _FakeRedis()


def _store(redis):
    return SyncStatusStore(client=redis)


# ── Статус виден всем процессам ──────────────────────────────────────────

def test_status_written_by_one_process_is_visible_to_another(shared_redis):
    """
    Суть починки: два разных объекта хранилища — как два uvicorn-воркера.
    Раньше словарь жил в памяти процесса, и второй воркер статуса не видел.
    """
    writer = _store(shared_redis)
    reader = _store(shared_redis)

    writer.set(1, {"phase": "fetching", "done": False})

    assert reader.get(1) == {"phase": "fetching", "done": False}


def test_update_merges_fields_across_processes(shared_redis):
    writer = _store(shared_redis)
    other = _store(shared_redis)

    writer.set(1, {"phase": "fetching", "found": 0, "done": False})
    other.update(1, found=124, pages=2)

    status = writer.get(1)
    assert status["phase"] == "fetching"
    assert status["found"] == 124
    assert status["pages"] == 2
    assert status["done"] is False


def test_unknown_integration_returns_none(shared_redis):
    assert _store(shared_redis).get(999) is None


def test_status_is_stored_as_json(shared_redis):
    """Данные должны читаться любым процессом, а не только этим."""
    _store(shared_redis).set(7, {"phase": "done"})
    raw = shared_redis.data["crm:sync:status:7"]
    assert json.loads(raw) == {"phase": "done"}


# ── Блокировка повторного запуска ────────────────────────────────────────

def test_second_acquire_is_refused(shared_redis):
    """Ровно тот сценарий с прода: второй запуск не должен стартовать."""
    first = _store(shared_redis)
    second = _store(shared_redis)

    assert first.acquire(1) is True
    assert second.acquire(1) is False


def test_lock_is_released_and_can_be_taken_again(shared_redis):
    first = _store(shared_redis)
    second = _store(shared_redis)

    assert first.acquire(1) is True
    first.release(1)
    assert second.acquire(1) is True


def test_locks_are_per_integration(shared_redis):
    store = _store(shared_redis)
    assert store.acquire(1) is True
    assert store.acquire(2) is True


def test_is_locked_reflects_state(shared_redis):
    store = _store(shared_redis)
    assert store.is_locked(1) is False
    store.acquire(1)
    assert store.is_locked(1) is True
    store.release(1)
    assert store.is_locked(1) is False


# ── Поведение при недоступном Redis ──────────────────────────────────────

def test_falls_back_to_memory_without_redis():
    """
    Фолбэк не решает исходную задачу при нескольких воркерах, но не должен
    ронять приложение: в разработке Redis может быть не поднят.
    """
    store = SyncStatusStore(redis_url=None)
    store.set(1, {"phase": "fetching"})
    assert store.get(1) == {"phase": "fetching"}
    assert store.acquire(1) is True
    assert store.acquire(1) is False


def test_broken_redis_does_not_block_sync():
    """
    Если Redis отвалился, отказать в синхронизации хуже, чем допустить
    второй прогон: данные от параллельного прогона не портятся.
    """
    class _Broken(_FakeRedis):
        def set(self, *a, **kw):
            raise RuntimeError("redis is down")

    assert _store(_Broken()).acquire(1) is True
