"""
Генератор baseline-миграции 001 из app/models.py.

Зачем скрипт, а не рукописный файл: baseline — это 9 таблиц и 71 колонка,
и любое расхождение типа с моделью ломает критерий приёмки (в alembic 1.12.1
compare_type по умолчанию True, см. alembic/runtime/migration.py:181).
Ловушки вроде `users.updated_at = mapped_column(String)` — String БЕЗ длины,
в базе `character varying` без ограничения — руками воспроизводятся неточно.
Поэтому типы рендерит сам alembic из Base.metadata.

ПРАВИЛО BASELINE:
    baseline = определение в app/models.py МИНУС всё, что добавляют 002-021.
    * колонка исключается, если её добавляет любая миграция 002-021;
    * индекс исключается, если хотя бы одна его колонка исключена.

Второе правило неочевидно, но обязательно: 003 добавляет не только
users.role, но и индекс ix_users_role по этой колонке, причём БЕЗ guard.
Оставить индекс в baseline — падение на CREATE INDEX вместо ADD COLUMN.

Запуск:
    DATABASE_URL=postgresql://... python tools/gen_baseline.py > /tmp/001_body.py
Проверка списков против самих миграций (без записи файла):
    python tools/gen_baseline.py --verify
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "app"))
sys.path.insert(0, str(_ROOT))
os.environ.setdefault("DATABASE_URL", "postgresql://gen:gen@localhost:5432/gen")

from alembic.autogenerate.api import AutogenContext  # noqa: E402
from alembic.autogenerate.render import _add_index, _add_table  # noqa: E402
from alembic.migration import MigrationContext  # noqa: E402
from alembic.operations.ops import CreateIndexOp, CreateTableOp  # noqa: E402
from sqlalchemy.dialects import postgresql  # noqa: E402

from database import Base  # noqa: E402,F401
import models  # noqa: E402,F401


# Таблицы, которые ни одна миграция 002-021 не создаёт: их создавал create_all.
# win_probability_scores / checklist_item_* сюда НЕ входят — они уезжают в 022,
# потому что на проде существуют с живыми данными, а на чистой базе их нет.
BASELINE_TABLES = [
    "users",
    "conversations",
    "messages",
    "attachments",
    "teams",
    "team_members",
    "analysis_training_plans",
    "trainings",
    "training_sessions",
]

VERSIONS_DIR = _ROOT / "alembic" / "versions"


def columns_added_by_migrations() -> dict[str, set[str]]:
    """
    Собирает op.add_column по всем миграциям в alembic/versions, включая
    вложенные в guard-условия (`if not _column_exists(...)`). Guard-статус
    здесь не важен: если миграция колонку добавляет — в baseline её быть
    не должно, иначе guard промолчит и мы закрепим ту же маскировку,
    ради устранения которой всё и затевается.
    """
    added: dict[str, set[str]] = {}
    for path in sorted(VERSIONS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (
                isinstance(fn, ast.Attribute)
                and isinstance(fn.value, ast.Name)
                and fn.value.id == "op"
                and fn.attr == "add_column"
                and len(node.args) >= 2
            ):
                continue
            try:
                table = ast.literal_eval(node.args[0])
            except Exception:
                continue
            col_call = node.args[1]
            if not (isinstance(col_call, ast.Call) and col_call.args):
                continue
            try:
                column = ast.literal_eval(col_call.args[0])
            except Exception:
                continue
            added.setdefault(table, set()).add(column)
    return added


def build_context() -> AutogenContext:
    mc = MigrationContext.configure(
        dialect=postgresql.dialect(),
        opts={"as_sql": True},
    )
    ctx = AutogenContext.__new__(AutogenContext)
    ctx.migration_context = mc
    ctx.dialect = mc.dialect
    ctx.imports = set()
    ctx.opts = {"sqlalchemy_module_prefix": "sa.", "alembic_module_prefix": "op."}
    ctx.metadata = Base.metadata
    ctx._has_batch = False
    return ctx


def render(verify_only: bool = False) -> str:
    added = columns_added_by_migrations()
    ctx = build_context()
    chunks: list[str] = []
    report: list[str] = []
    total_cols = 0

    for name in BASELINE_TABLES:
        table = Base.metadata.tables[name]
        drop = added.get(name, set()) & {c.name for c in table.columns}

        # Отцепляем копию таблицы, чтобы не мутировать Base.metadata:
        # to_metadata даёт независимый объект в отдельном MetaData.
        from sqlalchemy import MetaData

        tmp = MetaData()
        t = table.to_metadata(tmp)

        # Ограничения, ссылающиеся на исключаемую колонку, снимаем ПЕРВЫМИ:
        # SQLAlchemy не убирает FK/Unique автоматически вслед за колонкой и
        # падает при рендере (ConstraintColumnNotFoundError).
        for const in list(t.constraints):
            if {c.name for c in getattr(const, "columns", [])} & drop:
                t.constraints.discard(const)

        # Индекс уходит вместе с колонкой.
        kept_indexes = []
        dropped_indexes = []
        for ix in sorted(t.indexes, key=lambda i: i.name or ""):
            ix_cols = {c.name for c in ix.columns}
            (dropped_indexes if ix_cols & drop else kept_indexes).append(ix)
        t.indexes = set(kept_indexes)

        for col_name in sorted(drop):
            t._columns.remove(t.c[col_name])

        # Индексы, объявленные через Column(index=True), alembic рендерит
        # отдельно от create_table — собираем их из отцепленной таблицы.
        total_cols += len(t.columns)
        report.append(
            f"{name}: колонок {len(table.columns)} -> {len(t.columns)}"
            + (f", исключены: {sorted(drop)}" if drop else "")
            + (
                f", индексы исключены: {sorted(i.name for i in dropped_indexes)}"
                if dropped_indexes
                else ""
            )
        )

        chunks.append(_add_table(ctx, CreateTableOp.from_table(t)))
        for ix in sorted(kept_indexes, key=lambda i: i.name or ""):
            chunks.append(_add_index(ctx, CreateIndexOp.from_index(ix)))

    print("\n".join(report), file=sys.stderr)
    print(f"Итого колонок в baseline: {total_cols}", file=sys.stderr)
    print(f"Таблиц: {len(BASELINE_TABLES)}", file=sys.stderr)
    return "\n".join("    " + line for chunk in chunks for line in chunk.splitlines())


BASELINE_FILE = _ROOT / "alembic" / "versions" / "001_initial.py"


def verify(body: str) -> int:
    """
    Сверяет сгенерированное тело с тем, что лежит в 001_initial.py.

    Нужно как CI-проверка: baseline создаётся из app/models.py, и если модели
    поменяли, а baseline не перегенерировали, состав колонок разъедется молча.
    Пустой autogenerate это поймает не всегда — исключённая колонка может
    добавляться более поздней миграцией, и итоговая схема сойдётся, а baseline
    при этом будет описывать не то состояние.
    """
    committed = BASELINE_FILE.read_text(encoding="utf-8")
    _, _, tail = committed.partition("def upgrade():\n")
    current, _, _ = tail.partition("\n\ndef downgrade():")

    if current.strip() == body.strip():
        print("\nbaseline в 001_initial.py совпадает с моделями", file=sys.stderr)
        return 0

    import difflib

    print("\nBASELINE РАЗОШЁЛСЯ С МОДЕЛЯМИ.", file=sys.stderr)
    print("Перегенерируйте: python tools/gen_baseline.py\n", file=sys.stderr)
    diff = difflib.unified_diff(
        current.strip().splitlines(),
        body.strip().splitlines(),
        fromfile="alembic/versions/001_initial.py",
        tofile="tools/gen_baseline.py (ожидается)",
        lineterm="",
    )
    print("\n".join(diff), file=sys.stderr)
    return 1


if __name__ == "__main__":
    body = render()
    if "--verify" in sys.argv:
        sys.exit(verify(body))
    if body:
        print(body)
