# PLATFORM_MAP — карта проекта UpStat

Документ-навигатор по кодовой базе: что делает каждый файл, за что отвечает, какие в нём ключевые функции и **когда в него лезть**, чтобы менять что-то точечно.

> Как пользоваться: найдите нужную область (например «аналитика», «тренировки», «CRM», «голосовой ИИ»), посмотрите блок «Когда трогать» у файлов — он подскажет, где именно править. Фронтенд (внешний вид) — в разделе [Фронтенд](#фронтенд-templates--static).

## Содержание
- [Архитектура (коротко)](#архитектура-коротко)
- [Точки входа](#точки-входа)
- [Ядро приложения (app/)](#ядро-приложения-app)
- [Роутеры (app/routers/)](#роутеры-approuters)
- [Сервисы — часть 1 (app/services/)](#сервисы--часть-1-appservices)
- [Сервисы — часть 2 (app/services/)](#сервисы--часть-2-appservices)
- [Голосовой ИИ (voice_assistant/ и ai_agent_service/)](#голосовой-ии-voice_assistant-и-ai_agent_service)
- [Фронтенд (templates / static)](#фронтенд-templates--static)
- [Прочее (скрипты, боты, инфраструктура)](#прочее-скрипты-боты-инфраструктура)

---

## Архитектура (коротко)

- **Бэкенд:** FastAPI (Python), серверный рендеринг на **Jinja2-шаблонах** + htmx, без SPA-фреймворка.
- **БД:** PostgreSQL (SQLAlchemy 2.0 ORM). Сессии — `app/database.py`.
- **Структура:** `app/routers/*` (HTTP/WS-эндпоинты) → `app/services/*` (бизнес-логика) → `app/models.py` (ORM-таблицы). Шаблоны в `app/templates/`, статика в `app/static/`.
- **Анализ звонков** («pipeline»): аудио/текст/скриншоты → транскрибация → анализ по чек-листам (GPT-4o) → отчёт + аналитика (паспорт продавца, Win Probability, параметры).
- **Тренировки:** планы тренировок по слабым этапам + голосовые тренировки (Azure Voice Live).
- **Голосовой ИИ:** `voice_assistant/` встроен в основное приложение; `ai_agent_service/` — отдельный микросервис (STT→LLM→TTS) для встреч.
- **Режимы продукта (capabilities):** `full` (полный), `train` (только тренировки), `free` (триал) — управляются через `product_mode` пользователя и `app/services/capability_service.py`.

---

## Точки входа

### `main.py` (корень)
**Назначение:** точка входа для Docker — импортирует `app.main:app` и запускает uvicorn на порту 8000.
**Когда трогать:** параметры запуска контейнера (порт, ws-ping).

### `run.sh`
**Назначение:** shell-скрипт запуска приложения.
**Когда трогать:** локальный/прод запуск.

### `app/main.py`
См. раздел [Ядро приложения](#ядро-приложения-app) — фабрика `create_app()`, регистрация роутеров и middleware.

---

## Ядро приложения (app/)

### `app/main.py`
**Назначение:** Точка входа FastAPI-приложения: фабрика `create_app()`, автосоздание/миграции схемы БД, регистрация middleware и всех роутеров, глобальные обработчики ошибок.
**Ключевое:**
- `create_training_tables()` — идемпотентно создаёт (для SQLite) таблицы тренировок/уведомлений/метрик.
- `update_database_schema()` / `update_premium_schema()` / `create_org_capability_schema()` — ручные миграции схемы `users`, подписок, организаций/capabilities (SQLite + PostgreSQL).
- `BrandMiddleware` — определяет бренд (`train`/`full`) по hostname; локально через `?brand=`.
- `create_app()` — грузит `.env`, проверяет `SECRET_KEY`, монтирует `/static`, настраивает Jinja2, подключает все роутеры и middleware.
- `log_requests` / `global_exception_handler` / `validation_exception_handler` — логирование и обработчики 500/422.
- `app = create_app()` — глобальный экземпляр.

**Когда трогать:** добавление нового роутера/middleware, порядок инициализации, ручные миграции при старте.

### `app/database.py`
**Назначение:** Конфигурация подключения к БД (только PostgreSQL), фабрика сессий, базовый ORM-класс.
**Ключевое:** `DATABASE_URL` (обязателен, SQLite запрещён), `engine` (пул 20/40, pre-ping), `SessionLocal`, `Base(DeclarativeBase)`, `get_db()` (dependency-сессия).
**Когда трогать:** параметры пула, движок, строка подключения.

### `app/deps.py`
**Назначение:** FastAPI-зависимости авторизации и проверки прав/возможностей.
**Ключевое:**
- `require_user(request, db)` — пользователь из сессии или редирект на `/login`; Sale Manager ограничен `/sales`.
- `require_capability(key)` — фабрика зависимости: проверка capability, иначе 403 → `/dashboard`.

**Когда трогать:** логика аутентификации, ограничения ролей, модель capabilities.

### `app/security.py`
**Назначение:** Утилиты безопасности.
**Ключевое:** `hash_password`/`verify_password` (bcrypt), `validate_password_strength`, `generate_csrf_token`/`validate_csrf_token`, `sanitize_filename`, `is_safe_redirect_url`, `SecurityHeaders.get_security_headers()` (CSP и пр.), `validate_email`/`validate_phone`.
**Когда трогать:** политика паролей, CSP/заголовки, CSRF.

### `app/models.py`
**Назначение:** Все ORM-модели (SQLAlchemy 2.0) — схема БД проекта.
**Ключевое (основные таблицы):**
- `User` (`users`) — пользователи: роль (user/admin/manager/sale_manager), Google OAuth, подписка (`is_premium`, `free_analyses_limit`, `analyses_used`), `product_mode`, `organization_id`.
- `Conversation` / `Message` / `Attachment` — диалоги, сообщения, вложения.
- `ZoomMeeting` / `MeetingTranscript` / `CustomMeeting` / `MeetingParticipant` / `CustomMeetingTranscript` — Zoom- и WebRTC-встречи и их транскрипты.
- `Prompt` (`prompts`) — версионируемые промпты ИИ.
- `AnalysisTrainingPlan` / `Training` / `TrainingSession` / `VoiceTrainingMessage` — планы тренировок, тренировки/этапы, сессии, реплики голосовых тренировок.
- `Notification` — уведомления пользователя.
- `CRMIntegration` / `CRMRecording` / `CRMDeal` / `CRMLead` / `CRMContact` / `CRMCompany` / `CRMDealProduct` / `CRMActivity` / `CRMManagerMapping` — интеграции и сущности CRM.
- `Team` / `TeamMember` / `TeamInvitation` / `TeamScript` — команды, участники, приглашения, скрипты.
- `TrainingConversionMetric` / `TrainingErrorCorrection` — метрики конверсии и ошибки/коррекции.
- `ParameterDefinition` / `ParameterValue` — справочник параметров анализа и их значения по звонкам (~65 параметров).
- `ChecklistItemDefinition` / `ChecklistItemScore` / `WinProbabilityScore` — пункты чек-листов (с весами), оценки и вероятность выигрыша.
- `SellerPassport` / `PassportSnapshot` — паспорт продавца и снимки навыков.
- `ManagerAction` / `ActionPattern` — действия менеджера и подтверждённые паттерны.
- `AnalyticsMessage` — сообщения чата аналитики.
- `ResearchLog` — логи Research Mode (CoT-размышления ИИ).
- `TrainingProgram` / `TrainingProgramDay` — программы тренировок команды (train-режим).
- `Organization` — организация-клиент (`sku` FULL/TRAIN_RU/TRAIN_GLOBAL, capabilities).
- `PasswordResetToken` — токены сброса пароля.

**Когда трогать:** изменение структуры данных (новые таблицы/поля/связи). Помните: в `main.py` есть параллельные ручные миграции — синхронизируйте.

### `app/schemas.py`
**Назначение:** Pydantic-схемы запросов/ответов для встреч (Zoom и WebRTC) и WebSocket-сообщений (не покрывает все домены — только встречи).
**Ключевое:** `CreateMeetingRequest`, `MeetingResponse`/`MeetingListResponse`, `CreateCustomMeetingRequest`/`CustomMeetingResponse`, `JoinMeetingRequest`, `WebSocketMessage`/`AudioDataMessage`/`VideoDataMessage`/`ChatMessage`.
**Когда трогать:** контракты API встреч или формат WebSocket-сообщений.

### `app/admin.py`
**Назначение:** Хелперы и зависимости проверки ролей (admin / sale_manager).
**Ключевое:** `admin_required(f)`, `get_current_user(request, db)`, `is_admin(user)`/`is_sale_manager(user)`, `require_sale_manager(...)`.
**Когда трогать:** логика ролевого доступа к админ-/sales-разделам.

### `app/middleware/rate_limit.py`
**Назначение:** In-memory rate limiting.
**Ключевое:** `RateLimiter` (память, автоочистка), `RateLimitMiddleware` (правила: default 100/мин, api 30/мин, upload 10/5мин, auth 5/мин, authenticated 200/мин; строгие `/login`,`/register`,`/chat/send`,`/chat_trener/send`), `get_rate_limiter()`.
**Когда трогать:** настройка лимитов, новые строгие endpoints, переход на Redis.

### `app/utils/file_validator.py`
**Назначение:** Валидация загружаемых файлов (размер, расширение, magic bytes, MIME).
**Ключевое:** `ALLOWED_MIME_TYPES`/`MAX_FILE_SIZES`, `FileValidator.validate_file`, `validate_audio_file` (особый случай WAV), `validate_uploaded_file(...)`.
**Когда трогать:** новые форматы, лимиты размера, проверки безопасности загрузок.

### `app/__init__.py`
**Назначение:** делает `app` пакетом (тривиальный).

---

## Роутеры (app/routers/)

### `app/routers/admin.py`
**Назначение:** Админ-панель (`/admin`): дашборд статистики, управление пользователями/ролями, каскадное удаление.
**Маршруты:** `GET /admin/`, `GET /admin/users`, `POST /admin/users/{id}/set-role`, `POST /admin/users/{id}/delete`.
**Когда трогать:** админ-функции по пользователям/ролям, удаление аккаунта.

### `app/routers/admin_prompts.py`
**Назначение:** Управление AI-промптами (`/admin/prompts`) для `sales_audit_summary` и `sales_trainer`, с версионированием.
**Маршруты:** `GET /admin/prompts/`, `GET|POST /create`, `GET|POST /trainer`, `GET /{name}/versions`, `GET|POST /{name}/edit`, `POST /{id}/activate`, `POST /{id}/delete`.
**Когда трогать:** логика создания/активации/версий промптов.

### `app/routers/admin_research.py`
**Назначение:** Страница «Исследование» (`/admin/research`) — CoT-логи AI-пайплайна: просмотр/скачивание/удаление.
**Маршруты:** `GET /admin/research/`, `GET /{log_id}` (cards/raw), `GET /{log_id}/download`, `POST /{log_id}/delete`.
**Когда трогать:** отладочные логи AI и их отображение.

### `app/routers/analytics.py`
**Назначение:** «Аналитика» для РОПа/менеджера (capability `call_analysis`): графики по параметрам звонков, чат с ИИ-ассистентом, CRM-аналитика.
**Маршруты:** `GET /analytics`, `POST /analytics/send` + `GET /analytics/poll` + `POST /analytics/clear` (чат), `GET /analytics/api/summary|trend|comparison|boolean-stats|metrics-list|team-members`, `GET /analytics/api/buttons` + `POST /analytics/query`, `GET /analytics/api/crm/revenue|avg-check|win-rate|conversion|cycle|revenue-by-manager`.
**Когда трогать:** графики, метрики, AI-аналитика команды и CRM.

### `app/routers/auth.py`
**Назначение:** Аутентификация/регистрация: логин/логаут, email-верификация кодом, Google OAuth, сброс пароля, привязка приглашений.
**Маршруты:** `GET|POST /login`, `POST /logout`, `GET|POST /register`, `POST /register/verify`, `POST /register/resend`, `GET /auth/google` + `/auth/google/callback`, `GET|POST /forgot-password`, `GET|POST /reset-password`.
**Когда трогать:** вход/регистрация, OAuth, верификация, сброс пароля.

### `app/routers/chat.py`
**Назначение:** Основной чат ИИ-аналитика (capability `call_analysis`): загрузка аудио/текста/скриншотов, запуск пайплайна, лимиты, загрузка менеджером за участников.
**Маршруты:** `GET /chat`, `GET /chat/poll`, `POST /chat/send`, `GET /attachments/{id}`, `GET /chat/export/by-report/{id}`, `POST /chat/reset`.
**Когда трогать:** загрузка файлов, лимиты, запуск основного анализа.

### `app/routers/chat_trener.py`
**Назначение:** Чат «Тренер» — аналог основного чата с пайплайном тренера (`run_pipeline_trener*`).
**Маршруты:** `GET /chat_trener`, `GET /chat_trener/poll`, `POST /chat_trener/send`, `GET /attachments_trener/{id}`, `GET /chat_trener/export/by-report/{id}`, `POST /chat_trener/reset`.
**Когда трогать:** логика чата-тренера.

### `app/routers/crm_integration.py`
**Назначение:** Интеграции CRM (capability `crm`): подключение AmoCRM/Bitrix24, синхронизация, анализ записей, маппинг менеджеров, webhook.
**Маршруты:** `GET /crm`, `POST /crm/connect/{type}` + `/connect/bitrix24/webhook` + `GET /crm/oauth/callback`, `POST /crm/sync/{id}` + `GET /crm/sync-status/{id}`, `POST /crm/sync-chats|sync-all/{id}`, `GET /crm/recordings`, `POST /crm/recordings/{id}/analyze` + `/batch-analyze` + `GET /crm/batch-progress/{id}`, `POST /crm/webhook/{id}/{secret}`, маппинг менеджеров, `GET /crm/recording/{id}/details`.
**Когда трогать:** подключение CRM, синхронизация, автоанализ записей.

### `app/routers/dashboard.py`
**Назначение:** Главный кабинет: ветвление по capabilities (FREE / train / full), агрегация анализов, страница звонков, настройки.
**Маршруты:** `GET /dashboard`, `GET /calls`, `POST /test/create-training`, `GET /settings`.
**Когда трогать:** логика главного экрана, ветвление по тарифу/режиму, агрегация анализов.

### `app/routers/notifications.py`
**Назначение:** REST API уведомлений (`/api/notifications`) поверх `NotificationService`.
**Маршруты:** `GET /unread|all|count`, `POST /{id}/read` + `/read-all`, `DELETE /{id}` + `/clear-all`.
**Когда трогать:** API/логика уведомлений.

### `app/routers/owner_dashboard.py`
**Назначение:** Экран владельца (capability `owner_dashboard`) — сводная аналитика команды.
**Маршруты:** `GET /owner`, `GET /owner/{team_id}`, `GET /api/owner/{team_id}/data`.
**Когда трогать:** сводный экран владельца и его метрики.

### `app/routers/performance.py`
**Назначение:** Админ-API мониторинга/обслуживания (`/api/performance`, только admin): кеш, БД, хранилище, система.
**Маршруты:** `GET/POST /cache/*`, `GET/POST /database/*`, `GET/POST /storage/*`, `GET /system/info` + `/overview`.
**Когда трогать:** обслуживание/диагностика производительности.

### `app/routers/progress.py`
**Назначение:** API прогресса долгих операций (`/api/progress`) поверх `progress_tracker`.
**Маршруты:** `GET /{operation_id}`, `GET /active/list`, `POST /{operation_id}/cancel`.
**Когда трогать:** отображение прогресса анализа/обработки.

### `app/routers/public.py`
**Назначение:** Публичные страницы без авторизации: лендинг и карьера.
**Маршруты:** `GET /` (лендинг), `GET /career`.
**Когда трогать:** публичные маркетинговые страницы.

### `app/routers/sales.py`
**Назначение:** Панель Sale Manager (`/sales`): подписки/лимиты, назначение продукта (SKU → product_mode), организация.
**Маршруты:** `GET /sales/`, `POST /sales/toggle-premium/{id}`, `POST /sales/reset-analyses/{id}`, `POST /sales/assign-product/{id}`.
**Когда трогать:** тарифы/подписки, назначение продуктов и доступов.

### `app/routers/settings.py`
**Назначение:** Настройки пользователя: профиль, пароль, аватар.
**Маршруты:** `GET /settings`, `POST /settings/profile`, `POST /settings/password`, `POST /settings/avatar`.
**Когда трогать:** личный кабинет (профиль/пароль/аватар).

### `app/routers/team_analytics.py`
**Назначение:** Аналитика команды (capability `team_analytics`): сводка, паспорт продавца, конверсия, ошибки/коррекции, статистика планов.
**Маршруты:** `GET /teams/{id}/analytics`, `GET /teams/{id}/member/{mid}/report`, `GET /api/teams/{id}/conversion-metrics` + `/errors-corrections`, `POST /api/teams/{id}/errors/{eid}/mark-applied`, `GET /teams/{id}/member/{mid}/plan/{pid}/stats`.
**Когда трогать:** аналитика по командам/участникам, паспорт продавца, метрики.

### `app/routers/teams.py`
**Назначение:** Команды и приглашения: создание, участники, инвайты по email, загрузка скрипта (текст/Word → чек-лист).
**Маршруты:** `GET /teams/my`, `POST /teams`, `GET /teams/{id}/members`, `POST /teams/{id}/invitations`, `GET|POST /teams/{id}/script`.
**Когда трогать:** команды, приглашения, скрипты продаж.

### `app/routers/train_report.py`
**Назначение:** Train-отчёт РОПу (capability `train_report`): кто тренировался, средний score, streak.
**Маршруты:** `GET /teams/{id}/train-report`.
**Когда трогать:** train-отчёт по активности тренировок.

### `app/routers/training_catalog.py`
**Назначение:** Каталог тренировок (capability `training_catalog`): выбор этапа и запуск.
**Маршруты:** `GET /trainings/catalog`, `POST /trainings/catalog/start`.
**Когда трогать:** каталог самостоятельных тренировок.

### `app/routers/training_plans.py`
**Назначение:** Планы тренировок и сессии: показ/создание плана по анализу, старт тренировки, завершение с оценкой.
**Маршруты:** `GET /training-plan/by-plan/{id}`, `GET /training-plan/{report_msg_id}`, `POST /training/{id}/start`, `POST /training-session/{id}/complete`, `GET /api/training/{id}`.
**Когда трогать:** планы, прохождение сессий, оценки.

### `app/routers/training_program.py`
**Назначение:** Программа тренировок команды (capability `training_program`): цикл и порядок этапов по дням.
**Маршруты:** `GET /teams/{id}/program`, `POST /teams/{id}/program`.
**Когда трогать:** цикличная программа тренировок команды.

### `app/routers/tts_proxy.py`
**Назначение:** Прокси TTS к AI Agent Service со стримингом аудио.
**Маршруты:** `POST /tts-proxy`.
**Когда трогать:** интеграция синтеза речи через внешний сервис.

### `app/routers/webrtc_meetings.py`
**Назначение:** WebRTC-встречи (API `/api/webrtc` + HTML `/webrtc`): создание/список/детали, WS-подключение, AI-агент, транскрипты.
**Маршруты:** `POST /api/webrtc/meetings/create`, `GET /meetings` + `/{id}`, `POST /{id}/join`, `WS /{id}/join`, `POST /{id}/start-ai-agent` + `/end`, `GET /{id}/info`, `DELETE /{id}`, `GET /webrtc/meetings/{id}/room`.
**Когда трогать:** видеовстречи на WebRTC и их AI-агент.

### `app/routers/zoom_meetings.py`
**Назначение:** Zoom-встречи с ИИ-агентом (`/api/zoom`): создание/список/детали, запуск/завершение, управление агентом (SDK Runner + AI Agent Service), JWT-подпись, транскрипты.
**Маршруты:** `POST /meetings/create`, `GET /meetings` + `/{id}`, `POST /{id}/start|join|end`, `GET /{id}/transcript`, `POST /{id}/start-agent|stop-agent` + `GET /{id}/agent-status`, `POST /sdk-signature`, `DELETE /{id}`.
**Когда трогать:** интеграция Zoom, ИИ-агент на встречах, SDK-подпись.

---

## Сервисы — часть 1 (app/services/)

### `app/services/analytics_assistant.py`
**Назначение:** ИИ-ассистент для РОПа — отвечает на вопросы по структурированным параметрам звонков (SQL к `parameter_values` + контекст в GPT).
**Когда трогать:** логика чат-ассистента аналитики.

### `app/services/analytics_buttons.py`
**Назначение:** Каталог кнопок навигации в чате аналитики — 2 уровня (`BLOCKS`: блоки параметров → вопросы).
**Когда трогать:** состав кнопок/вопросов в аналитике.

### `app/services/analytics_queries.py`
**Назначение:** SQL-обработчики для кнопочной аналитики; каждый `query_type` делает запрос к `parameter_values`, `format_with_ai()` оборачивает результат через GPT-4o-mini.
**Когда трогать:** новые типы вопросов/запросов кнопочной аналитики.

### `app/services/analytics_service.py`
**Назначение:** Базовая аналитика команд/участников.
**Ключевое:** `AnalyticsService.calculate_conversion_rates`, `get_member_analytics`, `get_team_analytics`, `get_conversion_trends`, `extract_errors_from_analysis`.
**Когда трогать:** расчёт конверсий, агрегатов по участникам/командам, извлечение ошибок.

### `app/services/caching_service.py`
**Назначение:** Кеширование для производительности (хеш-ключи, TTL).
**Когда трогать:** кеш тяжёлых вычислений/запросов.

### `app/services/capability_service.py`
**Назначение:** Реестр SKU → capabilities и проверки доступа. Источник истины — `user.product_mode` (NULL→FULL, free→FREE, full→FULL, train→TRAIN_RU/GLOBAL).
**Когда трогать:** ключевой файл для тарифов/режимов — что доступно в FULL/TRAIN/FREE, добавление новых capability.

### `app/services/checklist_registry_service.py`
**Назначение:** Синхронизация справочника пунктов чек-листов (`checklist_item_definitions`) из JSON в `checklists/` с весами для Win Probability.
**Когда трогать:** изменение пунктов/весов чек-листов для расчёта вероятности.

### `app/services/crm_service.py`
**Назначение:** Клиент CRM (AmoCRM/Bitrix24): OAuth, синхронизация записей/сущностей, вызовы API (большой файл).
**Когда трогать:** интеграция с конкретными CRM, форматы их API, синхронизация.

### `app/services/curriculum_service.py`
**Назначение:** Создание `AnalysisTrainingPlan` из каталога тренировок без реального звонка и без GPT (stub-Conversation/Message); `plan_source="catalog"/"program"`.
**Когда трогать:** запуск тренировок из каталога/программы в train-режиме.

### `app/services/db_optimizer.py`
**Назначение:** Оптимизация работы с БД (индексы, очистка, статистика, eager-loading).
**Когда трогать:** обслуживание БД, оптимизация запросов.

### `app/services/email.py`
**Назначение:** Отправка email (приглашения в команду, сброс пароля, верификация) через SMTP. Содержит HTML-шаблоны писем.
**Когда трогать:** тексты/вёрстка писем, SMTP-настройки.

### `app/services/error_handler.py`
**Назначение:** Централизованная обработка ошибок (типизация, дружелюбные сообщения).
**Когда трогать:** единая обработка/маппинг ошибок.

### `app/services/file_optimizer.py`
**Назначение:** Оптимизация/очистка файлового хранилища (uploads), удаление старого.
**Когда трогать:** управление хранилищем файлов.

### `app/services/google_oauth.py`
**Назначение:** Google OAuth — обмен кода на токен, получение профиля, поиск/создание пользователя.
**Когда трогать:** вход через Google.

### `app/services/image_pipeline.py`
**Назначение:** Анализ скриншотов переписок: GPT-4o Vision (OCR + роли + порядок) → текстовый транскрипт → обычный пайплайн анализа по чек-листам.
**Когда трогать:** анализ переписок по скриншотам.

### `app/services/manager_actions_service.py`
**Назначение:** Извлечение действий менеджера из звонка и накопление статистических паттернов (`ManagerAction`/`ActionPattern`). Содержит HTML для отображения.
**Когда трогать:** учёт действий менеджера и паттерны успешных приёмов.

### `app/services/notification_service.py`
**Назначение:** Сервис уведомлений пользователю (создание/чтение/счётчик, типы уведомлений).
**Когда трогать:** генерация и логика уведомлений.

### `app/services/owner_analytics_service.py`
**Назначение:** Аналитика для экрана владельца (Owner Command Center): утечки денег, конверсия, риски, команда, прогноз.
**Когда трогать:** метрики и расчёты экрана владельца.

### `app/services/parameter_extraction.py`
**Назначение:** Извлечение структурированных параметров из транскрипта (последовательно ПОСЛЕ основного анализа), параметры берутся из `parameter_definitions`, извлечение через GPT-4o.
**Когда трогать:** состав/логика извлечения параметров аналитики звонка.

---

## Сервисы — часть 2 (app/services/)

> Про pipeline-файлы (все три — про анализ звонков): `pipeline.py` — «боевой» FULL-анализ с маскированием PII, Research Mode, Win Probability, паспортом продавца и извлечением ~65 параметров; `pipeline_enhanced.py` сам ничего не анализирует — оборачивает `pipeline.py` прогрессом и уведомлениями; `pipeline_trener.py` — облегчённый учебный анализ со своими чек-листами/промптами и без пост-аналитики.

### `app/services/pii_redactor.py`
**Назначение:** Маскирование персональных данных (PII) в тексте перед отправкой в LLM.
**Ключевое:** `redact_pii(text)` (телефоны/email/URL/ИНН/карты/паспорта/адреса/ФИО → плейсхолдеры), `_redact_persons(text)`, `redact_pii_in_dialogue(dialogue)`.
**Когда трогать:** новые типы чувствительных данных, правки regex маскирования.

### `app/services/pipeline.py`
**Назначение:** Основной конвейер анализа звонков (FULL): аудио/текст → транскрибация → JSON-диалог → анализ по чек-листам → отчёт + аналитика.
**Ключевое:** `run_pipeline(...)` (ffmpeg → ElevenLabs/Whisper → turns → анализ → отчёт), `run_pipeline_from_text(...)`, `run_pipeline_from_raw_text(...)`, `_analyze_checklist(...)`, `_words_to_turns(...)`. После анализа — Win Probability, паспорт продавца, параметры, действия менеджера, Research Mode, прогресс.
**Когда трогать:** транскрибация, формат диалога, состав пост-обработки FULL-анализа.

### `app/services/pipeline_enhanced.py`
**Назначение:** Обёртка над `pipeline.py` с прогрессом и push-уведомлениями.
**Ключевое:** `run_pipeline_with_progress(...)`, `run_pipeline_from_text_with_progress(...)`.
**Когда трогать:** UX прогресса/уведомлений вокруг анализа (без правки самой логики).

### `app/services/pipeline_trener.py`
**Назначение:** Упрощённый конвейер для режима «тренер» — отдельные чек-листы (`checklists_trener`) и промпты `sales_trainer`.
**Ключевое:** `run_pipeline_trener(...)`, `run_pipeline_from_text_trener(...)`, `run_pipeline_from_raw_text_trener(...)`, `safe_format_prompt(...)`.
**Когда трогать:** тренерский сценарий анализа, его промпты/чек-листы.

### `app/services/progress_tracker.py`
**Назначение:** Отслеживание прогресса длительных операций (этапы, %, ETA).
**Ключевое:** `ProgressInfo`, `ProgressTracker` (`create/update/complete/fail/cancel_operation`), `get_progress_tracker()`, `ProgressStatus`/`ProgressStage`.
**Когда трогать:** новые этапы/операции с прогрессом, расчёт времени.

### `app/services/prompt_service.py`
**Назначение:** Управление версионируемыми промптами в БД.
**Ключевое:** `PromptService.get_active_prompt(name)`, `create_prompt_version(...)`, `activate_prompt_version(id)`, `get_prompt_versions/...`, `get_prompt_statistics()`.
**Когда трогать:** система хранения/версионирования промптов.

### `app/services/research_service.py`
**Назначение:** Research Mode — детальный CoT-лог анализа (промпты, raw-ответы, решения, токены) в файл + БД для админки.
**Ключевое:** `ResearchLogger` (`write_header`/`capture_stage`/`capture_note`/`finalize`), `REASONING_INSTRUCTION_*`, `parse_research_file(content)`, `extract_reasoning_block(...)`.
**Когда трогать:** формат research-логов и вьюер `/admin/research`.

### `app/services/seller_passport_service.py`
**Назначение:** Паспорт продавца — оценка по 5 этапам продаж и фиксация динамики (снимки).
**Ключевое:** `evaluate_stage_scores(...)` (этапы contact/needs/presentation/objections/closing), `update_seller_passport(...)`, `_resolve_manager_user_id(...)`, `_find_completed_training_since(...)`.
**Когда трогать:** методика оценки этапов, шкала баллов, учёт эффекта тренировок.

### `app/services/signature_service.py`
**Назначение:** Генерация/валидация JWT-подписей для Zoom Meeting SDK.
**Ключевое:** `ZoomSignatureService.generate_zoom_signature(...)`, `validate_signature(...)`, `signature_service`.
**Когда трогать:** интеграция Zoom SDK, требования к подписи/ролям.

### `app/services/team_access.py`
**Назначение:** Права доступа к командам и получение доступных пользователей/скриптов.
**Ключевое:** `is_admin/is_manager`, `assert_can_manage_team(...)`, `get_manager_teams/get_user_teams`, `get_accessible_user_ids_for_manager(...)`, `get_team_script_for_user(...)`.
**Когда трогать:** ролевая модель, видимость участников, привязка скриптов команд.

### `app/services/team_invitations.py`
**Назначение:** Приглашения в команды по токену.
**Ключевое:** `create_invitations(...)`, `get_invitation_by_token(...)`, `accept_invitation(...)` (наследует organization_id/product_mode=train), `TeamInvitationStatus`.
**Когда трогать:** флоу инвайтов, срок действия, назначение тарифа/организации.

### `app/services/team_script_service.py`
**Назначение:** Конвертация скрипта продаж (текст/Word) в формат чек-листа (JSON).
**Ключевое:** `convert_to_checklist_format(...)`, `extract_text_from_word(path)`, `parse_text_to_checklist_format(text)`, фолбэк `parse_text_with_gpt(text)`.
**Когда трогать:** формат командных скриптов/чек-листов и их парсинг.

### `app/services/training_plan_service.py`
**Назначение:** Создание плана тренировок по отчёту анализа (выбор слабого этапа, рекомендации).
**Ключевое:** `TrainingPlanService.create_training_plan(...)`, `_pick_critical_stage(...)`, `_extract_recommendations_with_gpt(...)`, `unlock_next_training(...)`, `_generate_checklist(...)`.
**Когда трогать:** подбор тренировок, приоритизация этапов, структура плана.

### `app/services/training_stages_service.py`
**Назначение:** Многоэтапные голосовые тренировки — загрузка промптов этапов из файлов и управление переходами.
**Ключевое:** `load_stages(stage_name)` (`static/docs/trainings/<stage>/stage_N.txt`), `build_stage_tools()`, `strip_tags/has_stage_complete/has_training_complete`, `_get_first_line_template(...)`.
**Когда трогать:** новые многоэтапные сценарии, логика переходов между этапами.

### `app/services/training_validator_service.py`
**Назначение:** AI-валидация прохождения тренировки (оценка транскрипта диалога с ИИ-тренером).
**Ключевое:** `TrainingValidatorService.validate_training(...)` (0-100, `passed` при ≥70), `validate_and_complete_training(...)`, `VALIDATION_PROMPT`.
**Когда трогать:** критерии/порог зачёта, завершение плана.

### `app/services/webrtc_meeting_service.py`
**Назначение:** Управление WebRTC-встречами с ИИ-агентом (Redis + WebSocket, маршрутизация аудио/чата).
**Ключевое:** `WebRTCMeetingService.create_meeting/join_meeting/leave_meeting/_end_meeting`, `start_ai_agent/_connect_to_ai_agent/_listen_ai_agent_messages`, `handle_audio_data/handle_voice_message/handle_chat_message`, `webrtc_service`.
**Когда трогать:** WebRTC-встречи, интеграция с ИИ-агентом, рассылка участникам.

### `app/services/win_probability_service.py`
**Назначение:** Расчёт вероятности закрытия сделки по взвешенным оценкам чек-листов (потолок 80%).
**Ключевое:** `save_checklist_scores(...)`, `calculate_win_probability(...)`, `generate_probability_report(...)`.
**Когда трогать:** веса критериев, формула вероятности (`MAX_PROBABILITY`), формат отчёта.

### `app/services/zoom_service.py`
**Назначение:** Интеграция с Zoom REST API (Server-to-Server OAuth): создание/получение/удаление встреч.
**Ключевое:** `ZoomService._get_access_token()`, `create_meeting(...)`, `get_meeting_info/update_meeting_status/delete_meeting/update_meeting_agent_status`, `_generate_password()`.
**Когда трогать:** интеграция Zoom API, настройки встреч, хранение статусов.

### app/scripts/ — служебные скрипты данных
- **`add_analytics_parameters.py`** — добавляет ~54 параметра аналитики в `parameter_definitions` (идемпотентно). Запуск: `python3 -m app.scripts.add_analytics_parameters`.
- **`backfill_dialogue_metrics.py`** — пересчёт 4 базовых метрик диалога (`talk_listen_ratio` и др.) для старых звонков.
- **`fill_test_params.py`** — заполнение случайными тестовыми значениями ~65 параметров (демо/отладка аналитики).

---

## Голосовой ИИ (voice_assistant/ и ai_agent_service/)

> Две подсистемы: **`voice_assistant/`** встроен в основное FastAPI-приложение (используется на странице голосовой тренировки, общие модели/БД, Azure Voice Live). **`ai_agent_service/`** — отдельный микросервис (своя `config.py`, `Dockerfile`, цепочка STT→LLM→TTS), подключается к встречам (Zoom/WebRTC) и вызывается через `tts-proxy`/websocket.

### voice_assistant/ (встроенный)

#### `voice_assistant/config.py`
**Назначение:** Настройки голосового ИИ — API-ключи (OpenAI/ElevenLabs/Azure), модель GPT, параметры голоса/STT/TTS.
**Когда трогать:** ключи, выбор модели/голоса, параметры распознавания и синтеза.

#### `voice_assistant/router.py`
**Назначение:** Масштабируемый роутер голосового ассистента (изолированные сессии, 100+ пользователей).
**Когда трогать:** эндпоинты/WS голосового ассистента.

#### `voice_assistant/router_new.py`
**Назначение:** Новый масштабируемый роутер голосового ассистента (изолированные сессии).
**Когда трогать:** обновлённая версия роутинга голосовых сессий.

#### `voice_assistant/websocket_handler.py`
**Назначение:** WebSocket-обработчик для Azure Voice Live API — проксирует соединение клиент ↔ Azure.
**Когда трогать:** логика проксирования Azure Voice Live, обработка событий аудио.

#### `voice_assistant/session_manager.py`
**Назначение:** Менеджер сессий — изоляция голосовых тренировок пользователей (ThreadPoolExecutor, масштабируемость).
**Когда трогать:** управление параллельными сессиями.

#### `voice_assistant/stt_reactive.py`
**Назначение:** Распознавание речи (STT): faster-whisper / OpenAI Whisper API / ElevenLabs Scribe; реактивное в реальном времени.
**Когда трогать:** выбор/настройка движка распознавания.

#### `voice_assistant/vad.py`
**Назначение:** Определение активности речи (VAD) пороговым методом по RMS-амплитуде.
**Когда трогать:** чувствительность определения речи.

#### `voice_assistant/gpt_logic.py`
**Назначение:** Диалоговая логика с GPT (потоковая передача запросов/ответов GPT-4o/mini).
**Когда трогать:** логика диалога ИИ в голосовых сессиях.

#### `voice_assistant/tts_response.py`
**Назначение:** Озвучивание ответов (TTS): OpenAI TTS и ElevenLabs.
**Когда трогать:** синтез речи, выбор/настройка голоса.

#### `voice_assistant/azure_voice_live.py`
**Назначение:** Работа с Azure Voice Live API — проксирование WebSocket-соединения.
**Когда трогать:** интеграция с Azure Voice Live.

#### `voice_assistant/db_service.py`
**Назначение:** Работа с БД для голосовых тренировок (сохранение реплик/сессий).
**Когда трогать:** хранение данных голосовых тренировок.

#### `voice_assistant/get_voices.py`
**Назначение:** Утилита получения списка доступных голосов (TTS).
**Когда трогать:** обновление справочника голосов.

#### `voice_assistant/utils/` и `voice_assistant/web/`
**Назначение:** `utils/` — вспомогательные утилиты (например, `audio_utils` — RMS и обработка аудио); `web/` — статика/шаблоны голосового ассистента.

### ai_agent_service/ (отдельный микросервис)

#### `ai_agent_service/main.py`
**Назначение:** FastAPI-микросервис ИИ-агента: цепочка STT→LLM→TTS, WebSocket, фоновые задачи; подключается к встречам.
**Когда трогать:** логика ИИ-агента на встречах, его эндпоинты/WS.

#### `ai_agent_service/config.py`
**Назначение:** Настройки микросервиса (pydantic `BaseSettings`, собственный `.env`).
**Когда трогать:** ключи/параметры микросервиса ИИ-агента.

#### `ai_agent_service/pipeline/audio_pipeline.py`
**Назначение:** Аудио-конвейер микросервиса (обработка потока аудио).

#### `ai_agent_service/routers/tts_proxy.py`
**Назначение:** Прокси TTS-запросов (стриминг аудио) — парная сторона к `app/routers/tts_proxy.py`.

#### `ai_agent_service/services/`
**Назначение:** Сервисы микросервиса: `llm_service.py` (LLM), `stt_service.py` (распознавание), `tts_service.py` (синтез), `pii_redactor.py` (маскирование), `websocket_client.py` (WS-клиент), `zoom_client.py` (интеграция Zoom).

#### `ai_agent_service/voice_modules/utils`
**Назначение:** Вспомогательные утилиты микросервиса.

---

## Фронтенд (templates / static)

> Стек: Jinja2-шаблоны + htmx + ванильный CSS. Единый дизайн: navy-палитра, без градиентов, иконки — inline-SVG через макрос. Дизайн-токены (цвета/радиусы/тени) задаются в `:root` внутри `static/styles.css` и в `<style>` каркасов.

### Каркасы и общие шаблоны
- **`app/templates/base.html`** — базовый шаблон для standalone-страниц (топбар, переключатель темы). Здесь же бренд «UpStat».
- **`app/templates/_layout_dashboard.html`** — основной каркас кабинета: сайдбар-навигация, топбар, баннеры подписки, панель уведомлений, дизайн-токены (`:root` + `body.dark`). **Главный файл для общего вида кабинета.**
- **`app/templates/partials/icons.html`** — Jinja-макрос `icon(name, size, cls)` — единый набор inline-SVG иконок (~65). **Добавлять новые иконки сюда.**
- **`app/templates/partials/messages.html` / `messages_trener.html` / `analytics_messages.html`** — частичные шаблоны лент сообщений (htmx-поллинг).

### Страницы кабинета (наследуют `_layout_dashboard.html`)
`dashboard.html` (главная), `calls.html` (история звонков), `chat.html` (чат-аналитик), `chat_trener.html` (чат-тренер), `analytics.html` (аналитика), `team_analytics.html`, `team_members.html`, `team_manage.html`, `team_script.html` (команды), `training_plan.html` (план тренировок), `member_report.html` (паспорт продавца), `member_plan_stats.html`, `owner_dashboard.html` (экран владельца), `crm_integration.html` / `crm_recordings.html` (CRM), `settings.html` (настройки), `voice_training_conference.html` (голосовая тренировка).
**Когда трогать:** вид/верстка конкретной страницы кабинета.

### Админка (наследуют `admin/_layout_admin.html`)
- **`admin/_layout_admin.html`** — каркас админки (сайдбар, токены navy, красный только для destructive).
- Страницы: `admin/dashboard.html`, `admin/users.html`, `admin/prompts.html`, `admin/prompt_create.html` / `prompt_edit.html` / `prompt_versions.html` / `prompt_trainer.html`, `admin/research_list.html` / `research_view.html`.

### Train-кабинет (наследуют `train/_layout.html`)
- **`train/_layout.html`** — каркас train-режима (только тренировки), navy-палитра.
- Страницы: `train/dashboard.html`, `train/catalog.html`, `train/rop_report.html`, `train/team_program.html`.

### Sales и публичные
- **`sales/users.html`** — панель Sale Manager (выдача доступов/тарифов).
- **`landing.html`** — публичный лендинг (минималистичный navy).
- **`career.html`** — страница карьеры. **`free_activation.html`** — экран FREE-активации.
- **`admin_performance.html`** — страница мониторинга производительности.
- **`webrtc_meeting.html`** — комната WebRTC-встречи.
- **Вход/регистрация** (НЕ в общем стиле, отдельный `auth.css`): `login.html`, `register.html`, `register_verify.html`, `forgot-password.html`, `reset-password.html`.

### Стили (app/static/)
- **`static/styles.css`** — главный файл стилей: дизайн-токены (`:root`/`body.dark`), кнопки, карточки, таблицы, чат, компоненты. **Основной файл для глобального вида.**
- **`static/auth.css`** — стили страниц входа/регистрации (отдельная тема).
- **`static/css/voice-training.css`** — стили страницы голосовой тренировки.
- **`static/css/notifications.css`** — стили уведомлений.
- **`static/css/progress-tracker.css`** — стили индикатора прогресса.

### Скрипты (app/static/js/)
- **`static/js/voice-training.js`** — клиент голосовой тренировки (WebSocket, аудио, статусы, оверлей реконнекта).
- **`static/js/webrtc-meeting.js`** — клиент WebRTC-встреч (кнопки mic/камера/ИИ, потоки).
- **`static/js/notifications.js`** — клиент панели уведомлений.
- **`static/js/progress-tracker.js`** — отображение прогресса операций.
- **`static/js/audio-processor.js`** — AudioWorklet/обработка аудио.
- **`static/js/zoom_dashboard.js`** — клиент дашборда Zoom-встреч.

### Прочая статика
- `static/img/`, `static/avatars/` — изображения и аватары.
- `static/docs/` — текстовые материалы (в т.ч. `static/docs/trainings/<stage>/stage_N.txt` — промпты этапов тренировок, читаются `training_stages_service.py`).

---

## Прочее (скрипты, боты, инфраструктура)

### Telegram-боты (корень)
- **`bot.py`** / **`bot1.py`** — Telegram-боты на `aiogram` (отдельные точки входа, не часть веб-приложения).

### Создание/инициализация
- **`app/create_admin.py`**, **`app/create_postgres_admin.py`** — создание администратора.
- **`app/init_prompts.py`**, **`app/init_trainer_prompt.py`** — инициализация промптов в БД.
- **`app/check_prompts.py`** — проверка промптов.

### Миграции
- **`alembic/`** + `alembic.ini` — миграции Alembic (схема БД).
- **`app/migrate_to_postgresql.py`**, **`app/migrate_trainings.py`** — разовые миграции данных.

### Документация
- **`PLATFORM_MAP.md`** (корень) — этот файл-навигатор по проекту.
- **`docs/guides/`** — все прочие гайды и заметки (54 файла: деплой, OAuth, тренировки, CRM, voice и т.д.), ранее лежали в корне.
- **`docs/`** — отдельный node-инструмент для генерации PDF из HTML (`package.json`, `*.mjs`, `upstat-workflow-schemes/`) — не путать с `docs/guides/`.

### Данные/чек-листы
- **`checklists/`** — JSON чек-листы для FULL-анализа (с весами для Win Probability).
- **`checklists_trener/`** — чек-листы для режима «тренер».

### Инфраструктура
- **`requirements.txt`** — зависимости Python.
- **`Dockerfile`**, **`docker-compose.yml`**, **`docker-compose.override.yml`** — контейнеризация (app + Postgres `:5433` + Redis `:6380`).
- **`nginx.conf`**, **`ssl/`** — обратный прокси и сертификаты.
- **`.env`** / **`env.example`** — переменные окружения (ключи API, БД, Redis, SECRET_KEY).
- **`Voice-Live-Api-main/`**, **`sdk-runner/`** — вспомогательные подпроекты (Azure Voice Live примеры, Zoom SDK Runner).

---

_Документ описывает структуру на момент создания. При добавлении новых роутеров/сервисов/страниц — дополняйте соответствующий раздел, чтобы карта оставалась актуальной._
