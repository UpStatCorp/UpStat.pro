# UpStat — редизайн UI кабинета (15 июня 2026)

Дата: **2026-06-15**  
Цель: минималистичный интерфейс в духе Claude / ChatGPT / Jazu — светлая нейтральная база, **глубокий тёмно-синий (navy)** как единственный акцент, без градиентов, с inline-SVG иконками вместо эмодзи.

Стек **не менялся**: FastAPI + Jinja2 + htmx + ванильный CSS.

---

## Принципы дизайна

| Принцип | Было | Стало |
|---------|------|-------|
| Палитра | Яркий синий `#2563eb`, много градиентов | Navy `#1e3a8a` (светлая), `#3b5bdb` (тёмная тема) |
| Кнопки | Прямоугольные, градиент, `translateY` на hover | `border-radius: 999px`, плоский navy, без прыжков |
| Сайдбар | Градиентный бренд, активный пункт с градиентом и `translateX` | Плоский, активный = сплошная navy-заливка, иконки у пунктов |
| Эмодзи | 🎤 📊 ✅ 🔒 ⚡ и др. | Inline-SVG через Jinja-макрос `icon()` |
| Анимации | Pulse, blur, крупные translate | Сдержанные 150–200ms; pulse убран из layout |

---

## Дизайн-токены

### Светлая тема (`:root`)

```css
--bg: #f8fafc
--surface: #ffffff
--text: #0f172a
--muted: #64748b
--border: #e6e9ef
--primary: #1e3a8a          /* navy — кнопки, активные пункты, ссылки */
--primary-hover: #1e40af
--accent: #eef2ff           /* мягкая синяя подложка */
--accent-strong: #e0e7ff
--ring: rgba(30,58,138,.30)
--shadow: 0 1px 2px rgba(15,23,42,.05)
--shadow-md: 0 4px 16px rgba(15,23,42,.06)
--radius: 14px
--surface-hover: #f1f5f9
```

### Тёмная тема (`body.dark`)

```css
--bg: #0b1220
--surface: #111a2e
--text: #e8edf6
--primary: #3b5bdb
--primary-hover: #4f6ef7
--accent: rgba(59,91,219,.16)
--border: #1e2a44
```

**Файлы с токенами:**
- `app/static/styles.css` — глобальные переменные (строки ~1–35)
- `app/templates/_layout_dashboard.html` — дублирующий `:root` в `<style>` блоке layout'а кабинета

> CSS cache-bust: `styles.css?v=17` в `_layout_dashboard.html` и `base.html`.

---

## Этап 1 — Каркас кабинета

**Файл:** `app/templates/_layout_dashboard.html`

### Бренд и логотип
- Убран градиентный текст бренда (`linear-gradient` + `background-clip: text`).
- Добавлен `.brand-logo` — квадрат 28×28px с navy-фоном и SVG-иконкой `logo` (молния).

### Сайдбар
- Ширина ~220px (desktop), padding уменьшен.
- Фон: `var(--surface)` без градиента и `backdrop-filter`.
- Пункты меню: `color: var(--muted)`, hover → `var(--surface-hover)`.
- Активный пункт: `background: var(--primary); color: #fff` — без тени и `translateX`.
- У каждого пункта навигации — inline-SVG иконка (dashboard, phone, settings, chat, crm, analytics, team, owner).

### Кнопки `.btn`
- `border-radius: 999px`, основная `.btn-primary` — navy.
- Убраны `transform: translateY(-2px)` и цветные box-shadow на hover.

### Топбар, карточки, таблицы
- Убран `backdrop-filter: blur(10px)` с топбара.
- Карточки и таблицы — минимальные тени через `var(--shadow)`.

### Баннеры подписки
- Градиентные фоны заменены на сплошные: `#fef2f2` (danger), `#fffbeb` (warning).
- Эмодзи `⚠️` / `📊` → `icons.icon('warning')` / `icons.icon('chart')`.

### Уведомления
- Убран градиент в `.notifications-header`.
- Убрана `@keyframes pulse-badge` у бейджа.
- Добавлены `escapeHtml()` и `safeUrl()` в JS-рендере уведомлений (защита от XSS — см. также security-документ).

### Мобильный сайдбар
- Убран отдельный «тёмный градиентный» mobile-стиль (blur, хардкод `#2563eb`).
- Мобильная шторка использует ту же светлую схему, что и desktop.

### Топбар — починка иконок
- `<i class="fas fa-user-shield">` и `<i class="fas fa-credit-card">` заменены на `icons.icon('shield')` и `icons.icon('card')` — Font Awesome в этом layout не был подключён, иконки не отображались.

---

## Этап 2 — Макрос иконок

**Новый файл:** `app/templates/partials/icons.html`

```jinja
{% import "partials/icons.html" as icons %}
{{ icons.icon('dashboard', size=18) }}
```

- Jinja-макрос `icon(name, size=20, cls='')`.
- Inline line-SVG, `stroke=currentColor`, `viewBox="0 0 24 24"`.
- Словарь ~30 иконок; неизвестное имя → фолбэк `file`.
- Добавлена иконка `lock` (замок) для статусов «заблокировано».

### Шаблоны с импортом `icons` (кабинет + частично admin/train)

`_layout_dashboard.html`, `dashboard.html`, `calls.html`, `crm_integration.html`, `crm_recordings.html`, `analytics.html`, `team_analytics.html`, `member_plan_stats.html`, `member_report.html`, `training_plan.html`, `chat.html`, `chat_trener.html`, `team_members.html`, `settings.html`, `team_manage.html`, `team_script.html`, `free_activation.html`, `career.html`, `admin_performance.html`, `webrtc_meeting.html`, `voice_training_conference.html`, а также ряд admin/train шаблонов.

---

## Этап 3 — Глобальные стили

**Файл:** `app/static/styles.css`

### Масштаб работ
- **93** вхождения `linear-gradient` → схлопнуты в сплошные цвета через CSS-переменные.
- Остались только 2 легитимных `-webkit-mask: linear-gradient(...)` (техника рамок, не визуальный градиент).

### Ключевые замены
| Область | Изменение |
|---------|-----------|
| `.msg-bubble.user`, `.send-btn`, `.progress .bar` | `#2563eb` → `var(--primary)` |
| `.stat-card-* .stat-icon` | Градиенты → `var(--accent)` + `color: var(--primary)` |
| `.dashboard-header`, `.dashboard-title h1` | Градиентный фон/текст → `var(--surface)` / `var(--text)` |
| `.chat-header` | Убраны градиент, `::before` mask-border, `translateY` hover |
| Premium/gold секции | Янтарные градиенты → `#fffbeb` / `#f59e0b` |
| `body.dark` overrides | Градиенты → `var(--surface)` / `var(--bg)` |

---

## Этап 4 — Страницы кабинета (in-scope)

Полная обработка (эмодзи → SVG, градиенты → токены):

| Страница | Эмодзи (было) | Градиенты |
|----------|---------------|-----------|
| `dashboard.html` | 12 | 9 → 0 |
| `calls.html` | 6 | 9 → 0 |
| `crm_integration.html` | 4 | 3 → 0 |
| `crm_recordings.html` | 0 | 3 → 0 |
| `analytics.html` | 2 | 4 → 0 |
| `team_analytics.html` | 7 | 5 → 0 |
| `member_plan_stats.html` | 6 | 5 → 0 |
| `member_report.html` | 0 | 10 → 0 |
| `training_plan.html` | 12 | 5 → 0 |
| `chat.html` | 12 | 1 → 0 |
| `chat_trener.html` | 7 | 0 |
| `team_members.html` | 3 | 0 |
| `owner_dashboard.html` | 0 (типографика →) | 25 → 0 |
| `base.html` | 🌙/☀️ → SVG sun/moon | — |

### Пример: `dashboard.html`
- Секция «Голосовые тренировки»: фон `var(--accent)`, иконка `mic`.
- Карточки планов: `trophy`/`target`, статусы `check`/`clock`, locked → `lock`.
- Welcome-модалка: navy-кнопка, без фиолетовых градиентов, подарки через `target`/`mic` иконки.

---

## Вне рамок UI-прохода (не переработаны полностью)

| Область | Статус |
|---------|--------|
| `admin/research_list.html`, `admin/research_view.html` | Не трогали (Research Mode) |
| `admin/_layout_admin.html`, `train/_layout.html` | Font Awesome, частичный import icons |
| `landing.html` | ~33 эмодзи, ~18 градиентов |
| `login/register/reset/forgot-password` | Эмодзи, 1–3 градиента |
| `voice_training_conference.html` | ~37 эмодзи |
| `webrtc_meeting.html` | ~32 эмодзи |

---

## Minor-отклонения (осознанно оставлены)

После проверки субагентом остались некритичные моменты:

- `translateY(-2px…-6px)` на hover карточек (`team_analytics.html`, `calls.html` и др.)
- Один `pulse-icon` в `calls.html:733`
- `backdrop-filter: blur()` в welcome-overlay (`dashboard.html`) и CRM-модалках
- Хардкод `rgba(30,41,59,...)` в `body.dark`-оверрайдах отдельных страниц
- Цвета Chart.js в `analytics.html` (`#2563eb`, `#3b82f6`) — палитра графиков, не UI-токены

---

## План редизайна

Исходный план: `.cursor/plans/upstat_ui_redesign_cb4251c0.plan.md`  
Проверен субагентом до и после реализации.

## Порядок внедрения (фактический)

1. Токены `:root` / `body.dark`
2. Каркас `_layout_dashboard.html`
3. Макрос `partials/icons.html`
4. `styles.css` — массовое удаление градиентов
5. Страницы кабинета (dashboard эталон → 4 параллельных субагента)
6. Пост-проверка: хардкод `#2563eb`, иконка `lock`, CSS v17
