# UpStat Train — полное руководство по платформе «только тренировки»

> Документ для разработки и улучшения train-only ветки продукта.  
> Актуально на: **2026-06-17**. Репозиторий: `UpStat.pro_Local`.

---

## Содержание

1. [Что такое Train-платформа](#1-что-такое-train-платформа)
2. [Архитектура высокого уровня](#2-архитектура-высокого-уровня)
3. [Система capabilities и SKU](#3-система-capabilities-и-sku)
4. [Роли пользователей](#4-роли-пользователей)
5. [Точки входа и маршрутизация](#5-точки-входа-и-маршрутизация)
6. [Frontend: train-layout и страницы](#6-frontend-train-layout-и-страницы)
7. [Train Dashboard (главная)](#7-train-dashboard-главная)
8. [Каталог тренировок](#8-каталог-тренировок)
9. [Голосовой ИИ-тренер (Voice Training)](#9-голосовой-ии-тренер-voice-training)
10. [Многоэтапные тренировки](#10-многоэтапные-тренировки)
11. [AI-валидатор после тренировки](#11-ai-валидатор-после-тренировки)
12. [Команды (Teams)](#12-команды-teams)
13. [Программа тренировок (РОП)](#13-программа-тренировок-роп)
14. [Отчёт команды (train-report)](#14-отчёт-команды-train-report)
15. [Настройки профиля](#15-настройки-профиля)
16. [Модели базы данных](#16-модели-базы-данных)
17. [Сервисный слой: кто за что отвечает](#17-сервисный-слой-кто-за-что-отвечает)
18. [Карта файлов проекта (train-only)](#18-карта-файлов-проекта-train-only)
19. [Переменные окружения](#19-переменные-окружения)
20. [User flows (сценарии)](#20-user-flows-сценарии)
21. [Отличия Train vs Full](#21-отличия-train-vs-full)
22. [Паттерны разработки и guard'ы](#22-паттерны-разработки-и-guards)
23. [Чеклист при доработках train-only](#23-чеклист-при-доработках-train-only)

---

## 1. Что такое Train-платформа

**Train-платформа** — это режим UpStat **без анализа звонков**. Пользователь не загружает аудио, не получает отчёты по CRM, не пользуется чатом анализа. Весь продукт сосредоточен на:

- **Голосовых тренировках** с ИИ-тренером (Azure Voice Live API)
- **Каталоге** готовых этапов продаж
- **Командной работе** (РОП + менеджеры)
- **Программе тренировок** (РОП назначает последовательность этапов)
- **Отчёте по тренировкам** (кто тренировался, score, streak)

### Как система понимает, что пользователь в Train-режиме

Ключевая проверка по всему коду:

```python
from services.capability_service import has_capability

if not has_capability(user, "call_analysis"):
    # → Train-режим
```

Train-пользователь **не имеет** capability `call_analysis`.  
Full-пользователь **имеет** `call_analysis` + остальные full-функции.

---

## 2. Архитектура высокого уровня

```mermaid
flowchart TB
    subgraph Browser["Браузер (Jinja2 + JS)"]
        TL[train/_layout.html]
        TD[train/dashboard.html]
        TC[train/catalog.html]
        VT[voice_training_conference.html]
        VTJS[voice-training.js]
    end

    subgraph FastAPI["FastAPI app/main.py"]
        AUTH[routers/auth.py]
        DASH[routers/dashboard.py]
        CAT[routers/training_catalog.py]
        TEAMS[routers/teams.py]
        PROG[routers/training_program.py]
        REP[routers/train_report.py]
        SET[routers/settings.py]
        VTR[voice_assistant/router_new.py]
    end

    subgraph Services["app/services/"]
        CAP[capability_service.py]
        CUR[curriculum_service.py]
        STG[training_stages_service.py]
        VAL[training_validator_service.py]
    end

    subgraph Voice["voice_assistant/"]
        WSH[websocket_handler.py]
        AZ[azure_voice_live.py]
        SM[session_manager.py]
        DBS[db_service.py]
    end

    subgraph External["Внешние API"]
        AZURE[Azure Voice Live API]
        OPENAI[OpenAI GPT — валидатор]
    end

    subgraph DB["PostgreSQL"]
        MODELS[(models.py)]
    end

    Browser --> FastAPI
    FastAPI --> Services
    VTR --> WSH --> AZ --> AZURE
    WSH --> DBS --> MODELS
    VAL --> OPENAI
    FastAPI --> MODELS
    CAP --> MODELS
```

### Стек

| Слой | Технология |
|------|------------|
| Backend | FastAPI, SQLAlchemy 2.0, Jinja2 |
| БД | PostgreSQL (SQLite запрещён) |
| Сессии | Starlette SessionMiddleware + cookies |
| Голос | WebSocket → Azure Voice Live (realtime) |
| Frontend | Server-rendered HTML + vanilla JS |
| Стили | `app/static/styles.css` + page-specific CSS |

---

## 3. Система capabilities и SKU

**Файл-источник истины:** `app/services/capability_service.py`

### SKU → набор capabilities

| SKU | Capabilities |
|-----|-------------|
| `FULL` | call_analysis, crm, team_analytics, owner_dashboard, progress, voice_training, training_catalog, training_program, train_report |
| `TRAIN_RU` | voice_training, training_catalog, training_program, train_report |
| `TRAIN_GLOBAL` | voice_training, training_catalog, training_program, train_report |
| `FREE` | *(пусто — страница активации)* |

### Ключи capabilities (train-relevant)

| Capability | Назначение | Guard |
|------------|------------|-------|
| `voice_training` | WebSocket голосовые тренировки | `router_new.py` WS endpoint |
| `training_catalog` | Каталог свободного выбора | `training_catalog.py` |
| `training_program` | Программа команды (РОП) | `training_program.py` |
| `train_report` | Отчёт РОПа по тренировкам | `train_report.py` |

### Как определяется SKU пользователя

Приоритет (функция `_resolve_sku`):

1. `user.organization.sku` — если пользователь привязан к организации
2. `user.product_mode` → маппинг: `train` → `TRAIN_RU`, `full` → `FULL`, `free` → `FREE`
3. `NULL product_mode` → **FULL** (обратная совместимость старых пользователей)

Дополнительно: `Organization.capabilities_override` (JSON) может точечно включать/выключать capability поверх SKU.

### Фабрика guard'ов

**Файл:** `app/deps.py`

```python
def require_capability(key: str) -> Callable:
    # Проверяет has_capability(user, key), иначе HTTP 403
```

Используется как `dependencies=[Depends(require_capability("training_catalog"))]` на роутерах.

### Train vs Full в UI

Многие роутеры выбирают шаблон:

```python
if has_capability(user, "call_analysis"):
    return "settings.html"           # full layout
return "train/settings.html"         # train layout
```

Тот же паттерн в `teams.py` → `train/team_manage.html`.

---

## 4. Роли пользователей

| Роль (`User.role`) | В Train-режиме |
|--------------------|----------------|
| `manager` | РОП: команда, программа, отчёт, тренировки |
| `user` / менеджер продаж | Тренировки, каталог, дашборд |
| `admin` | Полный доступ (если SKU позволяет) |
| `sale_manager` | Только `/sales/` (не train UI) |

**РОП** (`role == 'manager'`) в sidebar train-layout видит дополнительно:
- `/teams/{id}/program` — программа тренировок
- `/teams/{id}/train-report` — отчёт команды

---

## 5. Точки входа и маршрутизация

### Запуск приложения

| Файл | Назначение |
|------|------------|
| `main.py` (корень) | Docker entrypoint → uvicorn |
| `app/main.py` | `create_app()`: middleware, роутеры, миграции схемы |
| `run.sh` | Локальный запуск: `uvicorn app.main:app --reload` |

### Порядок регистрации роутеров (train-relevant)

Из `app/main.py`:

```
public → auth → chat* → settings → dashboard → ...
→ teams → training_catalog → training_program → train_report
→ voice_assistant/router_new (prefix /voice-training)
```

`*` chat/chat_trener требуют `call_analysis` — train-пользователь получит 403.

### Auth flow

**Файл:** `app/routers/auth.py`

1. POST `/login` → сессия `user_id` в cookie
2. Redirect → `/dashboard`
3. `/dashboard` сам ветвится:
   - `caps == ∅` → `free_activation.html`
   - `!call_analysis` → `_train_dashboard()` → `train/dashboard.html`
   - иначе → full dashboard

### URL-карта Train-платформы

| URL | Метод | Capability | Шаблон / Handler |
|-----|-------|------------|------------------|
| `/dashboard` | GET | — | `train/dashboard.html` |
| `/trainings/catalog` | GET | training_catalog | `train/catalog.html` |
| `/trainings/catalog/start` | POST | training_catalog | redirect → voice training |
| `/voice-training/training?training_id=N` | GET | voice_training | `voice_training_conference.html` |
| `/voice-training/ws` | WS | voice_training | `websocket_handler.py` |
| `/voice-training/training/complete` | POST | auth session | AI validator |
| `/teams/my` | GET | — | `train/team_manage.html` |
| `/teams/{id}/members` | GET | — | `train/team_members.html` |
| `/teams/{id}/program` | GET/POST | training_program | `train/team_program.html` |
| `/teams/{id}/train-report` | GET | train_report | `train/rop_report.html` |
| `/settings` | GET/POST | — | `train/settings.html` |
| `/logout` | POST | — | redirect `/login` |

### Страницы, которые Train-пользователь **не должен** открывать

| URL | Причина |
|-----|---------|
| `/calls` | `require_capability("call_analysis")` |
| `/chat` | call_analysis guard |
| `/crm` | crm guard |
| `/analytics` | call_analysis / team_analytics |
| `/owner` | owner_dashboard guard |

> **Важно для фронта:** после тренировки redirect идёт на `/dashboard`, не на `/calls` (см. `post_training_url` в `router_new.py`).

---

## 6. Frontend: train-layout и страницы

### Базовый layout

**Файл:** `app/templates/train/_layout.html`

Sidebar (навигация):
- `/dashboard` — Главная
- `/trainings/catalog` — Каталог тренировок
- `/teams/my` — Моя команда
- *(РОП)* `/teams/{id}/program`, `/teams/{id}/train-report`
- `/settings` — Настройки
- POST `/logout` — Выйти

Стили: inline CSS + `/static/styles.css`.

### Train-шаблоны

| Файл | Страница |
|------|----------|
| `train/dashboard.html` | Главная со статистикой |
| `train/catalog.html` | Каталог этапов |
| `train/team_manage.html` | Список команд |
| `train/team_members.html` | Участники + приглашения |
| `train/team_program.html` | Программа РОПа |
| `train/rop_report.html` | Отчёт по команде |
| `train/settings.html` | Настройки профиля |

### Shared partials

| Файл | Содержимое |
|------|------------|
| `partials/settings_body.html` | Формы профиля/пароля/аватара |
| `partials/team_manage_body.html` | Тело страницы команд |
| `partials/team_members_body.html` | Участники (train_mode скрывает analytics-ссылки) |
| `partials/icons.html` | SVG-иконки |

### Голосовая тренировка UI

**Файл:** `app/templates/voice_training_conference.html`

- Extends `_layout_dashboard.html` *(full layout — известный tech debt для train)*
- Data-атрибуты на `.voice-training-container`:
  - `data-user-id`
  - `data-training-id`
  - `data-session-id`
  - `data-post-training-url` → `/dashboard` для train
- Подключает `voice-training.js`, `voice-training.css`

---

## 7. Train Dashboard (главная)

### Backend

**Файл:** `app/routers/dashboard.py` → `_train_dashboard()`

Вызывается когда `not has_capability(user, "call_analysis")`.

### Что считает дашборд

1. **TrainingSession** (status=completed) пользователя
2. **Streak** — дни подряд с тренировкой (`_compute_streak`)
3. **avg_score**, **total_sessions**, **completed_stages**
4. **recent_sessions** — последние 10 сессий
5. **today_training** — из `TrainingProgram` команды:
   - Берёт активную программу команды пользователя
   - По `start_date` + `cycle_days` вычисляет `day_index`
   - Находит `TrainingProgramDay.stage_key` → ссылка на тренировку

### Frontend

**Файл:** `app/templates/train/dashboard.html`

Блоки:
- Карточки статистики (стрик, сессии, score, этапы)
- «Тренировка на сегодня» (из программы РОПа)
- Ссылка на каталог
- Последние тренировки

---

## 8. Каталог тренировок

### Flow (полный)

```mermaid
sequenceDiagram
    participant U as Пользователь
    participant CAT as training_catalog.py
    participant CUR as curriculum_service.py
    participant DB as PostgreSQL
    participant VT as voice-training

    U->>CAT: GET /trainings/catalog
    CAT->>CUR: get_catalog()
    CUR-->>CAT: stages[] с available_levels
    CAT-->>U: train/catalog.html

    U->>CAT: POST /trainings/catalog/start (stage_key, level)
    CAT->>CUR: create_from_catalog(db, user, stage_key, level)
    CUR->>DB: Conversation (stub)
    CUR->>DB: Message (stub, role=bot)
    CUR->>DB: AnalysisTrainingPlan (plan_source=catalog)
    CUR->>DB: Training (status=available)
    CUR-->>CAT: (plan_id, training_id)
    CAT-->>U: 302 /voice-training/training?training_id=N
```

### Backend

| Файл | Функция |
|------|---------|
| `app/routers/training_catalog.py` | HTTP endpoints |
| `app/services/curriculum_service.py` | Логика каталога и создания stub-тренировки |

### Константа этапов (STAGES)

В `curriculum_service.py`:

| key | label | levels |
|-----|-------|--------|
| contact | Вступление в контакт | 1 |
| needs | Работа с потребностями | 1 |
| presentation | Презентация | 1 |
| objections | Работа с возражениями | 1 |
| closing | Завершение сделки | 4 |

### Промпты этапов (файлы)

```
app/static/docs/trainings/
├── contact/stage_1.txt
├── needs/stage_1.txt
├── presentation/stage_1.txt
├── objections/stage_1.txt
└── closing/stage_1.txt … stage_4.txt
```

`get_catalog()` проверяет наличие файлов → `available_levels`.

### Stub-записи в БД (зачем)

`AnalysisTrainingPlan.report_message_id` — NOT NULL FK на `messages`.  
Реального звонка нет → создаётся stub `Conversation` + `Message` с текстом `[catalog-stub] stage=… level=…`.

План помечается `plan_source="catalog"`.

### Training record

```python
Training(
    plan_id=plan.id,
    order=1,
    title=f"{stage_label} — уровень {level}",
    scenario_type=stage_key,  # → используется как Training.stage для многоэтапности
    status="available",
)
```

---

## 9. Голосовой ИИ-тренер (Voice Training)

### Архитектура голосового пайплайна

```mermaid
sequenceDiagram
    participant B as Браузер (voice-training.js)
    participant WS as /voice-training/ws
    participant WH as websocket_handler.py
    participant SM as session_manager.py
    participant AZ as azure_voice_live.py
    participant Azure as Azure Voice Live API
    participant DB as PostgreSQL

    B->>WS: WebSocket connect (cookies + training_id)
    WS->>WS: auth via session cookie
    WS->>WS: has_capability(voice_training)
    WS->>SM: create_session(user_id, training_id)
    SM->>DB: TrainingSession (VoiceTrainingDBService)
    WS->>AZ: connect()
    AZ->>Azure: WSS /voice-live/realtime
    WS->>Azure: session.update (instructions, voice, VAD)
    WS-->>B: {type: connected}
    WS->>Azure: response.create (ИИ начинает первым)

    loop Пока тренировка идёт
        B->>WS: input_audio_buffer.append (PCM16 base64)
        WS->>Azure: forward audio
        Azure-->>WS: speech_started, transcript, audio.delta
        WS-->>B: proxy events
        B->>B: воспроизведение + UI
    end

    B->>WS: POST /training/complete
    WS->>DB: validator + score
```

### Backend файлы

| Файл | Ответственность |
|------|-----------------|
| `voice_assistant/router_new.py` | HTTP + WebSocket endpoints, страница тренировки |
| `voice_assistant/websocket_handler.py` | Основной цикл WS, прокси Azure ↔ клиент |
| `voice_assistant/azure_voice_live.py` | WSS клиент к Azure, session.update, send_audio |
| `voice_assistant/session_manager.py` | In-memory сессии (до 100 concurrent) |
| `voice_assistant/db_service.py` | CRUD TrainingSession, VoiceTrainingMessage |
| `voice_assistant/config.py` | SYSTEM_PROMPT, Azure env vars |

### WebSocket endpoint

**URL:** `ws://host/voice-training/ws?user_id=N&training_id=M&db_session_id=K`

**Аутентификация:**
1. Cookie `session` → `user_id` (приоритет над query param)
2. Проверка `User` в БД
3. `has_capability(user, "voice_training")`

**Handler:** `handle_websocket_connection()` в `websocket_handler.py`

### Session Manager

**Файл:** `voice_assistant/session_manager.py`

- `UserSession` — изолированная in-memory сессия на пользователя
- `SessionManager.create_session()` — один active session на user_id
- При новом подключении закрывает старую (`_close_session_unlocked`)
- Поля `UserSession.stages`, `current_stage_index` — для многоэтапных тренировок

### Azure Voice Live

**Файл:** `voice_assistant/azure_voice_live.py`

Подключение:
```
wss://{endpoint}/voice-live/realtime?api-version=...&model=...&api-key=...
```

`send_session_update()` конфигурирует:
- `instructions` — системный промпт (+ контекст тренировки)
- `voice.name` — neural голос (из `AZURE_VOICE_LIVE_VOICE`)
- `turn_detection.type` — `azure_semantic_vad`
- `input_audio_format` — `pcm16` @ 24kHz
- `input_audio_transcription` — gpt-4o-transcribe
- `tools` — для многоэтапных (complete_stage, complete_training)

### Системный промпт

**Файл:** `voice_assistant/config.py` → `SYSTEM_PROMPT`

Длинный промпт с двумя ролями ИИ:
1. **Тренер-инструктор** — объясняет технику
2. **Тренер-напарник** — ролевая игра (менеджер ↔ клиент)

При старте сессии промпт дополняется контекстом из `Training.recommendation/title` (если нет многоэтапных файлов).

### Frontend: voice-training.js

**Файл:** `app/static/js/voice-training.js`

Класс `VoiceTraining`:

| Этап init | Действие |
|-----------|----------|
| `connectDOMElements()` | Привязка UI |
| `loadHistory()` | GET `/voice-training/training/{id}/history` |
| `connectWebSocket()` | WS connect |
| `requestMicrophoneAccess()` | getUserMedia |
| `autoStartListening()` | После `connected` / `session.created` |

**Аудио-пайплайн:**
1. `AudioWorklet` / ScriptProcessor → float32
2. Конвертация → int16 PCM
3. Base64 → `{type: "input_audio_buffer.append", audio: "..."}`
4. WebSocket → backend → Azure

**Sample rate:** 24000 Hz (константа `this.sampleRate`)

**События от сервера (основные):**

| event type | Действие UI |
|------------|-------------|
| `connected` | Сессия готова, автозапуск мика |
| `session.created` | Azure session ready |
| `response.audio.delta` | Буфер + воспроизведение |
| `response.audio.done` | Конец реплики ИИ |
| `user_text` / `ai_text` | Сообщения в чат |
| `stage_changed` | Смена этапа (UI badge) |
| `training_completed` | Все этапы пройдены |
| `error` | Уведомление |

**Завершение:**
- `confirmStopTraining()` / `handleTrainingCompleted()`
- POST `/voice-training/training/complete` с transcript
- Overlay с результатом валидации
- Redirect → `postTrainingUrl` (`/dashboard` для train)

### HTTP endpoints voice training

| Endpoint | Назначение |
|----------|------------|
| GET `/voice-training/training` | HTML страница |
| GET `/voice-training/training/{id}/history` | История сообщений из БД |
| POST `/voice-training/training/complete` | Завершение + валидатор |
| GET `/voice-training/stats` | Статистика session manager |
| WS `/voice-training/ws` | Голосовой канал |

---

## 10. Многоэтапные тренировки

### Когда включаются

Если `Training.stage` (или `scenario_type`) имеет папку:

```
app/static/docs/trainings/{stage}/stage_1.txt
app/static/docs/trainings/{stage}/stage_2.txt
...
```

**Сервис:** `app/services/training_stages_service.py`

### TrainingStage dataclass

```python
@dataclass
class TrainingStage:
    number: int
    prompt: str
    ai_role: str              # "Тренер-инструктор", "Клиент", ...
    ai_role_description: str
    training_type: str
    first_line_template: str  # Первая реплика этапа
```

Парсинг метаданных из блока `ИНФОРМАЦИЯ ОБ ЭТАПЕ:` в txt-файле.

### Переход между этапами

Два механизма (скрытые, не озвучиваются):

1. **Tool calls:** `complete_stage`, `complete_training` (Azure function calling)
2. **Теги в тексте:** `[STAGE_COMPLETE]`, `[TRAINING_COMPLETE]` (fallback)

**Обработчик:** `_handle_stage_action()` в `websocket_handler.py`

После `response.audio.done`:
- `next_stage` → `session.update` с новым промптом + `response.create`
- `complete_training` → event `training_completed` клиенту

### Пример: closing

4 файла `stage_1.txt` … `stage_4.txt` → 4-этапная тренировка по завершению сделки.

---

## 11. AI-валидатор после тренировки

### Когда запускается

POST `/voice-training/training/complete` → `router_new.complete_training()`

Если `training_id` валидный → `TrainingValidatorService.validate_and_complete_training()`.

### Сервис

**Файл:** `app/services/training_validator_service.py`

- Отправляет транскрипт + промпт тренировки в **OpenAI GPT**
- Критерии (0–100):
  - full_cycle (0–25)
  - understanding (0–25)
  - execution_quality (0–25)
  - active_participation (0–25)
- **passed** если score ≥ 70

### Что сохраняется

В `TrainingSession`:
- `score`, `feedback`, `status=completed`
- `completed_at`, `duration_seconds`
- `transcript`

В `Training` (если passed):
- `status=completed`, `best_score`, `attempts++`

В `AnalysisTrainingPlan`:
- `completed_trainings++`, возможно `status=completed`

### Frontend результат

`showValidationResult()` / `_showSimpleCompletionMessage()` в `voice-training.js`  
Кнопка «Продолжить» → `/dashboard` (train).

---

## 12. Команды (Teams)

### Backend

**Файл:** `app/routers/teams.py`

Train-aware helpers:
- `_train_mode(user)` → `not has_capability(user, "call_analysis")`
- `_teams_template(user, page)` → `train/{page}` или `{page}`
- `_user_for_layout()` → eager load `managed_teams` для sidebar РОПа

### Endpoints

| URL | Действие |
|-----|----------|
| GET `/teams/my` | Список команд (менеджер + участник) |
| POST `/teams` | Создать команду (РОП) |
| GET `/teams/{id}/members` | Участники + приглашения |
| POST `/teams/{id}/invitations` | Email-приглашения |

### Приглашения (train-specific)

При отправке invite для train-команды используется `TRAIN_PUBLIC_URL` или `PUBLIC_APP_URL` для ссылки регистрации.

**Сервис:** `app/services/team_invitations.py`

### Модели

- `Team` — `manager_id`, `organization_id`
- `TeamMember` — `user_id`, `role_in_team`
- `TeamInvitation` — email, token, status

---

## 13. Программа тренировок (РОП)

### Назначение

РОП задаёт **цикл этапов** для команды. Менеджеры на дашборде видят «Тренировку на сегодня».

### Backend

**Файл:** `app/routers/training_program.py`

| Endpoint | Действие |
|----------|----------|
| GET `/teams/{id}/program` | Форма программы |
| POST `/teams/{id}/program` | Сохранить программу |

### Модели

- `TrainingProgram` — team_id, start_date, cycle_days, is_active
- `TrainingProgramDay` — day_index, stage_key

### Логика «тренировка на сегодня»

В `_train_dashboard()`:
```python
day_idx = (today - start).days % max(1, prog.cycle_days)
prog_day = TrainingProgramDay where day_index == day_idx
```

---

## 14. Отчёт команды (train-report)

### Backend

**Файл:** `app/routers/train_report.py`  
**Capability:** `train_report`  
**URL:** GET `/teams/{id}/train-report`

### Метрики по каждому участнику

| Поле | Описание |
|------|----------|
| trained_today | Была ли сессия сегодня |
| today_score | Средний score за сегодня |
| avg_score | Средний score за всё время |
| streak | Дни подряд с тренировкой |
| total_sessions | Всего завершённых сессий |
| last_stage | Последний этап продаж |

**Шаблон:** `train/rop_report.html`

---

## 15. Настройки профиля

**Файл:** `app/routers/settings.py`

```python
def _settings_template(user):
    if has_capability(user, "call_analysis"):
        return "settings.html"        # full layout
    return "train/settings.html"
```

Endpoints:
- GET `/settings`
- POST `/settings/profile`
- POST `/settings/password`
- POST `/settings/avatar`

Контент: `partials/settings_body.html` (без блока «Интеграции» в train — `show_integrations=False`).

---

## 16. Модели базы данных

### ER-диаграмма (train core)

```mermaid
erDiagram
    User ||--o{ AnalysisTrainingPlan : has
    User ||--o{ TrainingSession : has
    User ||--o{ TeamMember : belongs
    Team ||--o{ TeamMember : has
    Team ||--o{ TrainingProgram : has
    AnalysisTrainingPlan ||--|{ Training : contains
    Training ||--o{ TrainingSession : has
    TrainingSession ||--o{ VoiceTrainingMessage : has
    TrainingProgram ||--|{ TrainingProgramDay : has
    Organization ||--o{ User : has
    Organization ||--o{ Team : has

    User {
        int id
        string role
        string product_mode
        int organization_id
    }
    AnalysisTrainingPlan {
        int id
        string plan_source
        string status
    }
    Training {
        int id
        string stage
        string scenario_type
        string status
    }
    TrainingSession {
        int id
        int score
        string status
        string websocket_session_id
    }
```

### Ключевые таблицы

| Модель | Таблица | Train-роль |
|--------|---------|------------|
| `AnalysisTrainingPlan` | analysis_training_plans | Контейнер тренировок (catalog/analysis/program) |
| `Training` | trainings | Одна тренировка (этап) |
| `TrainingSession` | training_sessions | Попытка прохождения (voice) |
| `VoiceTrainingMessage` | voice_training_messages | Реплики user/assistant |
| `TrainingProgram` | training_programs | Программа РОПа |
| `TrainingProgramDay` | training_program_days | День цикла программы |
| `Team` | teams | Команда |
| `TeamMember` | team_members | Участник |
| `Organization` | organizations | SKU / capabilities |

### plan_source значения

| Значение | Откуда создан |
|----------|---------------|
| `analysis` | Из анализа звонка (Full only) |
| `catalog` | Из каталога train |
| `program` | Из программы РОПа |

---

## 17. Сервисный слой: кто за что отвечает

| Сервис | Файл | Функции |
|--------|------|---------|
| Capabilities | `capability_service.py` | `get_capabilities`, `has_capability`, `_resolve_sku` |
| Каталог | `curriculum_service.py` | `get_catalog`, `create_from_catalog` |
| Этапы промптов | `training_stages_service.py` | `load_stages`, `build_stage_tools`, `strip_tags` |
| AI-валидатор | `training_validator_service.py` | `validate_training`, `validate_and_complete_training` |
| Команды | `team_access.py` | `get_manager_teams`, `assert_can_manage_team` |
| Приглашения | `team_invitations.py` | `create_invitations`, `accept_invitation` |
| PII redaction | `pii_redactor.py` | `redact_pii` в транскриптах |
| Voice DB | `voice_assistant/db_service.py` | CRUD sessions/messages |

### Связи между сервисами при старте тренировки из каталога

```
training_catalog.catalog_start()
  → curriculum_service.create_from_catalog()
    → models: Conversation, Message, AnalysisTrainingPlan, Training
  → Redirect /voice-training/training?training_id=X

voice-training page load
  → router_new.training_page()
  → voice_training_conference.html

VoiceTraining.init() [JS]
  → connectWebSocket()
  → router_new.websocket_training_endpoint()
    → websocket_handler.handle_websocket_connection()
      → session_manager.create_session()
      → db_service.create_training_session()
      → azure_voice_live.connect()
      → azure send_session_update(SYSTEM_PROMPT + training context)
      → azure send_response_create() [ИИ начинает]
```

---

## 18. Карта файлов проекта (train-only)

```
UpStat.pro_Local/
├── app/
│   ├── main.py                    # create_app(), роутеры, voice_assistant mount
│   ├── database.py                # PostgreSQL engine
│   ├── deps.py                    # require_user, require_capability
│   ├── models.py                  # ORM модели
│   ├── routers/
│   │   ├── auth.py                # login → /dashboard
│   │   ├── dashboard.py           # _train_dashboard()
│   │   ├── training_catalog.py    # /trainings/catalog*
│   │   ├── training_program.py    # /teams/{id}/program
│   │   ├── train_report.py        # /teams/{id}/train-report
│   │   ├── teams.py               # /teams/my (train templates)
│   │   └── settings.py            # train/settings.html
│   ├── services/
│   │   ├── capability_service.py  # ★ SKU / capabilities
│   │   ├── curriculum_service.py  # ★ каталог
│   │   ├── training_stages_service.py  # ★ многоэтапность
│   │   └── training_validator_service.py  # ★ AI validator
│   ├── templates/
│   │   ├── train/                 # ★ все train-страницы
│   │   ├── partials/              # shared body partials
│   │   └── voice_training_conference.html  # UI тренировки
│   └── static/
│       ├── js/voice-training.js   # ★ голосовой клиент
│       ├── js/audio-processor.js  # AudioWorklet
│       ├── css/voice-training.css
│       └── docs/trainings/        # ★ промпты этапов (.txt)
├── voice_assistant/
│   ├── router_new.py              # ★ /voice-training/*
│   ├── websocket_handler.py       # ★ WS proxy Azure
│   ├── azure_voice_live.py        # ★ Azure WSS client
│   ├── session_manager.py         # ★ in-memory sessions
│   ├── db_service.py              # ★ persist sessions
│   └── config.py                  # SYSTEM_PROMPT, Azure config
├── docker-compose.yml             # postgres, redis, backend
├── .env                           # секреты и Azure keys
└── Claude_GROWUP/                 # ★ эта документация
    └── TRAIN_PLATFORM_GUIDE.md
```

---

## 19. Переменные окружения

### Обязательные для train

| Variable | Назначение |
|----------|------------|
| `DATABASE_URL` | PostgreSQL connection string |
| `SECRET_KEY` | Session cookies + WS auth |
| `USE_AZURE_VOICE_LIVE` | `true` — голос через Azure |
| `AZURE_VOICE_LIVE_ENDPOINT` | URL Azure ресурса |
| `AZURE_VOICE_LIVE_API_KEY` | API key |
| `AZURE_VOICE_LIVE_MODEL` | e.g. `gpt-realtime` |
| `AZURE_VOICE_LIVE_VOICE` | e.g. `en-US-JennyMultilingualNeural` |
| `AZURE_VOICE_LIVE_API_VERSION` | e.g. `2025-05-01-preview` |
| `OPENAI_API_KEY` | AI-валидатор после тренировки |

### Опциональные

| Variable | Назначение |
|----------|------------|
| `AZURE_VOICE_LIVE_TRANSCRIPTION_MODEL` | default: gpt-4o-transcribe |
| `AZURE_VOICE_LIVE_TRANSCRIPTION_LANGUAGE` | `ru-RU` или пусто = auto |
| `PUBLIC_APP_URL` | Базовый URL для invite links |
| `TRAIN_PUBLIC_URL` | URL train-домена для invite |
| `REDIS_URL` | Кэш (notifications) |
| `SYSTEM_PROMPT_EN` | EN-версия системного промпта. **Строгий gate:** если не задана — для всех локалей используется RU-промпт (поведение ИИ не меняется). См. §24 (i18n). |
| `OPENAI_VALIDATOR_MODEL` | Модель AI-валидатора (default — см. код) |
| `ALLOW_QUERY_USER_ID` | `false` в проде: запрещает WS-аутентификацию через query-param `user_id` |
| `SENTRY_DSN` | Включает отправку ошибок в Sentry |

### Не нужны для train-only

`AMOCRM_*`, `BITRIX24_*`, `ZOOM_*`, `GOOGLE_*` (если не используете OAuth).

---

## 20. User flows (сценарии)

### A. Менеджер: тренировка из каталога

1. Login → `/dashboard` (train)
2. «Каталог тренировок» → `/trainings/catalog`
3. Выбрать этап → POST `/trainings/catalog/start`
4. Redirect → `/voice-training/training?training_id=N`
5. Разрешить микрофон → WS connect → ИИ приветствует
6. Диалог голосом → завершение
7. AI-валидатор → overlay с score
8. «Продолжить» → `/dashboard`

### B. РОП: программа + отчёт

1. «Моя команда» → `/teams/my`
2. «Участники» → пригласить email
3. «Программа» → `/teams/{id}/program` → задать цикл этапов
4. Менеджеры видят «тренировку на сегодня» на дашборде
5. «Отчёт команды» → `/teams/{id}/train-report`

### C. FREE пользователь (без SKU)

1. Login → `/dashboard`
2. `get_capabilities()` → пусто
3. `free_activation.html` — просьба связаться с РОП / Sale Manager

---

## 21. Отличия Train vs Full

| Аспект | Train | Full |
|--------|-------|------|
| Capability `call_analysis` | ❌ | ✅ |
| Layout | `train/_layout.html` | `_layout_dashboard.html` |
| Dashboard | `_train_dashboard` | analyses, CRM, calls |
| После тренировки | `/dashboard` | `/calls` |
| Анализ звонков | — | pipeline.py |
| CRM | — | crm_integration.py |
| Чат анализа | — | chat.py |
| Команды: analytics link | скрыт | `/teams/{id}/analytics` |
| План тренировок из звонка | — | analysis → plan |

### Как не сломать Full при правках Train

1. Всегда проверять `has_capability(user, "call_analysis")` перед выбором layout/redirect
2. Не удалять guard'ы с роутеров full-функций
3. Использовать `train/` шаблоны только при `not call_analysis`
4. JS redirect: `window.postTrainingUrl` / `this.postTrainingUrl`

---

## 22. Паттерны разработки и guard'ы

### 1. Capability guard на роутере

```python
router = APIRouter(
    dependencies=[Depends(require_capability("training_catalog"))]
)
```

### 2. Template branching

```python
return templates.TemplateResponse(
    _teams_template(user, "team_manage.html"),
    {"train_mode": _train_mode(user), ...}
)
```

### 3. Dashboard branching

```python
if not has_capability(user, "call_analysis"):
    return _train_dashboard(request, db, user)
```

### 4. POST vs GET для logout

```html
<form action="/logout" method="post">  <!-- ✅ -->
<a href="/logout">                   <!-- ❌ 405 -->
```

### 5. Teams URL

```html
<a href="/teams/my">  <!-- ✅ -->
<a href="/teams">     <!-- ❌ 405 (POST only for create) -->
```

### 6. Message model field

```python
Message(..., text="...", role="bot")  # ✅
Message(..., content="...")           # ❌ invalid keyword
```

---

## 23. Чеклист при доработках train-only

- [ ] Новый URL защищён `require_capability(...)` ?
- [ ] Шаблон использует `train/_layout.html` для train users?
- [ ] Redirect не ведёт на `/calls`, `/chat`, `/crm`?
- [ ] Sidebar train/_layout.html обновлён если добавлен новый раздел?
- [ ] `managed_teams` загружен для РОП sidebar?
- [ ] Голос: `USE_AZURE_VOICE_LIVE=true` и ключи в `.env` / docker-compose?
- [ ] Stub Message использует `text=`, не `content=`?
- [ ] Session manager: не вызывать `close_session()` изнутри `create_session` lock (deadlock)?
- [ ] После тренировки: `post_training_url = "/dashboard"` для train?
- [ ] Промпт этапа лежит в `app/static/docs/trainings/{stage}/stage_N.txt`?

---

## 24. Production Readiness / Roadmap

Раздел отражает работу по доведению train-платформы до прод-готовности.
Статусы: ✅ сделано · 🟡 частично · ⬜ запланировано.

### Статус по фазам

**P0 — блокеры безопасности и масштаба**

| # | Задача | Статус |
|---|--------|--------|
| 1 | Секреты вынесены в env, без хардкода/dev-fallback | 🟡 |
| 2 | `NULL product_mode → FREE` (fail-closed, не FULL) | ✅ |
| 3 | Sticky-WS (nginx), Redis для rate-limit + ownership, фоновый cleanup | 🟡 |
| 4 | Воркеры, `GET /health` + `/ready`, graceful shutdown | ⬜ |
| 5 | WS-безопасность: `ALLOW_QUERY_USER_ID=false`, cookie `max_age`, лимиты аудио/сессии | 🟡 |
| 6 | 500-handler не отдаёт `str(exc)` клиенту (generic + `request_id`) | ✅ |
| 7 | Прод-гигиена compose (нет dev bind-mounts, pgAdmin) | ⬜ |

**P1 — надёжность, наблюдаемость, данные**

| # | Задача | Статус |
|---|--------|--------|
| 8 | Структурное логирование (JSON) + `request_id` + Sentry | 🟡 |
| 9 | Alembic — единый источник схемы, без runtime-патчей на каждом воркере | 🟡 |
| 10 | AI-валидатор: retry с backoff, не «фейлит» при транзиентной ошибке (`ValidationTransientError`) | ✅ |
| 10b | Учёт usage-токенов + per-user/org квота | ⬜ |
| 11 | Бэкапы `pg_dump` + ретеншен 30д, runbook restore | ✅ |
| 12 | CI/CD (ruff, bandit, detect-secrets, pytest), `tests/` | ✅ |

**P2 — UI/UX и i18n**

| # | Задача | Статус |
|---|--------|--------|
| 13 | Layout голосовой страницы по capability (Full↔Train), без inline-хаков | ✅ |
| 14 | Обработка ошибок голосового UI (микрофон, обрыв WS, таймаут) | ⬜ |
| 15 | a11y: aria-label/aria-current/focus-visible в train-шаблонах | ✅ |
| 16 | Мобильная навигация (гамбургер) + dark-mode через CSS-переменные | ✅ |
| 17 | Клиентская валидация форм (пароль/телефон/программа) | ✅ |
| 18 | i18n-каркас (EN для `TRAIN_GLOBAL`), строгий gate AI-промптов | ✅ |
| 19 | Этот раздел | ✅ |

### Чеклист деплоя

1. **Секреты:** все значения в окружении (не в git). `SECRET_KEY` ≥ 32 символов, без dev-fallback.
2. **Миграции:** `alembic upgrade head` — **один раз** на релиз, не на каждом воркере.
3. **Health:** `GET /health` (liveness) и `GET /ready` (БД+Redis) возвращают 200.
4. **Sticky-WS:** nginx `ip_hash`/cookie для апстрима — обязательно при N воркерах (живой WS к Azure нельзя мигрировать между процессами).
5. **Бэкап:** `scripts/backup_postgres.sh` в cron; проверить восстановление `restore_postgres.sh` (DRY_RUN=1).
6. **`ALLOW_QUERY_USER_ID=false`** в проде.
7. **Capability fail-closed:** убедиться, что `NULL product_mode` даёт FREE, не FULL.

### Секреты и ротация

История git была скомпрометирована — перечисленные секреты **ротированы** (удаления из файла недостаточно).

| Секрет | Действие при ротации |
|--------|----------------------|
| `SECRET_KEY` | Перевыпуск → инвалидирует все сессии (повторный логин) |
| `CRM_ENCRYPTION_KEY` | Перевыпуск + ре-шифрование существующих CRM-токенов |
| `SMTP_USER` / `SMTP_PASSWORD` | Сменить пароль Gmail-аккаунта и app-password |
| `OPENAI_API_KEY` / `AZURE_VOICE_LIVE_API_KEY` | Перевыпуск в провайдере, обновить env |
| `POSTGRES_PASSWORD` | Сменить + обновить `DATABASE_URL` |

Процедуры — в `docs/runbook.md`.

### i18n (локализация)

- **Локаль по SKU:** `TRAIN_GLOBAL → en`, всё остальное (`TRAIN_RU`/`FULL`/`FREE`/`NULL`) → `ru` (fail-safe). См. `app/services/i18n_service.py::resolve_locale`.
- **Исходный язык — RU.** Строки в шаблонах остаются русскими и являются ключами перевода. Для `ru` перевод 1:1, поэтому существующие пользователи не видят изменений. Для `en` строка ищется в `app/i18n/en.json`; отсутствующий перевод безопасно фоллбэчит на исходную строку.
- **В шаблонах:** `{{ _('строка') }}` (context-aware, берёт локаль из `user` в контексте), фильтр дат `{{ d | localdate }}`.
- **Строгий gate AI-поведения:** системный промпт (`config.get_system_prompt(locale)`) и этапные `.txt` (`load_stages(stage, locale)`) переключаются на EN **только если EN-контент явно создан** (`SYSTEM_PROMPT_EN` задан / существует папка `trainings/{stage}/en/`). Иначе используется текущий RU-контент без изменений. Покрыто тестами `tests/test_prompt_locale_gate.py`.

### Тесты

`tests/` (pytest): `test_capability_service`, `test_session_manager`, `test_training_validator`, `test_i18n_service`, `test_prompt_locale_gate`. Запуск: `pytest tests/ -v`. Прогоняются в CI (`.github/workflows/ci.yml`).

---

## Приложение A: HTTP-коды ошибок train

| Ситуация | Ответ |
|----------|-------|
| Нет capability | 403 `{"detail":"Capability 'X' required"}` |
| GET на POST-only route | 405 Method Not Allowed |
| Не авторизован | 302 → `/login` |
| WS без session cookie | close 1008 Unauthorized |

---

## Приложение B: Логи для отладки голоса

```bash
docker logs -f upstatpro_local-backend-1 2>&1 | grep -iE 'voice|azure|websocket|session'
```

Ожидаемая цепочка:
```
🔐 Аутентификация успешна: user_id=N, training_id=M
✅ Создана сессия ...
🔌 Подключение к Azure Voice Live ...
✅ Azure Voice Live подключен успешно
✅ Конфигурация сессии отправлена в Azure
✅ Подтверждение подключения отправлено клиенту
🎙️ Запрошен стартовый ответ ИИ
📨 Получено событие от Azure: session.created
📨 Получено событие от Azure: response.audio.delta
🎤 Обнаружена речь пользователя
```

---

## Приложение C: Локальный запуск (train dev)

```bash
# 1. Docker: postgres + redis + backend
docker compose up -d postgres redis backend

# 2. Или локально (нужен Python 3.11)
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql://saas_user:PASSWORD@localhost:5432/saas
./run.sh

# 3. Открыть
open http://localhost:8000/dashboard
```

---

*Документ подготовлен для команды разработки и Claude Code. При изменении архитектуры train-only — обновляйте этот файл.*
