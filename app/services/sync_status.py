"""
Статус синхронизации CRM — общий для всех воркеров.

Раньше прогресс лежал в модульном словаре процесса (``_sync_status`` в
``routers/crm_integration.py``). Бэкенд запускается с ``WEB_CONCURRENCY=4``,
nginx работает без ``ip_hash``, поэтому POST на запуск синхронизации и GET
статуса попадали в РАЗНЫЕ процессы: индикатор показывал ``idle``/``done``
при работающей задаче и ``running`` при завершённой. На живой проверке
29.08.2026 это привело к тому, что пользователь запустил синхронизацию
дважды и два прогона шли параллельно.

Здесь статус хранится в Redis, который в проекте уже есть (очередь arq).
Плюс атомарная блокировка на время прогона: ``SET NX EX`` — единственный
способ не пустить второй запуск, когда процессов несколько.

Фолбэк в память оставлен намеренно, для разработки и тестов. Он НЕ решает
исходную задачу при нескольких воркерах — о чём и предупреждает в логе.
"""

import json
import logging
import os
import threading
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    import redis as _redis
except ImportError:  # pragma: no cover — redis есть в requirements
    _redis = None

# Ключи переживают перезапуск воркера, но не должны копиться вечно:
# словарь в памяти как раз никогда не чистился.
STATUS_TTL_SECONDS = 6 * 3600

# Блокировка живёт заметно дольше самой долгой наблюдавшейся синхронизации
# (5 минут на 227 записей), но не бесконечно: если процесс умрёт, не сняв
# её, интеграция не должна остаться заблокированной навсегда.
LOCK_TTL_SECONDS = 3600


class SyncStatusStore:
    """Статус и блокировка синхронизации, общие для всех процессов."""

    def __init__(self, redis_url: Optional[str] = None, client: Any = None):
        self._client = client
        self._memory: Dict[int, Dict[str, Any]] = {}
        self._memory_locks: Dict[int, bool] = {}
        self._memory_guard = threading.Lock()

        if self._client is None and _redis is not None and redis_url:
            try:
                self._client = _redis.from_url(redis_url, decode_responses=True)
                self._client.ping()
                logger.info("Статус синхронизации CRM хранится в Redis")
            except Exception as exc:
                logger.warning(
                    f"Redis недоступен ({exc}); статус синхронизации CRM хранится "
                    "в памяти процесса. При нескольких воркерах индикатор будет "
                    "врать, а повторный запуск не блокируется."
                )
                self._client = None

    # ── ключи ────────────────────────────────────────────────────────────

    @staticmethod
    def _status_key(integration_id: int) -> str:
        return f"crm:sync:status:{integration_id}"

    @staticmethod
    def _lock_key(integration_id: int) -> str:
        return f"crm:sync:lock:{integration_id}"

    # ── статус ───────────────────────────────────────────────────────────

    def get(self, integration_id: int) -> Optional[Dict[str, Any]]:
        if self._client is None:
            with self._memory_guard:
                value = self._memory.get(integration_id)
                return dict(value) if value else None
        try:
            raw = self._client.get(self._status_key(integration_id))
            return json.loads(raw) if raw else None
        except Exception as exc:
            logger.error(f"Не удалось прочитать статус синхронизации: {exc}")
            return None

    def set(self, integration_id: int, status: Dict[str, Any]) -> None:
        if self._client is None:
            with self._memory_guard:
                self._memory[integration_id] = dict(status)
            return
        try:
            self._client.setex(
                self._status_key(integration_id),
                STATUS_TTL_SECONDS,
                json.dumps(status, ensure_ascii=False, default=str),
            )
        except Exception as exc:
            logger.error(f"Не удалось записать статус синхронизации: {exc}")

    def update(self, integration_id: int, **fields: Any) -> Dict[str, Any]:
        """
        Дописать поля к текущему статусу.

        Не атомарно, и это осознанно: единственный писатель — задача
        синхронизации, а она у интеграции одна благодаря блокировке.
        """
        status = self.get(integration_id) or {}
        status.update(fields)
        self.set(integration_id, status)
        return status

    # ── блокировка ───────────────────────────────────────────────────────

    def acquire(self, integration_id: int, ttl: int = LOCK_TTL_SECONDS) -> bool:
        """
        Занять интеграцию под синхронизацию. False — прогон уже идёт.

        В Redis это ``SET NX EX`` — одна атомарная операция, поэтому гонка
        между воркерами невозможна.
        """
        if self._client is None:
            with self._memory_guard:
                if self._memory_locks.get(integration_id):
                    return False
                self._memory_locks[integration_id] = True
                return True
        try:
            return bool(self._client.set(self._lock_key(integration_id), "1",
                                         nx=True, ex=ttl))
        except Exception as exc:
            # Не блокируем работу из-за недоступного Redis: хуже отказать
            # в синхронизации, чем допустить второй прогон.
            logger.error(f"Не удалось взять блокировку синхронизации: {exc}")
            return True

    def release(self, integration_id: int) -> None:
        if self._client is None:
            with self._memory_guard:
                self._memory_locks.pop(integration_id, None)
            return
        try:
            self._client.delete(self._lock_key(integration_id))
        except Exception as exc:
            logger.error(f"Не удалось снять блокировку синхронизации: {exc}")

    def is_locked(self, integration_id: int) -> bool:
        if self._client is None:
            with self._memory_guard:
                return bool(self._memory_locks.get(integration_id))
        try:
            return bool(self._client.exists(self._lock_key(integration_id)))
        except Exception as exc:
            logger.error(f"Не удалось проверить блокировку синхронизации: {exc}")
            return False


_store: Optional[SyncStatusStore] = None


def get_sync_status_store() -> SyncStatusStore:
    """Единый экземпляр на процесс."""
    global _store
    if _store is None:
        _store = SyncStatusStore(os.getenv("REDIS_URL"))
    return _store
