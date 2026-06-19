#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# backup_postgres.sh — ежедневный дамп PostgreSQL + ротация 30 дней.
#
# Использование:
#   ./scripts/backup_postgres.sh                      # дефолты ниже
#   BACKUP_DIR=/mnt/nas/backups ./scripts/backup_postgres.sh
#
# Переменные окружения (все опциональны):
#   BACKUP_DIR       Директория для хранения дампов  (default: /var/backups/upstat)
#   RETENTION_DAYS   Хранить дампы N дней            (default: 30)
#   COMPOSE_PROJECT  Имя проекта docker compose       (default: auto-detect)
#   COMPOSE_FILE     Путь к docker-compose.yml        (default: script's parent dir)
#   POSTGRES_USER    Пользователь PostgreSQL          (default: saas_user)
#   POSTGRES_DB      База данных                       (default: saas)
#   NOTIFY_EMAIL     Отправить отчёт на email         (default: пусто = не отправлять)
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Конфигурация ──────────────────────────────────────────────────────────────
BACKUP_DIR="${BACKUP_DIR:-/var/backups/upstat}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
POSTGRES_USER="${POSTGRES_USER:-saas_user}"
POSTGRES_DB="${POSTGRES_DB:-saas}"
NOTIFY_EMAIL="${NOTIFY_EMAIL:-}"

# Определяем директорию проекта (родитель папки scripts/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"
COMPOSE_FILE="${COMPOSE_FILE:-${PROJECT_DIR}/docker-compose.yml}"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
FILENAME="upstat_${TIMESTAMP}.sql.gz"
BACKUP_PATH="${BACKUP_DIR}/${FILENAME}"
LOG_PREFIX="[backup] $(date '+%Y-%m-%d %H:%M:%S')"

# ── Функции ───────────────────────────────────────────────────────────────────

log() { echo "${LOG_PREFIX} $*" >&2; }

fail() {
    log "ERROR: $*"
    if [[ -n "${NOTIFY_EMAIL}" ]]; then
        echo "UpStat backup FAILED on $(hostname) at $(date): $*" \
            | mail -s "[ALERT] UpStat backup failed" "${NOTIFY_EMAIL}" 2>/dev/null || true
    fi
    exit 1
}

# ── Предусловия ───────────────────────────────────────────────────────────────

command -v docker >/dev/null 2>&1 || fail "docker not found in PATH"
[[ -f "${COMPOSE_FILE}" ]] || fail "docker-compose.yml not found at ${COMPOSE_FILE}"

mkdir -p "${BACKUP_DIR}" || fail "Cannot create backup directory ${BACKUP_DIR}"

# ── Дамп ─────────────────────────────────────────────────────────────────────

log "Starting backup → ${BACKUP_PATH}"

# pg_dump через docker compose exec (postgres не открывает порт наружу)
docker compose -f "${COMPOSE_FILE}" exec -T postgres \
    pg_dump \
        --no-password \
        -U "${POSTGRES_USER}" \
        "${POSTGRES_DB}" \
    | gzip -9 > "${BACKUP_PATH}" \
    || fail "pg_dump failed (see docker logs for details)"

BACKUP_SIZE="$(du -sh "${BACKUP_PATH}" | cut -f1)"
log "Backup OK: ${BACKUP_PATH} (${BACKUP_SIZE})"

# ── Ротация ───────────────────────────────────────────────────────────────────

PRUNED="$(find "${BACKUP_DIR}" -name "upstat_*.sql.gz" -mtime "+${RETENTION_DAYS}" -print -delete | wc -l | tr -d ' ')"
log "Pruned ${PRUNED} backup(s) older than ${RETENTION_DAYS} days"

# ── Итоговая статистика ───────────────────────────────────────────────────────

TOTAL="$(find "${BACKUP_DIR}" -name "upstat_*.sql.gz" | wc -l | tr -d ' ')"
OLDEST="$(find "${BACKUP_DIR}" -name "upstat_*.sql.gz" | sort | head -1 | xargs basename 2>/dev/null || echo 'n/a')"
log "Backup store: ${TOTAL} file(s), oldest=${OLDEST}"

# Уведомление об успехе (опционально)
if [[ -n "${NOTIFY_EMAIL}" ]]; then
    echo "Backup OK: ${BACKUP_PATH} (${BACKUP_SIZE}), ${TOTAL} total backup(s)" \
        | mail -s "[OK] UpStat backup $(date +%Y-%m-%d)" "${NOTIFY_EMAIL}" 2>/dev/null || true
fi

log "Done."
