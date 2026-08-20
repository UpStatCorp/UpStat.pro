"""
Проверка соответствия схемы БД коду. Вызывается на старте каждого процесса,
который ходит в базу: веб (create_app) и arq-воркер (WorkerSettings.on_startup).

Раньше проверка жила только в app/main.py и была сломана трижды:
  * ожидаемая версия захардкожена константой "018" при фактическом head 021 —
    то есть на ЗДОРОВОЙ базе все 4 uvicorn-воркера писали "Alembic version
    mismatch" на каждом старте, и настоящий рассинхрон в этом шуме был
    неразличим;
  * широкий `except Exception` глушил отсутствие таблицы alembic_version:
    база без миграций давала warning, старт продолжался, create_all создавал
    таблицы, и база навсегда оставалась без строки версии;
  * `SELECT ... LIMIT 1` без ORDER BY брал произвольную строку, если бы в
    alembic_version оказалось несколько голов.

Здесь сравниваются МНОЖЕСТВА голов: ScriptDirectory.get_heads() против
MigrationContext.get_current_heads(). Это разом снимает все три проблемы,
включая разнобой в формате идентификаторов ревизий ('021' против
'010_add_crm_entities') — сравниваются значения, а не парсятся.

Несоответствие — фатально. Раньше приложение продолжало старт и полагалось
на Base.metadata.create_all как на страховку; страховка убрана, потому что
она чинила только отсутствующие таблицы и при этом молча маскировала
расхождения (создавала таблицу, после чего guard в миграции пропускал её
и штамповал ревизию как применённую).
"""
from __future__ import annotations

import logging
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

_ROOT = Path(__file__).resolve().parent.parent

logger = logging.getLogger(__name__)


class SchemaOutOfSyncError(RuntimeError):
    """Схема БД не соответствует коду. Старт продолжать нельзя."""


def _expected_heads() -> set[str]:
    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "alembic"))
    return set(ScriptDirectory.from_config(cfg).get_heads())


def assert_schema_current(engine: sa.Engine | None = None) -> None:
    """
    Бросает SchemaOutOfSyncError, если миграции не накачены до head.

    Ошибки подключения к БД НЕ перехватываются: это отдельная авария с другой
    причиной, и она всё равно уронит старт на следующем шаге — но с честной
    трассой, а не с сообщением про миграции.
    """
    if engine is None:
        from database import engine as _engine

        engine = _engine

    expected = _expected_heads()

    with engine.connect() as conn:
        if not sa.inspect(conn).has_table("alembic_version"):
            raise SchemaOutOfSyncError(
                "Схема БД не инициализирована: таблицы alembic_version нет. "
                "Выполните `alembic upgrade head` ДО запуска приложения "
                "(см. docs/runbook.md, раздел «Порядок деплоя»)."
            )
        current = set(MigrationContext.configure(conn).get_current_heads())

    if current != expected:
        raise SchemaOutOfSyncError(
            f"Схема БД не соответствует коду: в базе {sorted(current) or '—'}, "
            f"ожидается {sorted(expected)}. "
            "Выполните `alembic upgrade head` ДО запуска приложения "
            "(см. docs/runbook.md, раздел «Порядок деплоя»)."
        )

    logger.info("Схема БД актуальна", extra={"revision": sorted(current)})
