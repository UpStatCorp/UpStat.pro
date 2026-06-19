# Frontend Style Changes — Claude-Style Redesign

## Overview

The platform UI was redesigned to match a compact, minimal "Claude-style" aesthetic:
- Smaller padding and font sizes throughout
- Flatter cards (no heavy box-shadows, reduced border-radius)
- Muted button defaults (pill-shaped outlines with `var(--muted)` color)
- No hover `transform: translateY(...)` animations — only subtle `border-color` transitions
- Consistent use of CSS custom properties: `--bg`, `--surface`, `--surface-hover`, `--text`, `--muted`, `--border`, `--primary`, `--primary-hover`, `--accent`

---

## 1. Train Module Sidebar — `app/templates/train/_layout.html`

Complete rewrite to match the main platform sidebar (`_layout_dashboard.html`).

**Key changes:**
- Brand: `<span class="brand-logo">` + `UpStat` + `<span class="brand-badge">TRAIN</span>` pill badge
- Sidebar width: `260px`, padding: `18px 14px`
- Menu items: `color: var(--muted)` default; active state = primary background
- Footer: userbox style (avatar circle + username/email) instead of inline user-chip
- Logout: `btn block` full-width button
- Dark theme toggle: moved to topbar
- Mobile: hamburger toggle with `.active` class, same pattern as main platform

---

## 2. Global CSS — `app/static/styles.css`

### Dashboard layout

| Element | Before | After |
|---|---|---|
| `.dashboard-header` padding | `28px 32px` | `20px 24px` |
| `.dashboard-header` margin-bottom | `28px` | `20px` |
| `.dashboard-title h1` font-size | `26px` | `22px` |
| `.stats-grid` gap | `20px` | `16px` |
| `.stat-card` padding | `24px` | `18px 20px` |
| `.stat-card` hover | lift + shadow | border-color change only |
| `.stat-icon` size | `52px` | `40px` |
| `.stat-icon` border-radius | `14px` | `10px` |
| `.stat-value` font-size | `36px` | `32px` |
| `.stat-label` font-size | `14px` | `13px` |
| `.dashboard-section` padding | `28px 32px` | `20px 24px` |
| `.section-title h2` font-size | `20px` | `16px` |
| `.btn-outline` | filled-ish | pill (`999px`), muted color, 1px border |
| `.btn-sm` | square-ish | pill (`999px`), `6px 12px` |
| `.analysis-item` padding | `16px 20px` | `14px 16px` |
| `.date-icon` size | `40px` | `34px`, background `var(--accent)` |

### Chat section (complete rewrite, ~lines 1349–1800)

| Element | After |
|---|---|
| `.chat-container` | `display: flex; flex-direction: column; height: calc(100vh - 57px); padding: 20px 24px` |
| `.chat-card` | `border: 1px solid var(--border); border-radius: 12px; flex: 1; no box-shadow` |
| `.chat-scroll` | `padding: 20px 24px; background: var(--bg); scrollbar-width: 5px` |
| `.bubble` | `max-width: 68%; padding: 11px 15px; border-radius: 14px; min-width: 0; overflow: hidden` |
| `.msg.you .bubble` | `border-radius: 14px 14px 4px 14px` (tail bottom-right) |
| `.msg.bot .bubble` | `border-radius: 14px 14px 14px 4px` (tail bottom-left) |
| `.chat-scroll .text` | `font-size: 14px` |
| `.composer` | `border-top: 1px solid var(--border); padding: 14px 16px` |
| `.attach-btn`, `.send-btn` | `38px`, `border-radius: 10px`, no animations |

### New elements added

```css
/* Bot avatar */
.msg-avatar {
  width: 26px; height: 26px; border-radius: 50%;
  background: var(--primary); color: #fff;
  font-size: 9px; font-weight: 700;
  margin-top: 18px;
}

/* Audio player in bubble */
.file-audio {
  margin-top: 10px; padding: 10px 12px;
  border-radius: 10px; background: rgba(0,0,0,.06);
}

/* File chip (non-image, non-audio attachments) */
a.file-chip {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 12px; border-radius: 10px;
  background: rgba(255,255,255,.15);
  color: rgba(255,255,255,.95) !important;
  text-decoration: none !important;
}
.chat-scroll .msg.bot a.file-chip {
  background: var(--surface-hover);
  color: var(--text) !important;
}
.file-chip-ext {
  background: rgba(255,255,255,.2); color: #fff;
  font-size: 10px; font-weight: 700;
  padding: 3px 6px; border-radius: 5px;
}

/* Composer file chips */
.chip { border-radius: 8px; border: 1px solid var(--border); padding: 6px 10px 6px 8px; }
.chip-icon { width: 26px; height: 26px; border-radius: 5px; background: var(--accent); color: var(--primary); }
.chip-x { background: none; }
.chip-x:hover { background: rgba(239,68,68,.1); color: #ef4444; }
```

---

## 3. Dashboard Template — `app/templates/dashboard.html`

- Removed inline `style` overrides from training section `div.dashboard-section`
- Section header h2: removed inline `font-size`/`gap` overrides
- Buttons: removed inline `padding`/`font-size` overrides (now inheriting from global `.btn-*`)

---

## 4. Chat Template — `app/templates/chat.html`

- Plan badges: changed from large `div` blocks to compact inline pill `<span>` elements
  ```html
  <span style="display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;border-radius:999px;padding:5px 12px;">
  ```
- Disclaimer box: `margin-top: 10px; padding: 9px 14px; border-radius: 8px`

---

## 5. Messages Partial — `app/templates/partials/messages.html`

Complete rewrite of message rendering:

- **Bot messages**: prepend `<div class="msg-avatar">AI</div>` (26px circle, primary color)
- **Bubble tails**: different `border-radius` for `.you` vs `.bot` messages
- **Images**: rendered in `.files-grid` with `class="file-item image"`
- **Audio**: rendered with native `<audio controls>` + download link + file size
- **Documents**: rendered as `<a class="file-chip">` with extension badge, filename, and size
- Jinja2 note: use `for a in m.attachments if a.mime_type.startswith('image/')` — `selectattr` with `match` test is NOT supported in Jinja2

---

## 6. CRM Integration Page — `app/templates/crm_integration.html`

Inline `<style>` block rewritten. Main changes:

| Element | Before | After |
|---|---|---|
| `.crm-page` padding | `28px 32px` | `20px 24px` |
| `.page-header h1` font-size | `26px` | `18px` |
| `.page-header` | plain flex row | surface card with `border: 1px solid var(--border); border-radius: 12px` |
| `.stat-card-crm` border-radius | `14px` | `10px` |
| `.stat-card-crm` padding | `20px` | `14px 16px` |
| `.stat-icon-crm` size | `48px` | `36px` |
| `.stat-num` font-size | `24px` | `20px` |
| `.section-title` font-size | `18px` | `14px`, uppercase, letter-spacing |
| `.integration-card` border-radius | `14px` | `10px` |
| `.integration-card` padding | `20px` | `16px` |
| `.integration-card` hover | shadow lift | border-color only |
| `.add-crm-card` layout | centered vertical (grid) | horizontal flex row |
| `.add-crm-card` hover | `transform: translateY(-4px) + shadow` | border-color only |
| `.add-crm-icon` size | `font-size: 40px` emoji | `36px` div, `var(--accent)` bg |
| `.how-it-works` border-radius | `14px` | `10px` |
| `.how-it-works` padding | `24px` | `16px 20px` |
| `.btn-action` border-radius | `10px` | `999px` (pill) |
| `.btn-primary-action` hover | `transform + shadow` | background color only |
| `.modal-box` border-radius | `16px` | `14px` + `border: 1px solid var(--border)` |
| `.field-input` border-radius | `10px` | `8px` |
| `.field-input` font-size | `14px` | `13px` |
| `.method-tab` font-size | `14px` | `13px` |
| `.tip-box` bg | `rgba(59,130,246,.06)` | `var(--accent)` |

---

## 7. Analytics Page — `app/templates/analytics.html`

Inline `<style>` block полностью переписан под Claude-style.

| Element | Before | After |
|---|---|---|
| `.analytics-page` padding | `24px` | `20px 24px` |
| `.analytics-title` font-size | `24px` | `20px` |
| `.analytics-subtitle` font-size | `14px` | `13px` |
| `.analytics-header` margin-bottom | `20px` | `16px` |
| `.a-select` padding | `8px 12px` | `6px 10px` |
| `.a-select` border-radius | `10px` | `8px` |
| `.a-checkbox-label` border-radius | `8px` | `999px` (pill) |
| `.analytics-tabs` margin-bottom | `20px` | `16px` |
| `.a-tab` padding | `10px 24px` | `8px 18px` |
| `.a-tab` font-size | `14px` | `13px` |
| `.a-tab.active` | `box-shadow: 0 4px 12px ...` | без тени |
| `.summary-cards` gap | `16px` | `14px` |
| `.summary-cards` margin-bottom | `24px` | `20px` |
| `.s-card` border-radius | `14px` | `10px` |
| `.s-card` padding | `18px 20px` | `14px 16px` |
| `.s-card` hover | `transform: translateY(-2px) + shadow` | `border-color` only |
| `.s-card-icon` size | `48px` | `36px` |
| `.s-card-value` font-size | `24px` | `20px` |
| SVG иконки в summary cards | `22×22` | `18×18` |
| `.charts-grid` gap | `20px` | `16px` |
| `.chart-card` border-radius | `14px` | `10px` |
| `.chart-card` padding | `20px` | `16px` |
| `.chart-card` hover | `box-shadow` lift | `border-color` only |
| `.chart-card-title` font-size | `15px` | `13px` |
| `.chart-wrap` height | `260px` | `240px` |
| `.chart-wide .chart-wrap` height | `300px` | `260px` |
| `.bool-grid` gap | `12px` | `10px` |
| `.bool-item` border-radius | `10px` | `8px` |
| `.bool-bar-bg` height | `8px` | `6px` |
| `.nav-btn` padding | `11px 14px` | `8px 12px` |
| `.nav-btn` font-size | `13px` | `12px` |
| `.nav-btn` border-radius | `12px` | `8px` |
| `.nav-btn` min-height | `44px` | `38px` |
| `.nav-btn` hover | `box-shadow` | без тени |
| `.nav-btn-back` border-radius | `12px` | `999px` (pill) |
| `.funnel-bar` padding | `12px 16px` | `10px 14px` |
| `.funnel-bar` border-radius | `10px` | `8px` |
| `.funnel-val` font-size | `18px` | `16px` |

Добавлен `.s-card-sub` (стиль для подписи в CRM-карточках).

---

## 8. Teams Page — `app/templates/team_manage.html` + `partials/team_manage_body.html`

**`team_manage.html` (full version):**
- Добавлен wrapper `<div style="padding:20px 24px; max-width:960px;">` — в dashboard layout `.content{padding:0}`, а в train layout `padding:28px 32px`, поэтому без wrapper контент был без отступов.

**`partials/team_manage_body.html`** — Claude-style:

| Element | Before | After |
|---|---|---|
| `.tl-header` margin-bottom | `32px` | `20px` |
| `.tl-title` font-size | `24px` | `20px` |
| `.tl-sub` font-size | `14px` | `13px` |
| `.tl-section` margin-bottom | `32px` | `20px` |
| `.tl-section-title` font-size | `13px` | `11px` |
| `.team-card` border-radius | `var(--radius)` | `10px` |
| `.team-card` padding | `18px 20px` | `14px 16px` |
| `.team-card` hover | `box-shadow` lift | `border-color` only |
| `.team-card` | `box-shadow: var(--shadow)` | без тени |
| `.tc-name` font-size | `16px` | `15px` |
| `.create-form-wrap` border-radius | `var(--radius)` | `10px` |
| `.create-form-wrap` padding | `20px 22px` | `16px` |
| `.create-form-wrap` | `box-shadow: var(--shadow)` | без тени |
| `.create-form-wrap h3` font-size | `15px` | `13px` |
| `.create-form-wrap input` padding | `10px 12px` | `8px 10px` |
| `.create-form-wrap input` font-size | `14px` | `13px` |
| `.empty-card` border-radius | `var(--radius)` | `10px` |
| `.empty-card` padding | `48px 20px` | `36px 20px` |
| `.empty-card` | `box-shadow: var(--shadow)` | без тени |
| `.ec-title` font-size | `16px` | `14px` |
| `.ec-sub` font-size | `14px` | `13px` |

---

## 9. Owner Dashboard — `app/templates/owner_dashboard.html`

**Кардинальный редизайн** — полная замена glassmorphism/cosmic стиля на Claude-style.

**Убрано:**
- Все кастомные CSS-переменные (`--blue-o`, `--gold-o`, `--cyan-o`, `--panel`, `--shadow-xl`, `--radius-2xl` и др.)
- Radial-gradient фон с blob-эффектами (`::before`/`::after` декорации)
- Glassmorphism (`backdrop-filter: blur`, `rgba(255,255,255,0.82)` фоны)
- `transform: translateY` hover-лифты на всех элементах
- Огромные border-radius (`34px`, `26px`, `22px`, `20px`, `18px`)
- Отдельные `body.dark .ow-*` правила (темная тема теперь через системные CSS-переменные)
- `clamp(28px, 4vw, 52px)` шрифты в заголовках
- `box-shadow` на интерактивных элементах

**Добавлено (Claude-style):**
- `.ow-glass` = `background: var(--surface); border: 1px solid var(--border); border-radius: 10px`
- Все цвета через системные переменные: `--bg`, `--surface`, `--text`, `--muted`, `--border`, `--primary`, `--accent`
- Hover только `border-color: var(--primary)` без сдвигов
- Компактные отступы: `padding: 20px 24px` на странице, `18px 20px` на панелях
- Pill-кнопки для режимов и фильтров периода (`border-radius: 999px`)
- `ringStyle()` в JS обновлён — использует системные цвета вместо `--blue-o`/`--gold-soft`
- CSS с ~560 строк → ~180 строк

---

## 10. Dashboard — `app/templates/dashboard.html`

**Выравнивание кнопок в карточках тренировок:**
- `.training-plan-actions` добавлен `margin-top: auto`
- Карточки уже `flex-direction: column`, поэтому `margin-top: auto` прижимает кнопку ко дну — все кнопки «Начать / Продолжить тренировку» выровнены по одной линии независимо от высоты контента карточки

---

## Design Principles Applied

1. **Compact density** — reduce all padding by ~25-30%; font sizes by 1-2px
2. **Flat surfaces** — `border: 1px solid var(--border)` replaces `box-shadow`; shadows used only on modals
3. **Muted secondary actions** — outline buttons use `var(--muted)` color, `var(--border)` stroke
4. **No lift animations** — `hover` only changes `border-color` or `background`; no `transform: translateY`
5. **Pill buttons** — secondary/outline actions use `border-radius: 999px`; primary actions keep `999px` too for consistency
6. **CSS custom properties** — all colors via variables, enabling dark mode via `body.dark` class

---

## Build Process

After any template or CSS change, a Docker rebuild is required:

```bash
docker compose build backend && docker compose up -d backend
```

Static files are baked into the image — live-mounting is not active.
