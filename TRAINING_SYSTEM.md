# 🎯 Система прогрессивных тренировок

## Обзор

Система прогрессивных тренировок автоматически создает план индивидуальных тренировок на основе анализа звонков. Каждая тренировка открывается последовательно после прохождения предыдущей.

## Как это работает

### 1. Анализ звонка
- Пользователь загружает аудиозапись звонка
- GPT анализирует звонок и создает отчёт с рекомендациями
- Отчёт сохраняется в историю звонков

### 2. Создание плана тренировок
- В истории звонков появляется кнопка **"🎯 Начать тренировку"**
- При клике система автоматически парсит рекомендации из анализа
- Создаётся план с несколькими тренировками (до 5 штук)
- Первая тренировка сразу доступна, остальные заблокированы

### 3. Прохождение тренировок
- Пользователь начинает первую тренировку
- Тренировка проходит через голосовой интерфейс с непрерывным прослушиванием
- ИИ-тренер работает с пользователем над конкретной проблемой
- После завершения автоматически сохраняется:
  - Транскрипт диалога
  - Оценка (score 0-100)
  - Обратная связь от ИИ
  - Статистика (количество ответов, вопросов)

### 4. Разблокировка следующих тренировок
- Если score ≥ 70, тренировка считается пройденной ✅
- Автоматически разблокируется следующая тренировка
- Прогресс отображается круговой диаграммой
- После прохождения всех тренировок план помечается как завершённый 🎉

## Архитектура

### Модели БД

#### `AnalysisTrainingPlan`
План тренировок на основе анализа звонка.

```python
- id: int (PK)
- user_id: int (FK -> users)
- report_message_id: int (FK -> messages)
- title: str
- recommendations_json: str (JSON с рекомендациями)
- total_trainings: int
- completed_trainings: int
- status: str (active, completed, archived)
- created_at: datetime
```

#### `Training`
Отдельная тренировка в плане.

```python
- id: int (PK)
- plan_id: int (FK -> analysis_training_plans)
- order: int (порядковый номер)
- title: str
- description: str
- recommendation: str
- scenario_type: str (sales, custom)
- checklist_json: str (опционально)
- status: str (locked, available, in_progress, completed)
- attempts: int
- best_score: int
- last_attempt_at: datetime
- completed_at: datetime
- created_at: datetime
```

#### `TrainingSession`
Сессия прохождения тренировки.

```python
- id: int (PK)
- training_id: int (FK -> trainings)
- user_id: int (FK -> users)
- started_at: datetime
- completed_at: datetime
- duration_seconds: int
- transcript: str
- score: int
- feedback: str
- checklist_results_json: str
- user_responses_count: int
- ai_questions_count: int
```

### Сервисы

#### `TrainingPlanService`
**Файл:** `app/services/training_plan_service.py`

Методы:
- `parse_recommendations_from_analysis(analysis_text)` - Парсит рекомендации из текста анализа
- `_extract_recommendations_with_gpt(analysis_text)` - Использует GPT для извлечения рекомендаций
- `create_training_plan(db, user_id, report_message_id, analysis_text)` - Создаёт план тренировок
- `unlock_next_training(db, plan_id)` - Разблокирует следующую тренировку

### Роутеры

#### `training_plans.py`
**Файл:** `app/routers/training_plans.py`

Endpoints:
- `GET /training-plan/{report_msg_id}` - Показывает план тренировок
- `POST /training/{training_id}/start` - Начинает тренировку (создаёт сессию)
- `POST /training-session/{session_id}/complete` - Завершает сессию
- `GET /api/training/{training_id}` - API для получения информации о тренировке

#### `voice_assistant.router.py`
**Файл:** `voice_assistant/router.py`

Обновлённые endpoints:
- `GET /voice-assistant/training?training_id={id}&session_id={id}` - Страница тренировки
- `POST /voice-assistant/training/complete` - Сохраняет результаты тренировки

### Frontend

#### `training_plan.html`
**Файл:** `app/templates/training_plan.html`

Страница плана тренировок:
- Круговая диаграмма прогресса
- Карточки тренировок с информацией:
  - Заголовок и описание проблемы
  - Рекомендация от GPT
  - Статистика (попытки, лучший результат)
  - Статус (🔒 заблокирована, ▶️ доступна, ✅ пройдена)
- Кнопки для начала/повтора тренировки

#### `voice-training.js`
**Файл:** `app/static/js/voice-training.js`

Обновлённые методы:
- `constructor(trainingId, sessionId)` - Принимает ID тренировки и сессии
- `saveTrainingResults()` - Сохраняет результаты после завершения
- Автоматически вызывается при остановке тренировки

## API Endpoints

### Получить план тренировок
```http
GET /training-plan/{report_msg_id}
```

**Ответ:** HTML страница с планом тренировок

---

### Начать тренировку
```http
POST /training/{training_id}/start
```

**Ответ:**
```json
{
  "success": true,
  "redirect": "/voice-assistant/training?training_id=123&session_id=456"
}
```

---

### Завершить тренировку
```http
POST /voice-assistant/training/complete
Content-Type: application/json

{
  "training_id": 123,
  "session_id": 456,
  "transcript": "...",
  "score": 85,
  "user_responses_count": 12,
  "ai_questions_count": 15
}
```

**Ответ:**
```json
{
  "success": true,
  "message": "Результаты сохранены",
  "score": 85,
  "feedback": "Отличная работа! Вы прекрасно справились с тренировкой."
}
```

## Формула оценки

```javascript
score = min(100, floor(
  (user_responses * 10) + 
  (checklist_progress * 0.5)
))
```

Критерии прохождения:
- ✅ **Пройдено:** score ≥ 70
- ⚠️ **Не пройдено:** score < 70

## Интеграция с существующей системой

### 1. История звонков
В `calls.html` добавлена кнопка:
```html
<a href="/training-plan/{{ p.report_msg_id }}" class="btn btn-primary btn-sm">
  🎯 Начать тренировку
</a>
```

### 2. Voice Assistant
Интегрирован с существующей системой голосового ассистента:
- Использует тот же WebSocket для потоковой передачи
- Непрерывное прослушивание (VAD)
- Распознавание речи (Whisper/ElevenLabs)
- Генерация ответов (GPT-4o)
- Озвучивание (ElevenLabs TTS)

### 3. База данных
Миграция автоматически создаётся при запуске через `create_training_tables()` в `main.py`.

## Развертывание

1. **Обновить код:**
```bash
git pull
```

2. **Перезапустить Docker:**
```bash
docker-compose down
docker-compose up -d --build
```

3. **Проверить создание таблиц:**
Таблицы создаются автоматически при первом запуске.

## Использование

1. Загрузите аудиозапись звонка через `/chat`
2. Дождитесь анализа
3. Перейдите в `/calls` (История звонков)
4. Нажмите **"🎯 Начать тренировку"** на любом анализе
5. Система автоматически создаст план тренировок
6. Начните первую тренировку
7. Общайтесь с ИИ-тренером голосом
8. После завершения (кнопка "Остановить") результаты сохранятся автоматически
9. Следующая тренировка разблокируется если score ≥ 70
10. Повторите для всех тренировок

## Особенности

- ✅ **Автоматический парсинг рекомендаций** из анализа GPT
- ✅ **Прогрессивная система** - тренировки открываются последовательно
- ✅ **Непрерывное прослушивание** - естественный диалог с ИИ
- ✅ **Автосохранение результатов** - транскрипт, оценка, обратная связь
- ✅ **Визуальный прогресс** - круговая диаграмма и статистика
- ✅ **Повторное прохождение** - можно улучшить результат
- ✅ **Адаптивный UI** - работает на мобильных устройствах

## Troubleshooting

### Проблема: Таблицы не создались
**Решение:** Проверьте логи Docker:
```bash
docker-compose logs backend | grep "training"
```

### Проблема: Кнопка "Начать тренировку" не работает
**Решение:** Проверьте что роутер подключен в `main.py`:
```python
app.include_router(training_plans.router)
```

### Проблема: Результаты не сохраняются
**Решение:** Проверьте что передаются `training_id` и `session_id` в URL:
```
/voice-assistant/training?training_id=123&session_id=456
```

## Будущие улучшения

- [ ] Интеграция с чеклистами для более детальной оценки
- [ ] AI-анализ тренировки для персонализированных рекомендаций
- [ ] Геймификация (достижения, лидерборды)
- [ ] Экспорт отчётов по всем тренировкам
- [ ] Видео-запись тренировок (опционально)
- [ ] Групповые тренировки с другими пользователями


