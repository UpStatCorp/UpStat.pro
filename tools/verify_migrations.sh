#!/usr/bin/env bash
#
# Критерий приёмки миграций:
#   на ЧИСТОЙ базе `alembic upgrade head` проходит без ошибок,
#   после чего `alembic revision --autogenerate` даёт ПУСТУЮ миграцию.
#
# Плюс проверка обратимости: downgrade base -> upgrade head. Она не входила
# в исходную формулировку, но нужна — в истории уже есть асимметричные
# downgrade'ы (014 откатывает то, чего не накатывала; 020 намеренно пустая
# вниз). Если обратимость сломана, знать об этом надо сейчас, а не в момент,
# когда откат понадобится по-настоящему.
#
# Запуск:
#   DATABASE_URL=postgresql://user:pass@host:5432/empty_db bash tools/verify_migrations.sh
#
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL не задан}"
cd "$(dirname "$0")/.."
export PYTHONPATH="${PYTHONPATH:-}:$PWD:$PWD/app"

PROBE_ID="zzzprobe"
PROBE_GLOB="alembic/versions/${PROBE_ID}_*.py"

cleanup() { rm -f ${PROBE_GLOB}; }
trap cleanup EXIT

echo "== 1. База должна быть пустой =="
python3 - <<'PY'
import os, sqlalchemy as sa
e = sa.create_engine(os.environ["DATABASE_URL"])
with e.connect() as c:
    tables = sa.inspect(c).get_table_names()
assert not tables, f"База не пуста, найдено {len(tables)} таблиц: {sorted(tables)[:5]}..."
print("   ok — 0 таблиц")
PY

echo "== 2. Ровно одна голова =="
heads=$(python3 -m alembic heads 2>/dev/null | grep -c "(head)" || true)
if [ "$heads" -ne 1 ]; then
    python3 -m alembic heads
    echo "ОШИБКА: голов $heads, должна быть 1"
    exit 1
fi
echo "   ok — 1 голова"

echo "== 3. upgrade head =="
python3 -m alembic upgrade head 2>&1 | grep -E "Running upgrade|ERROR" || true
echo "   ok"

echo "== 4. autogenerate должен быть ПУСТ =="
rm -f ${PROBE_GLOB}
python3 -m alembic revision --autogenerate -m "probe" --rev-id "${PROBE_ID}" >/dev/null 2>&1
probe=$(ls ${PROBE_GLOB})
python3 - "$probe" <<'PY'
import ast, sys
path = sys.argv[1]
src = open(path, encoding="utf-8").read()
for fn in ast.parse(src).body:
    if isinstance(fn, ast.FunctionDef) and fn.name == "upgrade":
        body = [
            n for n in fn.body
            if not isinstance(n, ast.Pass)
            and not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))
        ]
        if body:
            print("ДИФФ НЕ ПУСТ — модели и схема разошлись:\n")
            print(src)
            sys.exit(1)
print("   ok — autogenerate пуст")
PY
rm -f ${PROBE_GLOB}

echo "== 5. Обратимость: downgrade base -> upgrade head =="
python3 -m alembic downgrade base 2>&1 | grep -cE "Running downgrade" >/dev/null
python3 - <<'PY'
import os, sqlalchemy as sa
e = sa.create_engine(os.environ["DATABASE_URL"])
with e.connect() as c:
    tables = set(sa.inspect(c).get_table_names())
extra = tables - {"alembic_version"}
assert not extra, f"после downgrade base остались таблицы: {sorted(extra)}"
print("   ok — после downgrade base таблиц не осталось")
PY
python3 -m alembic upgrade head >/dev/null 2>&1
echo "   ok — повторный upgrade head прошёл"

echo
echo "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ"
