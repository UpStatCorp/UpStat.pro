# Карта кодовой базы UpStat.pro

> Первичная разведка для планирования рефакторинга. Дата: 2026-08-06, ветка `main`, коммит `08bf608`.
> Только чтение — изменений в код не вносилось.

## Содержание

1. [Стек](#1-стек)
2. [Размер](#2-размер)
3. [Структура](#3-структура)
4. [Точки входа](#4-точки-входа)
5. [Модель данных](#5-модель-данных)
6. [Внешние интеграции](#6-внешние-интеграции)
7. [Тесты и качество](#7-тесты-и-качество)
8. [Где сложнее всего](#8-где-сложнее-всего)
9. [Неоднородность](#9-неоднородность)

---

## 1. СТЕК

**Язык:** Python 3.11 (`Dockerfile` — `python:3.11-slim`, `pyproject.toml` — `target-version = "py311"`). Фронтенд — Jinja2-шаблоны + ванильный JS (без сборщика, без TypeScript, `package.json` есть только в `sdk-runner/`).

**Фреймворки:**
- FastAPI 0.104.1, Uvicorn 0.24.0 (`[standard]`), Starlette-middleware (Session, CORS, BaseHTTPMiddleware)
- Jinja2 3.1.2 — серверный рендеринг всех страниц
- Pydantic ≥2.5

**БД и работа с ней:**
- PostgreSQL 15 (`postgres:15-alpine`). SQLite явно запрещён — `app/database.py:8-11` кидает `ValueError`, если в `DATABASE_URL` есть `sqlite`.
- SQLAlchemy 2.0.23, синхронный `create_engine` + `sessionmaker`, декларативные модели с `Mapped[]`/`mapped_column` в `app/models.py`.
- Пул: `pool_size=20`, `max_overflow=40`, `pool_pre_ping`, `pool_recycle=3600` — всё через env (`app/database.py:19-27`).
- Миграции: Alembic 1.12.1. **Два набора версий**: `alembic/versions/` (21 файл, до `021_add_training_session_status_index`) и `app/alembic/versions/` (17 файлов, до `017`). В образ копируется только корневой `alembic/` (`Dockerfile:22-23`) — `app/alembic/` мёртвый.
- Одновременно с миграциями на старте вызывается `Base.metadata.create_all` (`app/main.py:131`) и проверка версии, где ожидаемая версия захардкожена как `"018"` (`app/main.py:70`) — рассинхрон с фактическим head `021`.
- Сырой SQL через `text()` — в 20+ файлах, включая роутеры (`app/routers/analytics.py`, `app/routers/chat.py`, `app/routers/crm_integration.py`, `app/routers/training_program.py`).

**Внешние сервисы и API:**

| Сервис | Назначение | Библиотека |
|---|---|---|
| OpenAI | LLM-анализ, Whisper STT | `openai>=1.12.0`, `GPT_MODEL=gpt-4o-mini` |
| Azure Voice Live API | голосовые тренировки (realtime WS) | `azure-identity`, `azure-core`, API-версия `2026-07-15`, модель `gpt-4o-realtime-preview` |
| ElevenLabs | TTS (опционально) | HTTP через `httpx`/`requests`, `api.elevenlabs.io` |
| amoCRM | синк звонков/сделок | OAuth2, `www.amocrm.ru` |
| Bitrix24 | синк звонков/сделок | OAuth2 + webhook, `oauth.bitrix.info` |
| Zoom | встречи, SDK-signature, транскрипты | `api.zoom.us` |
| Google OAuth | вход | `accounts.google.com`, `oauth2.googleapis.com` |
| SMTP | письма (верификация, сброс пароля) | `smtplib` через `app/services/email.py` |
| Sentry | мониторинг ошибок | `sentry-sdk[fastapi]>=2.0.0` |
| Deepgram | упоминается в `ai_agent_service/` | — (предположение: альтернативный STT, не в основном requirements) |

**Очереди, кеши, планировщики:**
- Redis 7 (`redis==5.0.1`) — брокер arq + кеш (`app/services/caching_service.py`) + локи/fairness (`app/services/job_control.py`).
- arq (`>=0.25,<1.0`) — очередь тяжёлых анализов. Тумблер `USE_QUEUE` (`app/services/queue.py:22`); при `false` — фолбэк на `BackgroundTasks`.
- **Планировщика нет** — ни cron, ни APScheduler, ни Celery-beat. Единственная периодика: `asyncio`-цикл `_session_cleanup_loop` раз в 5 мин внутри веб-процесса (`app/main.py:77-91`) и «ленивая» переобработка зависших CRM-записей по таймауту `STUCK_RECORDING_TIMEOUT_MIN` при запросе (`app/routers/crm_integration.py:612`).

**Запуск:** Docker Compose (`docker-compose.yml`) — 5 сервисов: `postgres`, `redis`, `backend` (uvicorn, `WEB_CONCURRENCY=4` воркеров), `worker` (`arq worker.WorkerSettings`, `WORKER_REPLICAS=2`), `nginx` (TLS-терминация, проксирование, отдельные `location` для WS: `/voice-assistant/`, `/voice-training/`). Оверлеи: `docker-compose.override.yml` (авто, dev-режим + внешний volume), `docker-compose.dev.yml` (bind-mount + `--reload` + pgAdmin). Локально — `run.sh`. systemd нет.

---

## 2. РАЗМЕР

**Всего: 263 файла с кодом, 88 584 строки** (без `venv/`, `.git/`, `node_modules/`, `__pycache__`, кешей).

| Тип | Файлов | Строк |
|---|---:|---:|
| `.py` | 184 | 46 956 |
| `.html` (Jinja2 + статика) | 63 | 24 214 |
| `.css` | 5 | 9 984 |
| `.js` | 11 | 7 430 |
| `.ts` | 0 | 0 |

**По директориям верхнего уровня:**

| Директория | Файлов | Строк |
|---|---:|---:|
| `app/` | 178 | 67 918 |
| `voice_assistant/` | 17 | 7 041 |
| `ai_agent_service/` | 11 | 3 524 |
| корень (скрипты) | 15 | 2 083 |
| `Voice-Live-Api-main/` | 5 | 1 916 |
| `alembic/` | 22 | 1 826 |
| `tests/` | 10 | 1 689 |
| `docs/` | 3 | 1 604 |
| `sdk-runner/` | 3 | 983 |
| `scripts/` | 0 (только `.sh`) | — |

**Топ-15 файлов:**

| # | Файл | Строк |
|---:|---|---:|
| 1 | `app/static/css/voice-training.css` | 4 789 |
| 2 | `app/static/styles.css` | 3 917 |
| 3 | `app/static/js/voice-training.js` | 3 314 |
| 4 | `app/services/crm_service.py` | 2 387 |
| 5 | `app/templates/landing.html` | 1 975 |
| 6 | `app/routers/crm_integration.py` | 1 537 |
| 7 | `voice_assistant/websocket_handler.py` | 1 451 |
| 8 | `app/services/pipeline.py` | 1 278 |
| 9 | `app/static/js/webrtc-meeting.js` | 1 258 |
| 10 | `app/templates/analytics.html` | 1 168 |
| 11 | `ai_agent_service/main.py` | 1 154 |
| 12 | `app/models.py` | 1 144 |
| 13 | `app/templates/webrtc_meeting.html` | 1 132 |
| 14 | `app/services/research_service.py` | 1 065 |
| 15 | `voice_assistant/web/index.html` | 1 058 |

Крупнейший Python-файл — `crm_service.py` (2 387 строк); крупнейшие файлы вообще — CSS/JS фронтенда.

---

## 3. СТРУКТУРА

```
.
├── app/                          Основное FastAPI-приложение (монолит)
│   ├── main.py                   Фабрика create_app: middleware, i18n, подключение ~25 роутеров
│   ├── models.py                 Все 44 SQLAlchemy-модели в одном файле
│   ├── database.py               engine/SessionLocal/get_db
│   ├── deps.py                   require_user, require_capability (авторизация)
│   ├── security.py               Хеши паролей, JWT, security-headers
│   ├── worker.py                 arq WorkerSettings + 2 джоба (анализ CRM-записи, пайплайн из чата)
│   ├── schemas.py                Pydantic-схемы (только 5 КБ — большинство роутеров без схем)
│   ├── logging_config.py         JSON-логи + инициализация Sentry
│   ├── time_utils.py             Локальные даты/таймзоны
│   ├── routers/                  26 роутеров: HTTP-эндпоинты + HTML-страницы
│   ├── services/                 44 сервиса: бизнес-логика, LLM-пайплайны, CRM, аналитика
│   ├── middleware/               rate_limit.py — единственный middleware вне main.py
│   ├── utils/                    file_validator.py
│   ├── templates/                34 Jinja2-шаблона (+ подпапки admin/, sales/, train/, partials/)
│   ├── static/                   CSS/JS/картинки/PDF + .txt-спеки в static/docs/
│   ├── i18n/                     en.json (RU — дефолт в коде)
│   ├── scripts/                  3 разовых скрипта бэкфилла параметров
│   ├── alembic/                  ДУБЛЬ миграций (001–017), в образ не копируется
│   ├── app/                      Артефакт: app.db + пустые static/uploads
│   ├── checklists/, checklists_trener/, uploads/, voice_assistant/  пустые каталоги-заглушки
│   ├── app.db, root_app.db       Остатки SQLite-эпохи
│   └── create_admin.py, init_prompts.py, migrate_*.py, check_prompts.py  разовые CLI-скрипты
│
├── voice_assistant/              Голосовой тренажёр (WebSocket + Azure Voice Live)
│   ├── router.py                 Старый роутер /voice-assistant (WS + REST), опциональный
│   ├── router_new.py             Новый масштабируемый роутер /voice-training
│   ├── websocket_handler.py      Ядро WS-сессии (1 451 строка)
│   ├── azure_voice_live.py       Клиент Azure Voice Live API
│   ├── session_manager.py        Реестр активных сессий + cleanup
│   ├── db_service.py             Свой доступ к БД (run_db) — параллельно app/database.py
│   ├── gpt_logic.py, stt_reactive.py, tts_response.py, vad.py  устаревший локальный STT/TTS/VAD-путь
│   ├── config.py                 Свой конфиг (дублирует env из docker-compose)
│   ├── web/                      Автономный HTML-клиент (index.html, 1 058 строк)
│   ├── utils/, temp/             Аудио-утилиты и временные файлы
│   └── 7 .md-файлов              Локальная документация модуля
│
├── ai_agent_service/             Отдельный FastAPI-сервис ИИ-агента для встреч (в compose НЕ подключён)
│   ├── main.py                   Свой FastAPI-app: /meetings/*, /agent/start, WS /ws/{meeting_id}
│   ├── services/                 llm/stt/tts/zoom/websocket-клиенты + свой pii_redactor
│   ├── pipeline/                 audio_pipeline.py
│   ├── routers/                  tts_proxy.py (дубль app/routers/tts_proxy.py)
│   └── Dockerfile, requirements.txt  собственные зависимости
│
├── alembic/versions/             Рабочие миграции 001–021
├── tests/                        10 файлов, pytest + pytest-asyncio
├── sdk-runner/                   Node-сервис для Zoom Web SDK (server.js + браузерный раннер)
├── Voice-Live-Api-main/          Вендорённый демо-клиент Azure Voice Live (serve.py, JS, PDF)
├── checklists/ (6 json)          Чеклисты анализа звонка
├── checklists_trener/ (1 json)   Чеклисты для тренерского режима
├── scripts/                      backup_postgres.sh, restore_postgres.sh
├── docs/                         Changelog, гайды, схемы workflow
├── uploads/                      Загрузки пользователей (по user_id) + avatars/, research/
├── ssl/                          Сертификаты для nginx
└── корень: bot.py, bot1.py       Telegram-боты на aiogram (aiogram НЕ в requirements.txt)
    create_admin.py, docker_create_admin.py, migrate_to_postgresql.py, migrate_trainings.py,
    check_prompts.py, check_active_prompts.py, debug_trainer_prompt.py, fix_oauth_database.py,
    test_prompt_usage.py, test_scalable_training.py  — разовые скрипты вне tests/
```

Мусор в репозитории: `backup.sql` (3.8 МБ), `server.log` (199 КБ), `test.db`, `cookies.txt`, `app/app.db`, `.env` / `.env.rf` / `.env.kz.bak` (закоммичены), `Claude_GROWUP/` и `claude growup/` (две папки, отличаются регистром/пробелом).

---

## 4. ТОЧКИ ВХОДА

### 4.1 HTTP/WS — основное приложение (`app.main:app`)

Публичные / аутентификация — `app/routers/public.py`, `app/routers/auth.py`:
```
GET  /health   GET /ready   GET /   GET /career
GET|POST /login   GET|POST /register   POST /register/verify   POST /register/resend   POST /logout
GET  /auth/google   GET /auth/google/callback
GET|POST /forgot-password   GET|POST /reset-password
```

Чат и анализ звонков — `app/routers/chat.py`, `app/routers/chat_trener.py` (два почти идентичных набора):
```
GET  /chat            GET /chat/poll            POST /chat/send
GET  /attachments/{attachment_id}
GET  /chat/export/by-report/{report_message_id}
POST /chat/reset
GET  /chat_trener     GET /chat_trener/poll     POST /chat_trener/send
GET  /attachments_trener/{attachment_id}
GET  /chat_trener/export/by-report/{report_message_id}
POST /chat_trener/reset
```

Дашборд, настройки, команды:
```
GET  /dashboard   GET /calls   POST /test/create-training
GET  /settings   POST /settings/profile   POST /settings/password   POST /settings/avatar
GET  /teams   GET /teams/my   POST /teams
GET  /teams/{team_id}/members   POST /teams/{team_id}/invitations
GET|POST /teams/{team_id}/script
```

Аналитика — `app/routers/analytics.py` (18 маршрутов):
```
GET  /analytics   POST /analytics/send   GET /analytics/poll   POST /analytics/clear
POST /analytics/query
GET  /analytics/api/{summary|trend|comparison|boolean-stats|metrics-list|team-members|buttons}
GET  /analytics/api/crm/{revenue|avg-check|win-rate|conversion|cycle|revenue-by-manager}
```

Аналитика команды и отчёты:
```
GET  /teams/{team_id}/analytics
GET  /teams/{team_id}/member/{member_id}/report
GET  /api/teams/{team_id}/conversion-metrics
GET  /api/teams/{team_id}/errors-corrections
POST /api/teams/{team_id}/errors/{error_id}/mark-applied
GET  /teams/{team_id}/member/{member_id}/plan/{plan_id}/stats
GET  /teams/{team_id}/train-report
GET  /teams/{team_id}/member/{member_id}/train-report
```

CRM — `app/routers/crm_integration.py` (21 маршрут):
```
GET  /crm
POST /crm/connect/{crm_type}   POST /crm/connect/bitrix24/webhook   GET /crm/oauth/callback
POST /crm/sync/{integration_id}   GET /crm/sync-status/{integration_id}
POST /crm/sync-chats/{integration_id}   POST /crm/sync-all/{integration_id}
GET  /crm/recordings   GET /crm/recording/{recording_id}/details
POST /crm/recordings/{recording_id}/analyze   POST /crm/recordings/batch-analyze
GET  /crm/batch-progress/{batch_id}
POST /crm/integrations/{integration_id}/enable-webhook
POST /crm/integrations/{integration_id}/disconnect
DELETE /crm/integrations/{integration_id}
GET  /crm/integrations/{integration_id}/managers
GET|POST /crm/integrations/{integration_id}/manager-mapping
GET  /crm/debug/{integration_id}
```

Тренировки:
```
GET  /training-plan/by-plan/{plan_id}   GET /training-plan/{report_msg_id}
POST /training/{training_id}/start      POST /training-session/{session_id}/complete
GET  /api/training/{training_id}
GET  /trainings/catalog   POST /trainings/catalog/start
+ 3 маршрута программ тренировок в app/routers/training_program.py:25,57,117
```

Встречи (Zoom / WebRTC):
```
POST /api/zoom/meetings/create   GET /api/zoom/meetings   GET /api/zoom/meetings/{id}
POST /api/zoom/meetings/{id}/{start|join|end|start-agent|stop-agent}
GET  /api/zoom/meetings/{id}/{transcript|agent-status}   DELETE /api/zoom/meetings/{id}
POST /api/zoom/sdk-signature
POST /api/webrtc/meetings/create   GET /api/webrtc/meetings   GET /api/webrtc/meetings/{id}
POST /api/webrtc/meetings/{id}/{join|start-ai-agent|end}   GET /api/webrtc/meetings/{id}/info
DELETE /api/webrtc/meetings/{id}
GET  /webrtc/meetings/{meeting_id}/room          ← отдельный html_router
```

Админ / продажи / владелец:
```
GET  /admin   GET /admin/users   POST /admin/users/{id}/set-role   POST /admin/users/{id}/delete
GET  /admin/prompts (+ /create, /trainer, /{name}/versions, /{name}/edit,
     /{id}/activate, /{id}/delete)
GET  /admin/research   GET /admin/research/{id}   GET /admin/research/{id}/download
POST /admin/research/{id}/delete
GET  /sales/   POST /sales/toggle-premium/{id}   POST /sales/reset-analyses/{id}
POST /sales/assign-product/{id}
GET  /owner   GET /owner/{team_id}   GET /api/owner/{team_id}/data
```

Служебные API:
```
GET  /api/notifications/{unread|all|count}   POST /api/notifications/{id}/read
POST /api/notifications/read-all   DELETE /api/notifications/{id}   DELETE /api/notifications/clear-all
GET  /api/progress/{operation_id}   GET /api/progress/active/list   POST /api/progress/{id}/cancel
GET  /api/performance/{cache/stats|database/stats|storage/stats|system/info|overview}
POST /api/performance/{cache/clear|database/optimize|database/cleanup|storage/cleanup|storage/clear-cache}
POST /api/tts-proxy
```

### 4.2 WebSocket
- `WS /voice-training/ws` — `voice_assistant/router_new.py`, основной путь голосовых тренировок
- `WS /voice-assistant/ws` — `voice_assistant/router.py`, легаси-путь
- `WS /api/webrtc/meetings/{meeting_id}/join` — `app/routers/webrtc_meetings.py`
- `WS /ws/{meeting_id}` — `ai_agent_service/main.py:492`, отдельный сервис

REST голосового модуля: `GET /voice-training/{stats|session/{id}|training|training/{id}/history}`, `POST /voice-training/{session/{id}/end|training/complete}`; легаси `GET /voice-assistant/{,health,training,scenarios,training/{id},training/{id}/stats}`, `POST /voice-assistant/training/{create,complete}`, `DELETE /voice-assistant/training/{id}`.

### 4.3 Вебхуки
- `POST /crm/webhook/{integration_id}/{webhook_secret}` — единственный внешний вебхук. Явно исключён из CSRF-проверки (`app/main.py:180`) и маскируется в логах (`app/main.py:311-317`).
- `GET /crm/oauth/callback` — колбэк OAuth от amoCRM/Bitrix24.
- `GET /auth/google/callback` — колбэк Google OAuth.

### 4.4 Воркеры и слушатели очередей
`app/worker.py`, запуск `arq worker.WorkerSettings`. Две джоб-функции:
- `analyze_recording_job(recording_id, owner_id, attempt)` — анализ CRM-записи
- `run_pipeline_job(kind, user_id, conversation_id, ref, ...)` — 8 видов анализа из чата: `audio`, `text`, `raw_text`, `images` и их `*_trener`-варианты

Настройки: `max_jobs=8`, `job_timeout=1800`, **`max_tries=1`** (авто-ретрай выключен намеренно — пайплайны неидемпотентны), `keep_result=3600`. Обвязка `_run_with_controls`: per-user fairness-слот → Redis-лок идемпотентности → выполнение.

### 4.5 Периодические задачи
Cron/планировщика нет. Есть:
- `_session_cleanup_loop` — `asyncio`-цикл раз в 300 с внутри каждого веб-процесса (`app/main.py:77-91`), закрывает WS-сессии старше 3600 с. **Запускается в каждом из 4 uvicorn-воркеров.**
- Переобработка зависших CRM-записей — не по расписанию, а по таймауту при обращении (`app/routers/crm_integration.py:612`).

### 4.6 CLI и вспомогательные процессы
Скрипты в корне и в `app/` (запуск вручную, `python <file>`): `create_admin.py`, `docker_create_admin.py`, `app/create_postgres_admin.py`, `app/init_prompts.py`, `app/init_trainer_prompt.py`, `migrate_to_postgresql.py` (и его дубль `app/migrate_to_postgresql.py`), `migrate_trainings.py` (+ дубль в `app/`), `check_prompts.py`, `check_active_prompts.py`, `debug_trainer_prompt.py`, `fix_oauth_database.py`, `test_prompt_usage.py`, `test_scalable_training.py`, `app/scripts/{add_analytics_parameters,backfill_dialogue_metrics,fill_test_params}.py`. Shell: `scripts/backup_postgres.sh`, `scripts/restore_postgres.sh`, `setup_production_oauth.sh`.

Отдельные процессы вне compose: `bot.py` и `bot1.py` (Telegram-боты на `aiogram` — библиотеки нет в `requirements.txt`, предположительно нерабочие/заброшенные), `ai_agent_service/main.py` (свой uvicorn, свой Dockerfile, в compose не подключён), `sdk-runner/src/server.js` (Node, Zoom SDK), `Voice-Live-Api-main/serve.py` (демо-сервер вендорённого клиента).

---

## 5. МОДЕЛЬ ДАННЫХ

Все 44 модели — в одном файле `app/models.py` (1 144 строки).

### Пользователи и организации
| Модель | Таблица | Назначение |
|---|---|---|
| `User` | `users` | Аккаунт: роль, премиум, привязка к организации |
| `Organization` | `organizations` | Клиент; SKU (`FULL`/`TRAIN_RU`/`TRAIN_GLOBAL`) → набор capabilities |
| `PasswordResetToken` | `password_reset_tokens` | Токены сброса пароля |

### Чат и анализ звонков
| Модель | Таблица | Назначение |
|---|---|---|
| `Conversation` | `conversations` | Диалог = одна единица анализа звонка |
| `Message` | `messages` | Сообщение в диалоге (`user_id=None` → бот) |
| `Attachment` | `attachments` | Файл при сообщении (аудио/изображение/документ) |
| `Prompt` | `prompts` | Версионируемые системные промпты с флагом активности |
| `ResearchLog` | `research_logs` | Лог «исследования» по диалогу для админ-разбора |

### Тренировки
| Модель | Таблица | Назначение |
|---|---|---|
| `AnalysisTrainingPlan` | `analysis_training_plans` | План тренировок, сгенерированный из отчёта по звонку |
| `Training` | `trainings` | Отдельная тренировка (этап) внутри плана |
| `TrainingSession` | `training_sessions` | Одна попытка прохождения тренировки |
| `VoiceTrainingMessage` | `voice_training_messages` | Реплики внутри голосовой сессии |
| `TrainingProgram` | `training_programs` | Программа тренировок команды (train-режим), цикл повторения |
| `TrainingProgramDay` | `training_program_days` | День программы: `stage_key` **или** `training_id` |
| `TrainingConversionMetric` | `training_conversion_metrics` | Метрики конверсии между этапами тренировок |
| `TrainingErrorCorrection` | `training_errors_corrections` | Ошибки/коррекции, извлечённые из анализа звонка |

### Команды
| Модель | Таблица | Назначение |
|---|---|---|
| `Team` | `teams` | Команда с менеджером (РОПом) |
| `TeamMember` | `team_members` | Участник команды |
| `TeamInvitation` | `team_invitations` | Приглашение по токену |
| `TeamScript` | `team_scripts` | Скрипт/чеклист команды (1:1 с `Team`) |

### CRM
| Модель | Таблица | Назначение |
|---|---|---|
| `CRMIntegration` | `crm_integrations` | Подключение пользователя к amoCRM/Bitrix24 (токены, webhook-секрет) |
| `CRMRecording` | `crm_recordings` | Запись звонка из CRM — центральный узел, связывает CRM-сущности с анализом |
| `CRMDeal` / `CRMLead` / `CRMContact` / `CRMCompany` | `crm_deals` / `crm_leads` / `crm_contacts` / `crm_companies` | Зеркала сущностей CRM |
| `CRMDealProduct` | `crm_deal_products` | Товары в сделке |
| `CRMActivity` | `crm_activities` | Активности: звонки, письма, встречи, задачи |
| `CRMManagerMapping` | `crm_manager_mappings` | Привязка менеджера CRM → аккаунт UpStat |

### Метрики и оценка качества
| Модель | Таблица | Назначение |
|---|---|---|
| `ParameterDefinition` | `parameter_definitions` | Справочник параметров анализа (dictionary-driven, ~150 параметров) |
| `ParameterValue` | `parameter_values` | Значение параметра для конкретного диалога |
| `ChecklistItemDefinition` | `checklist_item_definitions` | Справочник пунктов чеклиста с весами |
| `ChecklistItemScore` | `checklist_item_scores` | Оценка ± по пункту для конкретного звонка |
| `WinProbabilityScore` | `win_probability_scores` | Итоговая вероятность закрытия сделки (1:1 с `Conversation`) |
| `SellerPassport` | `seller_passports` | Профиль навыков менеджера (1:1 с `User`) |
| `PassportSnapshot` | `passport_snapshots` | Снимок навыков после конкретного звонка |
| `ManagerAction` | `manager_actions` | Успешное/неуспешное действие, извлечённое из звонка |
| `ActionPattern` | `action_patterns` | Паттерн действия, подтверждённый статистикой по команде |

### Встречи
| Модель | Таблица | Назначение |
|---|---|---|
| `ZoomMeeting` | `zoom_meetings` | Встреча Zoom |
| `MeetingTranscript` | `meeting_transcripts` | Транскрипт Zoom-встречи (1:1) |
| `CustomMeeting` | `custom_meetings` | WebRTC-встреча |
| `MeetingParticipant` | `meeting_participants` | Участник WebRTC-встречи |
| `CustomMeetingTranscript` | `custom_meeting_transcripts` | Транскрипт WebRTC-встречи (1:1) |

### Прочее
| Модель | Таблица | Назначение |
|---|---|---|
| `Notification` | `notifications` | Уведомления пользователя (cascade delete от `User`) |
| `AnalyticsMessage` | `analytics_messages` | Сообщения чата аналитики — отдельная таблица от `messages` |

### Ключевые связи

**Центральные узлы — `User` и `Conversation`.**

- `User` — прямые FK из 20+ таблиц. Самоссылка: `premium_granted_by → users.id`. FK `organization_id → organizations.id`.
- `Organization` → `Team.organization_id`, `User.organization_id` — источник capabilities.
- Цепочка чата: `User → Conversation → Message → Attachment`, каскадное удаление на каждом уровне.
- Цепочка тренировок: `Message (отчёт) → AnalysisTrainingPlan → Training → TrainingSession → VoiceTrainingMessage`, каскад по всей цепочке.
- Цепочка CRM: `User → CRMIntegration → CRMRecording`. `CRMRecording` имеет 7 FK: `integration_id`, `user_id`, `conversation_id`, `training_plan_id`, `deal_id`, `lead_id`, `contact_crm_id` — это точка склейки CRM-мира и мира анализа.
- `Conversation` ↔ `CRMRecording` — 1:1 в обе стороны (`uselist=False`), объявлено **вне классов**, монки-патчем после определения (`app/models.py:418-422`).
- Метрики цепляются к `Conversation`, а не к `CRMRecording`: `ParameterValue`, `ChecklistItemScore`, `WinProbabilityScore` (unique), `PassportSnapshot`, `ManagerAction` — все через `conversation_id`.
- `WinProbabilityScore` дублирует связи: одновременно `conversation_id` (unique), `crm_recording_id`, `deal_id`, `lead_id`.
- Команды: `Team.manager_id → users.id` (владелец), `TeamMember` — many-to-many `User ↔ Team`. Тимовый контекст протянут опциональным `team_id` в `TrainingConversionMetric`, `TrainingErrorCorrection`, `ManagerAction`; обязательным — в `ActionPattern`, `TrainingProgram`.
- CRM-сущности (`CRMDeal`, `CRMLead`, `CRMContact`, `CRMCompany`, `CRMActivity`) все висят на `integration_id` с `ondelete="CASCADE"`; между собой связаны только через поля-«зеркала» CRM-ID, без FK — кроме `CRMDealProduct → CRMDeal`.

**Заметки для рефакторинга:**
- `ondelete="CASCADE"` на уровне БД задан только у части таблиц (`PassportSnapshot`, `CRMManagerMapping`, все `CRM*`-зеркала); у остальных каскад только ORM-уровня (`cascade="all, delete-orphan"`) — при удалении сырым SQL целостность не гарантируется.
- Все `datetime`-поля — `DateTime` без таймзоны, дефолт `datetime.utcnow` (устаревший вызов), при этом есть отдельный `app/time_utils.py` с локальными датами.
- JSON хранится в `Text`-колонках как строки (`capabilities_override`, `capabilities` и т. п.), а не в `JSON`/`JSONB`.

---

## 6. ВНЕШНИЕ ИНТЕГРАЦИИ

### OpenAI (LLM + Whisper STT)
- **Откуда:** `app/services/pipeline.py:53` (клиент), `app/services/pipeline_trener.py`, `app/services/image_pipeline.py`, `app/services/research_service.py`, `app/services/analytics_assistant.py`, `app/services/parameter_extraction.py`, `app/services/manager_actions_service.py`, `voice_assistant/gpt_logic.py`.
- **Таймауты/ретраи:** только в `pipeline.py` — `OpenAI(timeout=LLM_TIMEOUT_SECONDS, max_retries=LLM_MAX_RETRIES)`, плюс собственная обёртка `_llm_create(..., max_refusal_retries=2)` (`app/services/pipeline.py:100`), которая ретраит **отказы модели**, а не сетевые ошибки (детектор `_looks_like_refusal`, `pipeline.py:89`).
- **Остальные вызывающие модули** создают `OpenAI(...)` без явных `timeout`/`max_retries` — работают на дефолтах SDK (600 с, 2 ретрая).
- **Ошибки:** ловятся широким `except Exception` с логированием и записью «ошибка анализа» в чат; отдельной классификации rate-limit/quota нет.

### Azure Voice Live API (голосовые тренировки)
- **Откуда:** `voice_assistant/azure_voice_live.py` (WS-клиент), `voice_assistant/websocket_handler.py` (оркестрация сессии).
- **Таймауты:** `ping_timeout=120`, `close_timeout=30` (`azure_voice_live.py:178-179`), `asyncio.wait_for(ws.recv(), timeout=30.0)` (`:217`), `_SESSION_CONFIG_TIMEOUT_S` на ожидание `session.updated` (`websocket_handler.py:114`).
- **Ретраи:** нет переподключения к Azure. Вместо этого — **фолбэк голоса**: если основной (preview) голос не синтезирует, сессия переключается на `AZURE_VOICE_LIVE_VOICE_FALLBACK` (GA-голос). Есть логика реконнекта клиента (`_reconnect_or_create`, `websocket_handler.py:333`).
- **Ошибки:** обрабатываются, но размазаны по 378-строчному `receive_from_azure` с вложенностью 19 — см. п. 8.

### ElevenLabs (STT + TTS)
- **Откуда:** транскрибация — `app/services/pipeline.py:193` и её копия `app/services/pipeline_trener.py:81`; синтез — `voice_assistant/tts_response.py:306`, `ai_agent_service/services/tts_service.py`, прокси `app/routers/tts_proxy.py`.
- **Таймауты:** `STT_TIMEOUT_SECONDS` в `pipeline.py`; захардкоженный `httpx.Timeout(300.0)` в `pipeline_trener.py:91`; `timeout=30.0` в tts_proxy. В `voice_assistant/stt_reactive.py:265` — **синхронный `requests.post` без таймаута** (блокирует event loop, если вызывается из async).
- **Ретраи:** нет.
- **Ошибки:** есть фолбэк STT ElevenLabs → OpenAI Whisper; `httpx.RequestError` перехватывается в tts_proxy.

### amoCRM
- **Откуда:** `AmoCRMService` — `app/services/crm_service.py:92-494`.
- **Таймауты:** частично. Есть `timeout=300.0` на скачивание записи (`:477`), но `httpx.AsyncClient()` **без таймаута** на обновление токена (`:123`), обмен кода (`:147`), API-запросы (`:175`) — это дефолтные 5 с httpx на connect, но без ограничения на read.
- **Ретраи:** для amoCRM ретраев нет (в отличие от Bitrix24).
- **Ошибки:** `except httpx.HTTPStatusError` с логированием, возврат `None`/пустого списка вверх.

### Bitrix24 — **две независимые реализации**
- `Bitrix24WebhookService` (`crm_service.py:495-1285`) — по входящему вебхук-URL.
- `Bitrix24Service` (`crm_service.py:1286-2358`) — по OAuth.
- **Ретраи:** есть в обеих, но кодом-близнецом: `_max_retries = 3`, экспоненциальный бэкофф `2 ** (attempt+1)` на 503 и rate-limit (`:513-560` и `:1361-1420`). Скачивание записи — отдельный цикл `for attempt in range(2)` с детектом «пришёл HTML вместо аудио» (`:1689-1762`).
- **Таймауты:** `timeout=60.0` на API-запросы, `timeout=300.0` на скачивание.
- **Троттлинг:** `self._request_delay` перед каждым запросом.
- **Есть фолбэк** на Voximplant-скачивание (`_try_voximplant_download`).

### Zoom
- **Откуда:** `app/services/zoom_service.py` (OAuth server-to-server, meetings API), `app/routers/zoom_meetings.py` (4 прямых вызова `httpx` из роутера — логика в HTTP-слое), `sdk-runner/` (Node-раннер Web SDK), `ai_agent_service/services/zoom_client.py`.
- **Таймауты:** есть везде — `timeout=30.0`, в одном месте `10.0` (`zoom_meetings.py:588`).
- **Ретраи:** нет. Есть кеширование access-токена с `token_expires_at`.
- **Ошибки:** проверка `if response.status_code == 200` вместо `raise_for_status()`; при неуспехе — лог + `None`.

### Google OAuth
- **Откуда:** `app/services/google_oauth.py`, вызывается из `app/routers/auth.py`.
- **Таймауты: НЕТ.** Синхронные `requests.post` / `requests.get` без `timeout` (`:54`, `:70`) внутри async-эндпоинта — блокировка event loop на неопределённое время при зависании Google.
- **Ретраи:** нет.
- **Ошибки:** `if status != 200 → raise HTTPException(400, detail=response.text)` — **текст ответа Google утекает пользователю**.

### SMTP (почта)
- **Откуда:** `app/services/email.py` — 3 функции: приглашение в команду, код верификации, сброс пароля.
- **Таймауты: НЕТ.** `smtplib.SMTP(host, port)` без `timeout=` (`:118`, `:180`, `:288`).
- **Ретраи:** нет. Отправка синхронная, в контексте HTTP-запроса.
- **Ошибки:** `except Exception` → лог, функция возвращает управление; пользователь не узнаёт, что письмо не ушло.

### Redis
- **Откуда:** `app/services/queue.py` (arq-пул), `app/services/job_control.py` (локи + fairness), `app/services/caching_service.py`, `app/services/progress_tracker.py`, `app/routers/public.py` (`/ready`).
- **Таймауты:** `socket_connect_timeout=2` только в `progress_tracker.py:208` и `public.py:34`. У arq-пула — дефолты.
- **Ретраи:** на уровне arq — **выключены** (`max_tries=1`, `app/worker.py:158`), потому что пайплайны неидемпотентны.
- **Ошибки:** `except Exception: pass` при закрытии пула (`queue.py:41-44`).

### Sentry
- Инициализируется в `app/logging_config.py`. Ошибок не обрабатывает — это сам обработчик.

### Входящий вебхук CRM
`app/routers/crm_integration.py:744-786`:
- Проверка секрета через `secrets.compare_digest` — корректно.
- Проверка capability владельца интеграции.
- Парсинг тела: `except Exception: pass` → при битом JSON `body = {}`, событие уходит в ветку `else` и всё равно триггерит полный синк.
- Работа делегируется в `BackgroundTasks` **веб-процесса**, не в arq: `_delayed_sync` (sleep 15 с) и `_delayed_entity_sync` (sleep 5 с). То есть тяжёлый синк идёт мимо очереди, вне fairness/локов, и теряется при рестарте контейнера.
- Дедупликации входящих событий нет — CRM, приславшая событие дважды, запустит два синка.

### Сводная таблица

| Сервис | Таймаут | Ретраи | Обработка ошибок |
|---|---|---|---|
| OpenAI | частично (только `pipeline.py`) | ретрай на отказ модели, ×2 | broad `except`, сообщение в чат |
| Azure Voice Live | да | нет реконнекта; фолбэк голоса | есть, но размазана |
| ElevenLabs | частично; в `stt_reactive.py` нет | нет | фолбэк на Whisper |
| amoCRM | частично | **нет** | `HTTPStatusError` → `None` |
| Bitrix24 (×2 класса) | да (60/300 с) | да, бэкофф ×3 | есть, дублируется |
| Zoom | да (30 с) | нет | проверка кода ответа → `None` |
| Google OAuth | **нет** | нет | текст ответа Google утекает клиенту |
| SMTP | **нет** | нет | «тихая» ошибка |
| Redis/arq | почти нет | выключены намеренно | `except: pass` |

---

## 7. ТЕСТЫ И КАЧЕСТВО

### Тесты
**Есть, 129 тестов в 8 файлах (`tests/`, 1 689 строк).** Все — unit-тесты на моках, интеграционных и E2E нет, `TestClient`/фикстуры БД не используются.

| Файл | Тестов | Что покрывает |
|---|---:|---|
| `tests/test_capability_service.py` | 33 | SKU → capabilities, `has_capability` |
| `tests/test_training_validator.py` | 28 | Валидация завершения тренировки |
| `tests/test_i18n_service.py` | 20 | Резолв локали, переводы |
| `tests/test_session_manager.py` | 15 | Реестр WS-сессий, cleanup |
| `tests/test_voice_payload.py` | 12 | Сборка `session.voice` для Azure (гейт temperature по типу голоса) |
| `tests/test_voice_db_service.py` | 10 | Voice-слой БД, фоновые сохранения |
| `tests/test_prompt_locale_gate.py` | 9 | Locale-gate: локализация не меняет поведение ИИ |
| `tests/test_run_db_concurrency.py` | 2 | `run_db` — своя Session на операцию |

**Не покрыто ничем:** все 26 роутеров (0 тестов на HTTP-слой), `pipeline.py` / `pipeline_trener.py` / `image_pipeline.py` (ядро продукта), `crm_service.py` (2 387 строк, самый крупный Python-файл), `worker.py` и джобы, `models.py`, аналитика, `security.py`, миграции.

Тесты покрывают в основном свежий пласт (capabilities, i18n, голос) — это следы недавних задач, а не системное покрытие. Вне `tests/` лежат `test_prompt_usage.py` и `test_scalable_training.py` — скрипты, требующие живого окружения, pytest их не подхватывает (`testpaths = ["tests"]`).

### Линтеры и типизация
- **Ruff 0.4.4** — настроен в `pyproject.toml`: `line-length=100`, набор `E,W,F,I,B`. Отключены `E501`, `E402`, `B008`, `B904`, **`F841`** (неиспользуемые переменные).
- **Типизация:** mypy сконфигурирован в `pyproject.toml`, но `strict = false`, `ignore_missing_imports = true`, и **в CI не запускается вообще**. Аннотации есть в моделях (`Mapped[...]`) и сигнатурах сервисов, но не сплошные; `schemas.py` — всего 5 КБ на 44 модели, т.е. большинство эндпоинтов принимают/возвращают нетипизированные `dict`.
- **Форматтера нет** — ни black, ни `ruff format` в CI/pre-commit. Отсюда разнобой в отступах и висящие пробелы (см. п. 9).
- **Bandit** — настроен, `skips = ["B101", "B311"]`.

### CI
`.github/workflows/ci.yml`, на push в `main`/`develop` и PR в `main`. Два джоба:
1. **Lint & Security:** `ruff check app/ voice_assistant/`, `bandit -ll`, `detect-secrets`.
2. **Unit Tests:** `pytest tests/ -v`.

Проблемы CI:
- Линтится только `app/` и `voice_assistant/` — `ai_agent_service/`, `tests/`, `sdk-runner/` и скрипты в корне вне проверки.
- Флаги ruff в CI (`--select E,F,W,I --ignore E501,E402`) **не совпадают** с `pyproject.toml` (там ещё `B`, и игнорируются `B008/B904/F841`) — локальный и CI-прогон дают разный результат.
- `pytest`/`pytest-asyncio`/`pytest-mock` ставятся отдельной строкой в CI, но **отсутствуют в `requirements.txt`** — локально тесты из чистого окружения не запустятся.
- В CI задан `DATABASE_URL: "sqlite:///./ci_test.db"`, тогда как `app/database.py:10-11` на любой sqlite-URL кидает `ValueError`. Работает только потому, что ни один тест не импортирует `database`.
- Нет шага сборки Docker-образа и нет проверки, что миграции применяются (`alembic upgrade head`).
- Деплоя в CI нет — выкладка ручная.

### Pre-commit
`.pre-commit-config.yaml`: ruff (`--fix`), detect-secrets, trailing-whitespace, end-of-file-fixer, check-yaml, check-added-large-files (500 КБ). При этом в репозитории лежат `backup.sql` (3.8 МБ) и `server.log` (199 КБ) — хук либо не установлен локально, либо файлы добавлены до него.

### Прочие сигналы качества
- `.env`, `.env.rf`, `.env.kz.bak` закоммичены в репозиторий.
- 124 вызова `print()` в production-коде `app/` и `voice_assistant/` при настроенном структурированном логировании.
- 8 «голых» `except:` и 309 `except Exception` в `app/`.

---

## 8. ГДЕ СЛОЖНЕЕ ВСЕГО

### 1. `voice_assistant/websocket_handler.py:233-782` — `handle_websocket_connection`
**550 строк, вложенность 11.** Одна функция держит весь жизненный цикл голосовой тренировки: приём WS-клиента, реконнект или создание сессии в БД, загрузка локали и данных тренировки, конфигурирование Azure-сессии с ожиданием `session.updated`, запуск двух конкурентных задач приёма/отправки, финализация с подсчётом длительности и сообщений. Внутри — вложенные функции-замыкания (`_reconnect_or_create`, `_load_setup`, `_finalize`), передаваемые в `run_db`. Состояние (`user_session`, `azure_connection`, флаги конфигурации) живёт в локальных переменных и мутируется из разных веток. Точка, где сходятся Azure, БД, менеджер сессий и WS-протокол.

### 2. `voice_assistant/websocket_handler.py:785-1162` — `receive_from_azure`
**378 строк, вложенность 19** — самая глубокая вложенность в проекте. Цикл `while True` → `try` → разбор типа события Azure → вложенные `if` по подтипам → внутри `try` на каждую ветку → работа с БД → отправка клиенту. Девятнадцать уровней означает, что часть веток физически недостижима для чтения без разворачивания. Здесь же живёт логика фолбэка голоса и «немого синтеза» — то, что чинили последними пятью коммитами ветки.

### 3. `app/routers/chat.py:187-607` — `send_message`
**421 строка, вложенность 7 в HTTP-эндпоинте.** Один обработчик делает: валидацию загруженного файла, определение типа (аудио / текст / изображения / сырой текст), запись `Attachment`, ветвление на 4+ типа пайплайна, выбор между arq-очередью и `BackgroundTasks` по `USE_QUEUE`, обработку лимитов пользователя, формирование ответа. Бизнес-логика живёт в роутере, а не в сервисе. Полный близнец — `app/routers/chat_trener.py:155-371` (217 строк): нормализованный diff между файлами — 714 строк из 1 206, т.е. расходятся они больше чем наполовину, хотя решают одну задачу.

### 4. `app/services/crm_service.py` — 2 387 строк, две почти одинаковые реализации Bitrix24
`Bitrix24WebhookService` (`:495-1285`) и `Bitrix24Service` (`:1286-2358`) — параллельные классы, различающиеся только способом аутентификации. Дублируются попарно: `_make_api_request` (обе с одинаковым бэкоффом), `get_recordings`, `_get_owner_info`, `_get_user_info`, `get_chats`, `_get_chat_messages`, `download_recording`. Внутри — `_bitrix24_get_recordings` на **246 строк с вложенностью 10** (`:591`) и `download_recording` с вложенностью 10 (`:1683`). 12 циклов `while True` по файлу. Любое исправление логики Bitrix нужно делать дважды.

### 5. `app/services/pipeline.py` (1 278) + `app/services/pipeline_trener.py` (828) — копипаста ядра продукта
**8 приватных функций скопированы байт-в-байт или почти:** `_ffmpeg_wav`, `_elevenlabs_transcribe`, `_openai_whisper_transcribe`, `_words_to_turns`, `_text_to_single_speaker_turns`, `_attach_file`, `_read_text_file`. Публичные функции идут парами: `run_pipeline` (203 строки) / `run_pipeline_trener` (226), `run_pipeline_from_text` (116) / `run_pipeline_from_text_trener` (193), `run_pipeline_from_raw_text` (100) / `run_pipeline_from_raw_text_trener` (164). При этом trener-версии уже разошлись: у них захардкожен `httpx.Timeout(300.0)` вместо `STT_TIMEOUT_SECONDS`, нет `_llm_create` с ретраем отказов, нет `_resolve_roles`, есть свой `safe_format_prompt`. Рядом — `app/services/pipeline_enhanced.py` (96 строк, третий вариант), **который никем не импортируется** — мёртвый код.

### 6. `app/models.py` — 1 144 строки, 44 модели, fan-in 54
Импортируется из 54 мест — самый связывающий модуль проекта (следом `app/database.py` — 42, `app/deps.py` — 19). Любое изменение модели трогает половину кодовой базы. Плюс два отношения объявлены **вне классов**, монки-патчем после определения: `User.crm_integrations`, `User.crm_recordings`, `Conversation.crm_recording` (`:418-422`) — они невидимы при чтении самих классов. `CRMRecording` — узел с 7 внешними ключами, склеивающий CRM-мир и мир анализа.

### 7. `app/routers/crm_integration.py` — 1 537 строк, 21 маршрут, смесь слоёв
В одном файле: OAuth-колбэк, приём вебхука, ручной и батч-запуск анализа, `_process_single_recording` (бизнес-логика внутри роутера), `analyze_recording_task` (вызывается **из worker'а** — `app/worker.py:76`, т.е. воркер импортирует роутер), `_delayed_sync` / `_delayed_entity_sync` (фоновая работа), логика «зависших» записей по таймауту (`:612`), `_STAGE_LABELS` для прогресса, сырой SQL через `text()`. Это и HTTP-слой, и сервис, и воркер-хендлер одновременно.

### 8. `app/main.py:110-329` — `create_app` на 219 строк
Фабрика приложения содержит **три класса middleware, объявленных прямо в теле функции** (`_SecurityHeadersMiddleware`, `_CSRFOriginMiddleware` — `:159-197`), настройку i18n с `try/except ImportError` фолбэком на другой путь импорта, ручное дописывание `sys.path` для `voice_assistant` (`:277-292`), подключение 28 роутеров, часть — внутри `try/except`, где падение импорта логируется и приложение стартует без роутера. Порядок `add_middleware` критичен и задокументирован комментарием, но структурно ничем не защищён.

---

## 9. НЕОДНОРОДНОСТЬ

### 9.1 Два способа ходить в БД (фактически три)
- **Depends(get_db)** — 161 использование в роутерах, канонический путь.
- **`SessionLocal()` напрямую** — 19 файлов, включая production-код: `app/services/pipeline.py`, `app/services/crm_service.py`, `app/services/notification_service.py`, `app/services/research_service.py`, `app/services/image_pipeline.py`, `app/routers/crm_integration.py`, `voice_assistant/router_new.py`. Закрытие сессии — вручную в `finally`, где-то забыто.
- **`run_db(fn, ...)`** — собственный слой в `voice_assistant/db_service.py:44`, открывающий короткоживущую Session на каждую операцию. Появился специально под голосовые WS-сессии; за пределы `voice_assistant/` не распространён.

Плюс поверх ORM — сырой SQL через `text()` в 20+ файлах, в том числе прямо в роутерах (`analytics.py`, `chat.py`, `training_program.py`, `crm_integration.py`).

### 9.2 Две системы импортов, склеенные через `sys.path`
Ни одного `from app.services...` на верхнем уровне — везде «голые» `from services.x`, `from models` (182 вхождения), работающие только благодаря `PYTHONPATH=/app:/app/app:/app/voice_assistant` в `Dockerfile:37` и ручной правке `sys.path` в `app/main.py:277-292` и `tests/conftest.py`. Как страховка — **19 файлов с `try: from services.x / except ImportError: from app.services.x`**. Один и тот же модуль может быть загружен дважды под разными именами (`services.capability_service` и `app.services.capability_service`) — с двумя копиями модульного состояния.

### 9.3 Две системы обработки ошибок
- **Централизованная:** `app/services/error_handler.py` — `ErrorCategory`, иерархия `CustomError` → `FileProcessingError`/`ValidationError`, декоратор `retry_on_error` (`:225`). **Используется в 4 файлах:** `chat.py`, `chat_trener.py`, `file_validator.py`, `pipeline.py`.
- **Ad-hoc:** 309 `except Exception` + 8 голых `except:` + 240 `raise HTTPException` по остальным ~180 файлам. Три разных исхода на одну ситуацию: `except: pass` (`queue.py:41`), `except → log → return None` (CRM/Zoom), `except → raise HTTPException` (auth).
- Декоратор `retry_on_error` существует, но CRM-сервис вместо него пишет **свои** циклы бэкоффа — дважды.

### 9.4 Два способа рендерить шаблоны
- **Канон:** `request.app.state.templates` — 19 роутеров, инстанс создаётся один раз в `app/main.py:219`.
- **Свой инстанс:** `app/routers/webrtc_meetings.py:29` — `Jinja2Templates(directory="templates")` с **относительным путём** (в контейнере cwd=`/app`, шаблоны в `/app/app/templates` — предположительно `/webrtc/meetings/{id}/room` отдаёт 500).
- **Локальный инстанс на вызов:** `voice_assistant/router.py:153`, `voice_assistant/router_new.py:383` — создаётся внутри функции при каждом запросе.
- Только `app.state.templates` знает про i18n-глобалы (`_`, `gettext`, `localdate`, `get_brand`) — остальные три их не имеют, т.е. в этих шаблонах перевод и бренд не работают.

### 9.5 Четыре формата ответа эндпоинта
81 `TemplateResponse`, 64 `JSONResponse`, 34 «голых» `return {...}`. При этом `response_model=` использован **10 раз на 200+ маршрутов**, а `BaseModel` встречается в роутерах 7 раз. `app/schemas.py` — 5 КБ на 44 модели: контракты API де-факто не описаны, ответ формируется словарём по месту.

### 9.6 Разнобой в конфигурации
- **Нет `app/config.py`.** 145 вызовов `os.getenv` разбросаны по 40 файлам, часто на уровне модуля (значение фиксируется на импорте).
- При этом у `voice_assistant/config.py` и `ai_agent_service/config.py` — свои конфиг-модули, каждый со своей конвенцией.
- Дефолты задублированы между кодом и `docker-compose.yml`, причём compose всегда перекрывает код — в самом compose об этом стоит предупреждающий комментарий про `AZURE_VOICE_LIVE_VOICE` («устаревшее значение тут молча отменяет правку в конфиге»).

### 9.7 Логирование: три стиля в одном проекте
- `getLogger(__name__)` — 48 раз (канон).
- `getLogger("main")` — 12 раз, включая `app/worker.py` и `app/services/queue.py`: логи воркера пишутся под именем веб-приложения.
- `print()` — 124 раза в production-коде `app/` и `voice_assistant/`, при настроенном `python-json-logger` + Sentry.
- Внутри логов: часть через f-строки (`logger.info(f"...")`), часть через `extra={...}` — структурированный формат применяется непоследовательно, JSON-поля есть только у меньшинства записей.

### 9.8 Дата и время: три подхода
- `datetime.utcnow()` — **206 вхождений**, наивные datetime без таймзоны, в том числе как `default=` во всех моделях. Метод deprecated в Python 3.12.
- `datetime.now(...)` — 9 вхождений.
- Есть `app/time_utils.py` с локальными датами (`today_local`), но импортируется **всего в 3 местах**.

Итог: в БД лежит UTC-naive, в отчётах (`train_report.py`) — локальная дата, конвертация — по месту.

### 9.9 Именование маршрутов и сущностей
- **URL:** 71 путь со `snake_case` (`/chat_trener`, `/attachments_trener`) против 41 с `kebab-case` (`/set-role`, `/forgot-password`, `/batch-analyze`, `/sync-all`). Внутри одного роутера соседствуют оба: `/crm/sync-status/{id}` и `/crm/batch-progress/{id}` рядом с `/crm/webhook/{integration_id}/{webhook_secret}`.
- **Префиксы:** часть роутеров объявляет `prefix=` в `APIRouter` (`/admin`, `/api/notifications`, `/sales`, `/api/zoom`), часть пишет полный путь в каждом декораторе (`analytics.py`, `crm_integration.py`, `auth.py`).
- **API vs страницы:** нет разделения. `/api/owner/{team_id}/data` и `/analytics/api/summary` — два разных способа обозначить «это JSON»; при этом `/teams/{team_id}/members` возвращает HTML, а `/crm/recordings` — JSON.
- **Суффикс `_trener`** как маркер второго продукта протянут через URL, файлы, функции и таблицы — вместо параметра.

### 9.10 Язык кода
Идентификаторы английские, комментарии и докстринги — русские, сообщения об ошибках — русские в API-ответах (`app/main.py:322` — `"Внутренняя ошибка сервера"`), но при этом есть i18n-слой с `en.json`. Часть докстрингов и комментариев — английские (`app/main.py:57-60`, `tests/test_capability_service.py`), часть тестов документирована по-русски, часть по-английски. Логи смешанные: `logger.info(f"Запрос: {method}")` рядом с `logger.info("Session cleanup complete")`.

### 9.11 Дубли файлов и мёртвый код
- `alembic/versions/` (001–021) и `app/alembic/versions/` (001–017) — два набора миграций, второй в образ не копируется.
- `migrate_to_postgresql.py` ≡ `app/migrate_to_postgresql.py` — **байт-в-байт**. То же для `migrate_trainings.py`.
- `create_admin.py` / `app/create_admin.py` / `docker_create_admin.py` / `app/create_postgres_admin.py` — четыре варианта одной задачи.
- `check_prompts.py` / `app/check_prompts.py` — расходятся на 11 строк.
- `app/routers/tts_proxy.py` (50) и `ai_agent_service/routers/tts_proxy.py` (62) — разошедшиеся копии.
- `voice_assistant/router.py` (863) и `voice_assistant/router_new.py` (708) — старый и новый роутеры голосового модуля, **оба подключены** в `app/main.py:295-320`; старый — со своими `VAD`/`STTEngine`/`GPTDialogue`/`TTSEngine`, полностью вытесненными Azure Voice Live.
- `app/services/pipeline_enhanced.py` — не импортируется нигде.
- `Claude_GROWUP/` и `claude growup/` — две папки, различающиеся регистром и пробелом.
- Пустые каталоги-заглушки: `app/checklists/`, `app/checklists_trener/`, `app/uploads/`, `app/voice_assistant/`, `app/app/` (с `app.db` внутри).
- Артефакты SQLite-эпохи при том, что SQLite явно запрещён в коде: `app/app.db`, `app/root_app.db`, `test.db`.
