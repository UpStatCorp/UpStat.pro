#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# restore_postgres.sh — восстановление БД из gzip-дампа.
#
# ⚠️  ВНИМАНИЕ: скрипт УДАЛЯЕТ все данные в целевой базе перед восстановлением.
#     Используйте только осознанно. В prod — только после согласования с командой.
#
# Использование:
#   ./scripts/restore_postgres.sh /var/backups/upstat/upstat_20260617_030000.sql.gz
#
# Опциональные переменные окружения:
#   COMPOSE_FILE     Путь к docker-compose.yml     (default: рядом со scripts/)
#   POSTGRES_USER    Пользователь PostgreSQL        (default: saas_user)
#   POSTGRES_DB      База данных для восстановления  (default: saas)
#   DRY_RUN=1        Только показать команды, не выполнять
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

BACKUP_FILE="${1:-}"
if [[ -z "${BACKUP_FILE}" ]]; then
    echo "Usage: $0 <path/to/upstat_YYYYMMDD_HHMMSS.sql.gz>" >&2
    exit 1
fi
[[ -f "${BACKUP_FILE}" ]] || { echo "ERROR: file not found: ${BACKUP_FILE}" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"
COMPOSE_FILE="${COMPOSE_FILE:-${PROJECT_DIR}/docker-compose.yml}"
POSTGRES_USER="${POSTGRES_USER:-saas_user}"
POSTGRES_DB="${POSTGRES_DB:-saas}"
DRY_RUN="${DRY_RUN:-0}"

log() { echo "[restore] $(date '+%Y-%m-%d %H:%M:%S') $*" >&2; }

[[ -f "${COMPOSE_FILE}" ]] || { log "ERROR: compose file not found: ${COMPOSE_FILE}"; exit 1; }

log "=== UpStat PostgreSQL Restore ==="
log "Source file  : ${BACKUP_FILE} ($(du -sh "${BACKUP_FILE}" | cut -f1))"
log "Target DB    : ${POSTGRES_DB}@postgres (user: ${POSTGRES_USER})"
log "Compose file : ${COMPOSE_FILE}"

# ── Подтверждение ────────────────────────────────────────────────────────────

if [[ "${DRY_RUN}" != "1" ]]; then
    echo ""
    echo "⚠️  Это действие УНИЧТОЖИТ все данные в базе '${POSTGRES_DB}'."
    echo "    Убедитесь, что у вас есть свежий бэкап и стек остановлен (кроме postgres)."
    echo ""
    read -r -p "Введите имя базы данных для подтверждения [${POSTGRES_DB}]: " CONFIRM
    if [[ "${CONFIRM}" != "${POSTGRES_DB}" ]]; then
        log "Отменено."
        exit 1
    fi
fi

# ── Остановить backend и redis (postgres оставить живым) ──────────────────────

if [[ "${DRY_RUN}" == "1" ]]; then
    log "[DRY_RUN] docker compose stop backend"
    log "[DRY_RUN] drop + create database"
    log "[DRY_RUN] gunzip | psql"
    log "[DRY_RUN] docker compose start backend"
    exit 0
fi

log "Stopping backend service..."
docker compose -f "${COMPOSE_FILE}" stop backend 2>/dev/null || true

# ── Пересоздать базу ──────────────────────────────────────────────────────────

log "Dropping and recreating database '${POSTGRES_DB}'..."
docker compose -f "${COMPOSE_FILE}" exec -T postgres \
    psql -U "${POSTGRES_USER}" -c \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='${POSTGRES_DB}' AND pid <> pg_backend_pid();" \
    postgres >/dev/null 2>&1 || true

docker compose -f "${COMPOSE_FILE}" exec -T postgres \
    psql -U "${POSTGRES_USER}" -c "DROP DATABASE IF EXISTS \"${POSTGRES_DB}\";" postgres

docker compose -f "${COMPOSE_FILE}" exec -T postgres \
    psql -U "${POSTGRES_USER}" -c "CREATE DATABASE \"${POSTGRES_DB}\" OWNER \"${POSTGRES_USER}\";" postgres

# ── Восстановить дамп ────────────────────────────────────────────────────────

log "Restoring from ${BACKUP_FILE}..."
gunzip -c "${BACKUP_FILE}" \
    | docker compose -f "${COMPOSE_FILE}" exec -T postgres \
        psql --no-password -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -q \
    || { log "ERROR: restore failed — check psql output above"; exit 1; }

log "Database restored successfully."

# ── Применить незакрытые миграции ────────────────────────────────────────────

log "Running 'alembic upgrade head' to ensure schema is current..."
docker compose -f "${COMPOSE_FILE}" run --rm backend \
    alembic upgrade head \
    || log "WARNING: alembic upgrade failed — run manually before starting backend"

# ── Перезапустить backend ────────────────────────────────────────────────────

log "Starting backend service..."
docker compose -f "${COMPOSE_FILE}" start backend

log "=== Restore complete. Run 'docker compose ps' to verify health checks. ==="
