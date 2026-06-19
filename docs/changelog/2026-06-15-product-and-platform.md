# UpStat — продукт, платформа и инфраструктура (15 июня 2026)

Дата: **2026-06-15**  
Охват: train-режим, capabilities/SKU, карта проекта, реорганизация документации, гигиена репозитория, прочие изменения.

---

## 1. Train-режим и capabilities (SKU)

### Контекст
UpStat поддерживает несколько **режимов продукта** через `user.product_mode` и таблицу `Organization` с полем `sku`. Это позволяет продавать:
- **FULL** — полная платформа (анализ звонков, CRM, аналитика, тренировки)
- **TRAIN_RU / TRAIN_GLOBAL** — только тренировки
- **FREE** — триал без платных функций

### Источник истины
**Файл:** `app/services/capability_service.py`

```python
SKU_CAPABILITIES = {
    "FULL": { call_analysis, crm, team_analytics, owner_dashboard, progress,
              voice_training, training_catalog, training_program, train_report },
    "TRAIN_RU": { voice_training, training_catalog, training_program, train_report },
    "TRAIN_GLOBAL": { voice_training, training_catalog, training_program, train_report },
    "FREE": set(),
}
```

**Проверка доступа:** `app/deps.py` → `require_capability(key)` → 403 если capability недоступна.

### Политика product_mode

| product_mode | SKU по умолчанию | Кто получает |
|--------------|------------------|--------------|
| `NULL` | FULL | Существующие пользователи (обратная совместимость) |
| `"free"` | FREE | Новые самoregистрации без инвайта |
| `"full"` | FULL | Назначение через Sale Manager |
| `"train"` | TRAIN_RU | Train-only клиенты |

> Новые регистрации в `auth.py` / `google_oauth.py` должны получать `product_mode="free"`, не NULL.

### Новые роутеры (коммит `05f447d`)

| Роутер | Назначение |
|--------|------------|
| `app/routers/training_catalog.py` | Каталог тренировок свободного выбора |
| `app/routers/training_program.py` | Программа/стрик тренировок команды |
| `app/routers/train_report.py` | Упрощённый отчёт РОПу |

### Новые сервисы

| Сервис | Назначение |
|--------|------------|
| `app/services/curriculum_service.py` | Учебная программа, дни, прогресс |
| `app/services/capability_service.py` | SKU → capabilities, проверки |

### Train-фронтенд

**Layout:** `app/templates/train/_layout.html`  
**Страницы:**
- `train/dashboard.html` — дашборд train-режима
- `train/catalog.html` — каталог тренировок
- `train/team_program.html` — программа команды
- `train/rop_report.html` — отчёт РОПу

**Brand middleware:** `app/main.py` → `BrandMiddleware` определяет бренд (`train`/`full`) по hostname; локально через `?brand=`.

### Sale Manager
- `app/routers/sales.py` — расширен для назначения SKU/product_mode
- `app/templates/sales/users.html` — UI управления пользователями
- `app/templates/free_activation.html` — активация free → full/train

### Документация train-режима

| Файл | Описание |
|------|----------|
| `docs/training-mode-explained.html` | Объяснение режима «только тренировки» (HTML, ~30 KB) |
| `docs/training-only-mode-strategies.html` | Стратегии train-only продукта |
| `docs/generate-training-mode-pdf.mjs` | Скрипт генерации PDF из HTML |
| `docs/generate-training-strategies-pdf.mjs` | PDF для strategies |
| `docs/upstat-workflow-schemes/` | Mermaid/HTML схемы workflow UpStat |

---

## 2. Карта проекта (PLATFORM_MAP)

**Новый файл:** [`PLATFORM_MAP.md`](../../PLATFORM_MAP.md) (корень репозитория)

Навигатор по кодовой базе (~600 строк):
- Архитектура (FastAPI + Jinja2 + PostgreSQL)
- Точки входа (`main.py`, `app/main.py`)
- Ядро: `database.py`, `deps.py`, `security.py`, `models.py`
- Все роутеры с описанием «когда трогать»
- Сервисы (pipeline, CRM, analytics, training, capabilities)
- Голосовой ИИ (`voice_assistant/`, `ai_agent_service/`)
- Фронтенд (templates / static)
- Режимы продукта (full / train / free)

**Назначение:** быстро найти файл для точечного изменения без обхода всего репозитория.

---

## 3. Реорганизация документации

### Перенос в `docs/guides/`

Из корня репозитория в `docs/guides/` перемещены **~50 markdown-файлов**:

- Руководства: `ADMIN_GUIDE.md`, `CRM_INTEGRATION_GUIDE.md`, `VOICE_TRAINING_GUIDE.md`, …
- Deployment: `DEPLOYMENT_GUIDE_SCALABLE.md`, `DOCKER_DEPLOYMENT.md`, …
- OAuth: `GOOGLE_OAUTH_SETUP.md`, `PRODUCTION_OAUTH_SETUP.md`, …
- Промпты: `TRAINER_PROMPT_TEMPLATE.md`, `STORYTELLER_TRAINER_PROMPT.md`, …
- Диплом: `DIPLOMA_THESIS.md`

**Цель:** очистить корень проекта, оставить только operational-файлы (`Dockerfile`, `docker-compose.yml`, `requirements.txt`, `PLATFORM_MAP.md`, `SECURITY_AUDIT.md`).

### Структура `docs/` после реорганизации

```
docs/
├── changelog/              ← NEW: логи изменений по дням
│   ├── 2026-06-15-summary.md
│   ├── 2026-06-15-ui-redesign.md
│   ├── 2026-06-15-security.md
│   └── 2026-06-15-product-and-platform.md
├── guides/                 ← перенесённые MD из корня
├── upstat-workflow-schemes/
├── training-mode-explained.html
├── training-only-mode-strategies.html
├── generate-training-mode-pdf.mjs
└── package.json            ← для puppeteer/pdf генерации
```

---

## 4. Гигиена репозитория

### Удалено из git-индекса (untracked / deleted)

| Файл | Причина |
|------|---------|
| `app.db`, `app/app.db`, `app/root_app.db` | SQLite БД с данными пользователей |
| `test.db` | Тестовая БД |
| `backup.sql` | Дамп PostgreSQL ~3.7 MB |
| `cookies.txt` | Сессионные cookies |
| `server.log` | Логи с URL (в т.ч. webhook secrets) |
| `Voice-Live-Api-main/.env` | Секреты API |

### Расширен `.gitignore`

```gitignore
cookies.txt
*.bak
.crm_encryption_key
app/.crm_encryption_key
```

(Плюс существующие правила для `*.db`, `.env`, `docker-compose.override.yml`.)

### Docker override

**Файл:** `docker-compose.override.yml`

```yaml
volumes:
  postgres_data:
    external: true
    name: saas_ocenka-main_v36_postgres_data
```

Подключение к существующему volume PostgreSQL из другого проекта (локальная разработка).

---

## 5. Прочие изменения кода (15 июня, uncommitted)

### voice_assistant

**Файл:** `voice_assistant/router.py`

```python
active_training_sessions: dict = {}
```

Объявлено хранилище активных тренировочных сессий — чтобы эндпоинты чтения/удаления возвращали 404, а не `NameError` (создание сессий временно отключено).

IDOR-фиксы в voice training — из коммита `05f447d` (`router_new.py`).

### ai_agent_service

**Файл:** `ai_agent_service/config.py`, `main.py`, `services/zoom_client.py` — правки конфигурации и CORS (см. security-документ).

### Статика (частично)

Изменены, но **не полностью** в рамках UI-редизайна:
- `app/static/css/notifications.css`
- `app/static/css/progress-tracker.css`
- `app/static/css/voice-training.css`
- `app/static/js/voice-training.js`
- `app/static/js/webrtc-meeting.js`

### Admin-шаблоны

Модифицированы (частично icons import, не полный редизайн):
- `admin/prompts.html`, `prompt_create.html`, `prompt_edit.html`, `prompt_versions.html`, `prompt_trainer.html`
- `admin/research_list.html`, `research_view.html` — Research Mode (функциональность, не UI-редизайн)
- `admin/dashboard.html`, `users.html`, `_layout_admin.html`

---

## 6. Research Mode (контекст, без UI-редизайна)

Последние коммиты до train-режима добавили **Research Mode** — CoT-логи AI-анализа для админки:

| Коммит | Описание |
|--------|----------|
| `324a4a6` | Research mode — CoT-логи AI-анализа звонков |
| `da66cac` | Развёрнутый reasoning + HTML-вьюер с карточками |
| `3676cb4` | Полный учёт действий менеджера + reasoning в карточках |

**Файлы:** `app/templates/admin/research_list.html`, `research_view.html`, модель `ResearchLog` в `models.py`.

> UI Research Mode **не входил** в редизайн 15 июня — только функциональные доработки ранее.

---

## 7. Модели БД (новое из train-коммита)

**Файл:** `app/models.py` (+75 строк в коммите)

- `Organization` — организация-клиент (`sku`, `capabilities_override`)
- `TrainingProgram` / `TrainingProgramDay` — программы тренировок команды
- Связь `User.organization_id`

**Миграции:** `app/main.py` → `create_org_capability_schema()` — идемпотентное создание схемы при старте.

---

## 8. Статистика изменений (рабочая копия)

| Метрика | Значение |
|---------|----------|
| Файлов изменено (uncommitted) | ~61 |
| Templates/static | ~47 |
| Строк удалено (в основном docs move + server.log) | ~31 000 |
| Строк добавлено | ~1 500 |
| Новые docs/changelog | 4 файла |

---

## Связанные документы

- [2026-06-15-summary.md](./2026-06-15-summary.md) — общая сводка дня
- [2026-06-15-ui-redesign.md](./2026-06-15-ui-redesign.md) — редизайн
- [2026-06-15-security.md](./2026-06-15-security.md) — безопасность
- [`PLATFORM_MAP.md`](../../PLATFORM_MAP.md) — карта проекта
- [`SECURITY_AUDIT.md`](../../SECURITY_AUDIT.md) — аудит безопасности
