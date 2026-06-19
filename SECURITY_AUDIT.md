# SECURITY_AUDIT — аудит безопасности UpStat

Дата: 2026-06-15. Метод: ручной статический анализ кода (read-only), без эксплуатации. Покрытие: аутентификация/сессии/доступ, инъекции, загрузки/SSRF, секреты/конфигурация.

> Обновлено 2026-06-15: код-фиксы по всем 13 находкам внесены. Остаются **ops-действия** (см. ниже). Приоритеты: **P0** — немедленно, **P1** — спринт, **P2** — харденинг.

## Статус исправлений (что сделано в коде)

| # | Находка | Статус |
|---|---------|--------|
| 1 | Секреты/БД в git | ✅ untrack (`git rm --cached`) + `.gitignore`. ⚠️ ОСТАЁТСЯ ops: **закоммитить**, **ротация секретов**, **чистка истории** (BFG/filter-repo) |
| 2 | Код верификации `random` | ✅ заменён на `secrets.randbelow` (`auth.py`) |
| 3 | CORS `*`+credentials | ✅ origins из `CORS_ALLOW_ORIGINS` (`ai_agent_service/main.py`) |
| 4 | XSS innerHTML | ✅ `escapeHtml`/`safeUrl` в рендере уведомлений (`_layout_dashboard.html`); `notifications.js` уже экранировал |
| 5 | Cookie не Secure | ✅ Secure по умолчанию в prod + `SESSION_MAX_AGE` (`main.py`) |
| 6 | Нет CSRF | ✅ middleware проверки Origin/Referer на изменяющих запросах (`main.py`); проверено: кросс-домен POST → 403 |
| 7 | Webhook secret | ✅ `secrets.compare_digest` + редакция секрета/query в логах (`crm_integration.py`, `main.py`) |
| 8 | Rate limit | ✅ account-lockout на логин (10 неудач/15 мин, общий через Redis при `REDIS_URL`); verify уже имел лимит 5 |
| 9 | CRM ключ | ✅ код уже читает `CRM_ENCRYPTION_KEY` из env; файл в `.gitignore`. ⚠️ ops: задать env в проде |
| 10 | Лимит попыток кода | ✅ уже был (5 попыток, TTL 10 мин) — подтверждено |
| 11 | DEV-код | ✅ в production никогда не показывается (`_is_dev_mode`) |
| 12 | Расширения загрузки | ✅ MIME/magic уже валидировались; добавлена санитизация ext (`chat.py`) |
| 13 | IDOR | ✅ проверено — все эндпоинты (training/meetings/notifications/crm/research/team) корректно скоупят по владельцу/команде; уязвимостей не найдено, правки не требуются |

### Остаётся выполнить вручную (ops, не код)
- **Закоммитить** untrack секретов и правки.
- **Ротировать** все секреты, которые были в репозитории (БД-креды, `SECRET_KEY`, OpenAI/ElevenLabs/Azure/Zoom/Google OAuth, CRM-токены, `.crm_encryption_key`).
- **Очистить историю git** (BFG/`git filter-repo`) + форс-пуш (предупредить команду).
- В проде задать env: `ENVIRONMENT=production`, `HTTPS_ONLY=true` (или оставить дефолт), `CRM_ENCRYPTION_KEY`, при необходимости `CORS_ALLOW_ORIGINS`, `REDIS_URL` (для общего rate-limit/lockout между воркерами).

---

> Исходный план (ниже) оставлен для контекста.

## Сводка находок

| # | Находка | Severity | Файл |
|---|---------|----------|------|
| 1 | Базы данных и секреты закоммичены в git | **Critical** | `app.db`, `backup.sql`, `Voice-Live-Api-main/.env`, `cookies.txt`, `server.log` |
| 2 | Код верификации e-mail генерится небезопасным `random` | **High** | `app/routers/auth.py:271,439` |
| 3 | CORS `*` + credentials в AI-сервисе | **High** | `ai_agent_service/main.py:32-33` |
| 4 | CSP с `unsafe-inline` + рендер данных через `innerHTML` (stored XSS) | **High** | `app/security.py:178`, `app/templates/_layout_dashboard.html` |
| 5 | Сессионная cookie не Secure по умолчанию | **Medium** | `app/main.py:582,600-605` |
| 6 | Нет CSRF-защиты на POST-формах/запросах | **Medium** | `app/security.py` (функции есть, не применяются) |
| 7 | Секрет CRM-webhook: сравнение `!=` (timing) + секрет в URL (попадает в логи) | **Medium** | `app/routers/crm_integration.py:730` |
| 8 | Rate limit in-memory: не общий между воркерами, обходится сменой IP | **Medium** | `app/middleware/rate_limit.py` |
| 9 | Ключ шифрования CRM-токенов лежит файлом рядом с кодом | **Medium** | `app/.crm_encryption_key`, `app/services/crm_service.py` |
| 10 | Нет ограничения попыток ввода кода верификации | **Medium** | `app/routers/auth.py` |
| 11 | DEV-показ кода верификации (под env-флагом) | **Low** | `app/routers/auth.py:30` |
| 12 | Расширение загружаемого файла не проверяется по allowlist | **Low** | `app/routers/chat.py:325` |
| 13 | IDOR на ряде эндпоинтов — требует проверки | **Medium (?)** | см. раздел |

## Что проверено и оказалось ОК (важно)
- **Command injection:** `ffmpeg` вызывается списком аргументов без `shell=True` (`app/services/pipeline.py:50-52`, `pipeline_trener.py:78`) — шелл-инъекция невозможна.
- **AI-аналитика НЕ выполняет сгенерированный LLM SQL:** `analytics_assistant.py`/`analytics_queries.py` строят запросы через ORM (`db.query(...)`), LLM получает уже агрегированный контекст. Прямого «AI→SQL→БД» нет.
- **Доступ к вложениям/экспорту:** `GET /attachments/{id}` и `/chat/export/by-report/{id}` проверяют владельца (`conv.user_id != user.id → 403`) — `app/routers/chat.py:594-605, 607+`.
- **Имена файлов на диске:** генерируются `uuid4().hex + ext`, не из имени пользователя (`app/routers/chat.py:324-325`) — path traversal/перезапись исключены.
- **SECRET_KEY:** обязателен, минимум 32 символа, без небезопасного дефолта (`app/main.py:541-549`).
- **Токен сброса пароля и OAuth-state:** `secrets.token_urlsafe(48/32)` — криптостойкие (`app/routers/auth.py:485,620`).
- **Security-headers middleware** применяется глобально (`app/main.py:586-593`); у основного приложения нет CORS `*`.
- **Хардкод-секретов в `*.py`** не найдено (используется `os.getenv`).

---

## Детальные находки и исправления

### 1. [Critical] БД и секреты в git-репозитории
**Где:** `git ls-files` показывает отслеживаемые: `app.db`, `app/app.db`, `app/app/app.db`, `app/root_app.db`, `test.db`, `backup.sql` (дамп ~3.7MB с данными), `cookies.txt`, `server.log`, `Voice-Live-Api-main/.env`.
**Риск:** любой, у кого есть доступ к репозиторию (или к его истории/форку/CI), получает реальные данные пользователей и секреты. `.gitignore` их уже перечисляет, но файлы были закоммичены раньше и остаются в индексе и истории.
**Исправление (P0):**
1. `git rm --cached app.db app/app.db app/app/app.db app/root_app.db test.db backup.sql cookies.txt server.log Voice-Live-Api-main/.env` и закоммитить.
2. **Ротировать ВСЕ секреты**, которые могли утечь: пароль/креды БД, `SECRET_KEY`, OpenAI/ElevenLabs/Azure/Zoom/Google OAuth ключи, токены CRM, `.crm_encryption_key`.
3. Вычистить историю: BFG Repo-Cleaner или `git filter-repo` (затем форс-пуш, предупредить команду).
4. Убедиться, что `.gitignore` покрывает все эти пути (уже частично есть), добавить `*.bak`, `.crm_encryption_key`.

### 2. [High] Небезопасная генерация кода верификации
**Где:** `app/routers/auth.py:271` и `:439` — `code = str(random.randint(100000, 999999))`.
**Риск:** `random` (Mersenne Twister) предсказуем; 6-значный код в сочетании со слабым rate limit и без лимита попыток → подбор/предсказание кода → захват аккаунта при регистрации/верификации.
**Исправление (P0/P1):** заменить на `secrets.randbelow(900000)+100000`; ограничить число попыток ввода (например, 5, затем блок/новый код); срок жизни кода ≤10 мин; привязать счётчик попыток к записи.

### 3. [High] CORS `*` + credentials в ai_agent_service
**Где:** `ai_agent_service/main.py:32-33` — `allow_origins=["*"]`, `allow_credentials=True`.
**Риск:** если микросервис доступен из браузера/сети — ослабление политики источников (и потенциальная утечка ответов с кредами). Комментарий «в продакшене ограничить» не выполнен.
**Исправление (P1):** задать конкретный список origins (домены фронта) либо отключить `allow_credentials`; убедиться, что сервис не торчит наружу (только внутренняя сеть/Docker).

### 4. [High] CSP `unsafe-inline` + stored XSS через innerHTML
**Где:** `app/security.py:176-180` — `script-src 'self' 'unsafe-inline' https://unpkg.com`. Рендер данных сервера в DOM через `innerHTML` (например, панель уведомлений в `app/templates/_layout_dashboard.html`, где `${n.title}`/`${n.link}` вставляются в разметку).
**Риск:** при попадании HTML в поля (заголовок/ссылка уведомления, имена, данные из CRM) — исполнение скрипта; CSP не спасает из-за `unsafe-inline`.
**Исправление (P1):** (а) экранировать/использовать `textContent` вместо `innerHTML` для серверных строк; (б) перейти на CSP без `unsafe-inline` — вынести inline-скрипты в файлы, использовать nonce; (в) серверная sanitize для пользовательского HTML, если он где-то выводится.

### 5. [Medium] Сессионная cookie не Secure по умолчанию
**Где:** `app/main.py:582` — `https_only = getenv("HTTPS_ONLY","false")`; `:600-605` `SessionMiddleware(..., same_site="lax")` без `max_age`.
**Риск:** при HTTP (или если флаг не выставлен) cookie сессии передаётся без `Secure` и может быть перехвачена; нет явного срока истечения.
**Исправление (P1):** в проде `HTTPS_ONLY=true`; задать `max_age` (например, 7–14 дней) и при желании `same_site="strict"` для критичных действий; убедиться, что nginx форсит HTTPS.

### 6. [Medium] Отсутствует CSRF-защита
**Где:** `app/security.py` содержит `generate_csrf_token`/`validate_csrf_token`, но они не применяются к POST-формам/изменяющим запросам; глобального CSRF-middleware нет.
**Риск:** `SameSite=lax` частично защищает, но не покрывает все сценарии (особенно если будет `none`/кросс-сайтовые POST). Изменяющие действия (смена роли, premium, удаление) потенциально подделываемы.
**Исправление (P1):** внедрить CSRF-токены в формы и проверку на сервере (или строгий `SameSite=strict` + проверка Origin/Referer для POST). Особенно для `/admin/*`, `/sales/*`, `/settings/*`.

### 7. [Medium] CRM-webhook: timing-сравнение и секрет в URL
**Где:** `app/routers/crm_integration.py:724-730` — секрет в пути `/{webhook_secret}`, сравнение `integration.webhook_secret != webhook_secret`.
**Риск:** (а) не constant-time сравнение → теоретический timing-leak; (б) секрет в URL попадает в access-логи/прокси/`server.log` (а он в репо) → утечка → подделка событий CRM.
**Исправление (P1/P2):** сравнивать через `secrets.compare_digest`; передавать секрет в заголовке/подписи (HMAC тела), а не в URL; не логировать URL с секретом.

### 8. [Medium] Rate limiting in-memory
**Где:** `app/middleware/rate_limit.py` — счётчики в памяти процесса.
**Риск:** у вас запускается несколько uvicorn-воркеров → лимиты считаются по-воркерно (фактический лимит ×N); сбрасываются при рестарте; идентификация по IP обходится сменой IP. Слабо защищает от распределённого брутфорса логина/кода.
**Исправление (P1):** перенести лимитер в Redis (общий стор); добавить **account-based lockout** на логин/верификацию/сброс (по email, не только IP).

### 9. [Medium] Ключ шифрования CRM-токенов в каталоге кода
**Где:** `app/.crm_encryption_key` (не в git — это плюс), используется `app/services/crm_service.py`.
**Риск:** ключ рядом с кодом; при бэкапе/копировании каталога/случайном коммите — компрометация всех CRM-токенов. Сейчас файл untracked, но риск управляемости.
**Исправление (P2):** хранить ключ в секрет-менеджере/переменной окружения (`CRM_ENCRYPTION_KEY`), вне рабочего каталога; ротация ключа + перешифровка токенов.

### 10. [Medium] Нет лимита попыток ввода кода верификации
**Где:** `app/routers/auth.py` (verify/resend).
**Риск:** усиливает находку №2 — без счётчика попыток 6-значный код брутфорсится.
**Исправление (P1):** счётчик попыток + блокировка/инвалидация кода, экспоненциальная задержка.

### 11. [Low] DEV-показ кода верификации
**Где:** `app/routers/auth.py:30` — показывается при `DEV_SHOW_VERIFICATION_CODE in (1/true/yes)`.
**Риск:** при случайном включении в проде — раскрытие кодов.
**Исправление (P2):** жёстко привязать к `ENVIRONMENT=development`; убедиться, что в проде флаг выключен; логировать предупреждение при включении.

### 12. [Low] Расширение загрузки без allowlist
**Где:** `app/routers/chat.py:325` — `ext` берётся из имени файла.
**Риск:** низкий (файлы отдаются как `attachment`, т.е. скачиваются, не рендерятся), но злоупотребление хранилищем/обход типа.
**Исправление (P2):** валидировать `ext` по allowlist в `file_validator`, согласовать с MIME; принудительно `Content-Disposition: attachment` (уже де-факто так).

### 13. [Medium, требует проверки] IDOR на прочих эндпоинтах
Я подтвердил проверку владельца для вложений/экспорта, но **не дочитал** часть эндпоинтов — их нужно проверить на принадлежность объекта пользователю/команде:
- `app/routers/training_plans.py` — `POST /training/{id}/start`, `POST /training-session/{id}/complete`, `GET /api/training/{id}` (может ли чужой пользователь стартовать/завершать чужую тренировку?).
- `app/routers/team_analytics.py` — доступ менеджера к `member/{member_id}` (только своей команды?).
- `app/routers/webrtc_meetings.py` / `zoom_meetings.py` — join/детали/удаление по `meeting_id` (проверка участника/создателя?).
- `app/routers/notifications.py` — `POST /{id}/read`, `DELETE /{id}` (чужие уведомления?).
- `app/routers/crm_integration.py` — `GET /crm/recording/{id}/details`, `integrations/{id}/*`, `GET /crm/debug/{id}` (владелец интеграции?).
- `app/routers/admin_research.py` — только admin (через layout/гард) — проверить, что роутер реально требует admin.

**Исправление (P1):** добавить/подтвердить проверку `owner/team` во всех перечисленных; где доступ по id — всегда фильтровать по `user_id`/членству.

---

## План исправлений по фазам

### P0 — немедленно (утечка данных)
- [ ] `git rm --cached` для БД/дампов/логов/cookies/.env; коммит.
- [ ] Ротация всех секретов (БД, SECRET_KEY, API-ключи, OAuth, CRM, ключ шифрования).
- [ ] Чистка истории git (BFG/filter-repo) + форс-пуш (предупредить команду).
- [ ] Код верификации → `secrets`, лимит попыток + срок (находки №2, №10).

### P1 — ближайший спринт
- [ ] Проверить/закрыть IDOR (находка №13).
- [ ] CSP без `unsafe-inline` + `textContent`/sanitize вместо `innerHTML` (№4).
- [ ] CSRF на изменяющих запросах/формах (№6).
- [ ] Rate limit в Redis + account lockout (№8).
- [ ] Сессия: `HTTPS_ONLY=true` в проде, `max_age` (№5).
- [ ] CRM-webhook: `compare_digest`, секрет в заголовке/HMAC, не логировать (№7).
- [ ] CORS ai_agent_service — ограничить origins (№3).

### P2 — харденинг
- [ ] Ключ шифрования CRM в секрет-менеджер/env (№9).
- [ ] DEV-флаги привязать к ENVIRONMENT, аудит логов на PII (№11), не коммитить `server.log`.
- [ ] Allowlist расширений загрузки (№12).
- [ ] `pip-audit`/обновление зависимостей; пентест-проверка SSRF в CRM/OAuth (валидация хоста исходящих запросов).
- [ ] Централизованное логирование без секретов; маскирование в логах (PII уже маскируется в LLM-пайплайне через `pii_redactor`, но не в общих логах).

---

## Ограничения этого аудита
- Только статический анализ кода, без динамической проверки/эксплуатации.
- Не проверены до конца: полный список IDOR (см. №13), SSRF-валидация исходящих URL в `crm_service.py`/`zoom_service.py`/`google_oauth.py` (рекомендуется отдельная проверка хоста/схемы и блокировка внутренних адресов), актуальность версий зависимостей.
- Субагенты-аудиторы не использовались (биллинг аккаунта); при возможности стоит прогнать автоматические сканеры (Bandit, Semgrep, pip-audit, gitleaks/trufflehog по истории).
