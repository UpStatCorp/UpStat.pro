# UpStat.pro — Production Runbook

> **Аудитория:** инженеры, выполняющие деплой, дежурные on-call.
> Документ описывает процедуры для продакшн-окружения.
> Команды предполагают, что вы находитесь в директории проекта (`/opt/upstat`).

---

## Содержание

1. [Первоначальный деплой](#1-первоначальный-деплой)
2. [Деплой обновлений](#2-деплой-обновлений)
3. [Миграции базы данных](#3-миграции-базы-данных)
4. [Откат релиза](#4-откат-релиза)
5. [Ротация секретов](#5-ротация-секретов)
6. [Бэкапы и восстановление](#6-бэкапы-и-восстановление)
7. [Health checks](#7-health-checks)
8. [Мониторинг и логи](#8-мониторинг-и-логи)
9. [Аварийные процедуры](#9-аварийные-процедуры)

---

## 1. Первоначальный деплой

### 1.1 Требования к серверу
- Ubuntu 22.04 LTS (минимум 2 vCPU, 4 GB RAM, 40 GB SSD)
- Docker 24+ и Docker Compose v2
- Открытые порты: 80/443 (Nginx)
- Домены: `upstat.pro` и `train.upstat.pro` → A-записи на IP сервера

### 1.2 Настройка переменных

```bash
cp .env.example .env
nano .env  # заполнить все значения
```

**Обязательные переменные (должны быть без пустых значений перед запуском):**

| Переменная | Как сгенерировать |
|------------|------------------|
| `SECRET_KEY` | `openssl rand -hex 32` |
| `CRM_ENCRYPTION_KEY` | `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `POSTGRES_PASSWORD` | `openssl rand -hex 24` |
| `OPENAI_API_KEY` | platform.openai.com |
| `AZURE_VOICE_LIVE_ENDPOINT` / `API_KEY` | Azure Portal |
| `SMTP_USER` / `SMTP_PASSWORD` | Gmail app password |
| `PUBLIC_APP_URL` | `https://upstat.pro` |
| `TRAIN_PUBLIC_URL` | `https://train.upstat.pro` |

### 1.3 Последовательность запуска

```bash
# 1. Запустить только postgres и redis
docker compose up -d postgres redis

# 2. Дождаться healthy
docker compose ps   # postgres и redis → healthy

# 3. Применить миграции (ОДИН РАЗ, не на каждом воркере)
docker compose run --rm backend alembic upgrade head

# 4. Запустить остальные сервисы
docker compose up -d backend nginx

# 5. Проверить
curl -sf http://localhost/health && echo "OK"
```

### 1.4 Настройка бэкапов

```bash
# Создать директорию для бэкапов
sudo mkdir -p /var/backups/upstat
sudo chmod 700 /var/backups/upstat

# Добавить в crontab (от имени пользователя, запускающего docker)
crontab -e
```

Добавить строку (запуск каждый день в 03:00):

```cron
0 3 * * * /opt/upstat/scripts/backup_postgres.sh >> /var/log/upstat-backup.log 2>&1
```

---

## 2. Деплой обновлений

### 2.1 Стандартный rolling deploy (без миграций)

```bash
# Получить новый код
git pull origin main

# Собрать новый образ
docker compose build backend

# Перезапустить backend без простоя (Nginx буферизует запросы ~5s)
docker compose up -d --no-deps backend

# Проверить
docker compose ps
curl -sf http://localhost/health
```

### 2.2 Деплой с миграциями

Если релиз содержит новые Alembic-миграции:

```bash
git pull origin main
docker compose build backend

# ① Остановить backend (входящие WS-сессии завершатся)
docker compose stop backend

# ② Применить миграции
docker compose run --rm backend alembic upgrade head

# ③ Запустить новый backend
docker compose up -d --no-deps backend

# ④ Проверить
curl -sf http://localhost/health
docker compose logs backend --tail=50
```

> **Правило:** `alembic upgrade head` выполняется ровно один раз, до старта воркеров.
> Никогда не запускать одновременно с несколькими воркерами — DDL-гонка.

### 2.3 Проверка после деплоя

```bash
# Версия alembic совпадает с ожидаемой
docker compose exec backend alembic current

# Логи не содержат ERROR в первые 60 секунд
docker compose logs backend -f --since=60s | grep -v INFO

# Тест критичных эндпоинтов
curl -sf https://upstat.pro/health
curl -sf https://upstat.pro/ready
```

---

## 3. Миграции базы данных

### 3.1 Текущее состояние

```bash
docker compose exec backend alembic current
docker compose exec backend alembic history --verbose | head -20
```

### 3.2 Применить все ожидающие миграции

```bash
docker compose run --rm backend alembic upgrade head
```

### 3.3 Применить конкретную ревизию

```bash
docker compose run --rm backend alembic upgrade 018
```

### 3.4 Откатить одну ревизию

```bash
docker compose run --rm backend alembic downgrade -1
```

### 3.5 Создать новую миграцию (в разработке)

```bash
# Автогенерация из изменений models.py
docker compose run --rm backend alembic revision --autogenerate -m "add_column_x"

# Ревизия вручную (для DDL без ORM-модели)
docker compose run --rm backend alembic revision -m "create_index_y"
```

> **Соглашение:** файлы миграций именуются `NNN_краткое_описание.py`.
> Каждая операция обёрнута в `_table_exists()` / `_column_exists()` для идемпотентности.

---

## 4. Откат релиза

### 4.1 Быстрый откат образа (без изменений схемы)

```bash
# Найти предыдущий образ
docker images | grep upstat

# Откатить backend на предыдущий тег
docker compose stop backend
docker tag upstat-backend:previous upstat-backend:latest
docker compose up -d --no-deps backend
```

### 4.2 Откат с миграцией вниз

```bash
# 1. Остановить backend
docker compose stop backend

# 2. Откатить схему на нужную ревизию
docker compose run --rm backend alembic downgrade 017

# 3. Переключить код на предыдущий тег и запустить
docker compose up -d --no-deps backend
```

> ⚠️ Деструктивные downgrade (DROP TABLE, DROP COLUMN) необратимы.
> Если миграция удаляет данные — сделайте бэкап перед откатом.

---

## 5. Ротация секретов

### 5.1 SECRET_KEY (сессии пользователей)

Смена SECRET_KEY **аннулирует все активные сессии** — пользователи выйдут из системы.
Выполнять в период минимальной нагрузки.

```bash
# Сгенерировать новый ключ
NEW_KEY=$(openssl rand -hex 32)
echo "SECRET_KEY=${NEW_KEY}"

# Обновить .env
nano .env  # заменить SECRET_KEY

# Перезапустить backend
docker compose up -d --no-deps backend
```

### 5.2 CRM_ENCRYPTION_KEY (шифрование CRM-токенов)

> ⚠️ Смена ключа требует **ре-шифрования всех существующих CRM-токенов**.
> Без этого шага CRM-интеграции перестанут работать.

```bash
# Этап 1: сгенерировать новый ключ
NEW_CRM_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

# Этап 2: запустить скрипт ре-шифрования (разработать отдельно для вашей БД)
docker compose run --rm backend python3 scripts/reencrypt_crm_tokens.py \
    --old-key "${OLD_CRM_KEY}" --new-key "${NEW_CRM_KEY}"

# Этап 3: обновить .env и перезапустить
nano .env
docker compose up -d --no-deps backend
```

### 5.3 OPENAI_API_KEY / AZURE_VOICE_LIVE_API_KEY

```bash
nano .env  # заменить ключ
docker compose up -d --no-deps backend  # перезапуск без остановки трафика
```

### 5.4 POSTGRES_PASSWORD

```bash
NEW_PG_PASS=$(openssl rand -hex 24)

# 1. Изменить пароль в PostgreSQL
docker compose exec postgres psql -U saas_user -c \
    "ALTER USER saas_user PASSWORD '${NEW_PG_PASS}';"

# 2. Обновить .env
nano .env  # POSTGRES_PASSWORD и DATABASE_URL

# 3. Перезапустить backend (новый пул соединений)
docker compose up -d --no-deps backend
```

### 5.5 SMTP (Gmail app password)

```bash
# 1. Отозвать старый app password в Google Account → Security → App passwords
# 2. Создать новый app password
# 3. Обновить .env: SMTP_PASSWORD
nano .env
docker compose up -d --no-deps backend
```

---

## 6. Бэкапы и восстановление

### 6.1 Ручной бэкап

```bash
./scripts/backup_postgres.sh
# Дамп сохраняется в /var/backups/upstat/upstat_YYYYMMDD_HHMMSS.sql.gz
```

### 6.2 Проверка бэкапов

```bash
# Список доступных дампов
ls -lh /var/backups/upstat/

# Проверить целостность последнего дампа (gunzip без распаковки)
gunzip -t /var/backups/upstat/$(ls -t /var/backups/upstat/ | head -1)
echo "Integrity OK"

# Размер и количество
find /var/backups/upstat -name "*.sql.gz" | wc -l
du -sh /var/backups/upstat/
```

### 6.3 Восстановление из бэкапа

> ⚠️ Уничтожает текущие данные. Сначала сделайте свежий бэкап.

```bash
# Сделать бэкап текущего состояния перед восстановлением
./scripts/backup_postgres.sh

# Восстановить из файла
./scripts/restore_postgres.sh /var/backups/upstat/upstat_20260617_030000.sql.gz
```

Скрипт:
1. Просит подтверждение (ввести имя базы)
2. Останавливает `backend`
3. Пересоздаёт базу данных
4. Восстанавливает дамп через `psql`
5. Запускает `alembic upgrade head`
6. Стартует `backend`

### 6.4 Тест восстановления (dry-run)

```bash
DRY_RUN=1 ./scripts/restore_postgres.sh /var/backups/upstat/upstat_20260617_030000.sql.gz
```

### 6.5 Автоматические бэкапы — настройка cron

```bash
crontab -l  # проверить текущий crontab
crontab -e  # добавить строку:
```

```
# UpStat: ежедневный бэкап PostgreSQL в 03:00
0 3 * * * /opt/upstat/scripts/backup_postgres.sh >> /var/log/upstat-backup.log 2>&1
```

Проверить последний запуск:

```bash
tail -30 /var/log/upstat-backup.log
```

### 6.6 Перенос бэкапов во внешнее хранилище (опционально)

```bash
# Пример: синхронизация в S3 (требует aws-cli)
aws s3 sync /var/backups/upstat/ s3://your-bucket/upstat-backups/ \
    --storage-class STANDARD_IA \
    --delete

# Добавить в crontab после основного бэкапа:
30 3 * * * aws s3 sync /var/backups/upstat/ s3://your-bucket/upstat-backups/ --storage-class STANDARD_IA --delete >> /var/log/upstat-backup.log 2>&1
```

---

## 7. Health checks

### 7.1 Эндпоинты

| Эндпоинт | Назначение | Ожидаемый ответ |
|----------|-----------|----------------|
| `GET /health` | Liveness (приложение запущено) | `200 {"status": "ok"}` |
| `GET /ready` | Readiness (БД + Redis доступны) | `200 {"status": "ready"}` |

```bash
# Быстрая проверка
curl -sf http://localhost/health | python3 -m json.tool
curl -sf http://localhost/ready  | python3 -m json.tool

# Проверка через HTTPS
curl -sf https://upstat.pro/health
curl -sf https://train.upstat.pro/health
```

### 7.2 Docker healthchecks

```bash
docker compose ps  # колонка STATUS должна показывать "healthy"
```

### 7.3 Ручная проверка Alembic

```bash
docker compose exec backend alembic current
# Должна показать ревизию "018 (head)"
```

---

## 8. Мониторинг и логи

### 8.1 Просмотр логов

```bash
# Все сервисы, последние 100 строк
docker compose logs --tail=100

# Backend в реальном времени
docker compose logs -f backend

# Только ошибки
docker compose logs backend | grep -E '"level":"ERROR"'

# Конкретный request_id
docker compose logs backend | grep '"request_id":"abc-123"'
```

### 8.2 Структура JSON-лога

Все логи приложения в формате JSON (python-json-logger):

```json
{
  "asctime": "2026-06-17 03:00:00,123",
  "service": "upstat",
  "logger": "session_cleanup",
  "level": "INFO",
  "message": "Session cleanup complete",
  "sessions": 5
}
```

### 8.3 Sentry

Если `SENTRY_DSN` задан в `.env`, ошибки уровня ERROR автоматически отправляются в Sentry.

```bash
# Проверить, что DSN задан
grep SENTRY_DSN .env
```

### 8.4 Rate limit и Redis

```bash
# Проверить, что Redis живой
docker compose exec redis redis-cli ping  # PONG

# Посмотреть ключи rate-limit
docker compose exec redis redis-cli keys "rl:*" | head -20
```

---

## 9. Аварийные процедуры

### 9.1 Backend не стартует (crash loop)

```bash
docker compose logs backend --tail=50

# Частые причины:
# - DATABASE_URL не задан или неправильный пароль
# - SECRET_KEY короче 32 символов
# - Alembic upgrade head не был выполнен → таблицы отсутствуют
# - Порт 8000 занят другим процессом

# Диагностика
docker compose run --rm backend python3 -c "from app.main import create_app; create_app()"
```

### 9.2 PostgreSQL недоступен

```bash
docker compose ps postgres  # проверить статус

# Если unhealthy — перезапустить
docker compose restart postgres
docker compose exec postgres pg_isready -U saas_user -d saas

# Если данные повреждены — восстановить из бэкапа (раздел 6.3)
```

### 9.3 Redis недоступен

Rate-limit автоматически переключается на in-memory fallback с WARNING в логах.
Функциональность сохраняется, но rate-limit не разделяется между воркерами.

```bash
docker compose restart redis
docker compose logs redis --tail=20
```

### 9.4 Голосовые WebSocket-сессии зависли

```bash
# Проверить активные сессии
curl -sf http://localhost/api/voice/stats  # если эндпоинт доступен

# Принудительный сброс — перезапустить backend (все WS-сессии закрываются)
docker compose restart backend
```

### 9.5 Disk full (диск переполнен)

```bash
df -h  # найти полный раздел

# Очистить Docker-кэш (не трогает volumes)
docker system prune -f

# Очистить старые логи
truncate -s 0 /var/log/upstat-backup.log

# Удалить лишние бэкапы вручную (оставить последние N)
ls -t /var/backups/upstat/*.sql.gz | tail -n +8 | xargs rm -f
```

### 9.6 Утечка/компрометация секрета

1. Ротировать скомпрометированный секрет (раздел 5)
2. Проверить git-историю: `git log --all -p -- .env` — если секрет попал в коммит, считать его публичным
3. Отозвать API-ключ в источнике (OpenAI, Azure, Google, Gmail)
4. Проверить access-логи на предмет несанкционированного использования
5. Уведомить команду

---

## Чеклист деплоя (распечатать и отмечать)

- [ ] `.env` заполнен, нет пустых обязательных переменных
- [ ] `SECRET_KEY` ≥ 32 символа
- [ ] `HTTPS_ONLY=true` / `ENVIRONMENT=production` в `.env`
- [ ] `ALLOW_QUERY_USER_ID=false`
- [ ] `CORS_ORIGINS` = конкретные домены (не `*`)
- [ ] PostgreSQL и Redis healthy (`docker compose ps`)
- [ ] `alembic upgrade head` выполнен без ошибок
- [ ] `docker compose up -d` → все сервисы healthy
- [ ] `GET /health` → 200
- [ ] `GET /ready` → 200
- [ ] Логи backend → нет ERROR в первые 2 минуты
- [ ] Cron-бэкап настроен и протестирован (`DRY_RUN=1`)
- [ ] Sentry DSN задан и получает test-событие
- [ ] SSL-сертификат валиден (`openssl s_client -connect upstat.pro:443`)
