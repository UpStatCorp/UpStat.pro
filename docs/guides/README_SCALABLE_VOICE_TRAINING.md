# 🎤 Масштабируемая голосовая тренировка

> **Поддержка 100+ одновременных пользователей с изолированными сессиями**

## 🎯 Основные преимущества

| Характеристика | До | После |
|----------------|----|----- |
| **Одновременные пользователи** | 1-5 (общие компоненты) | 100+ (изолированные сессии) |
| **История диалога** | ❌ Смешивается | ✅ Отдельная для каждого |
| **Сохранение в БД** | ❌ Нет | ✅ Автоматически |
| **Аутентификация** | ❌ Нет | ✅ JWT токен |
| **Мониторинг** | ❌ Нет | ✅ Статистика в реальном времени |
| **Масштабируемость** | ❌ Ограничена | ✅ До 100+ пользователей |

## 🏗️ Архитектура

```
👤 Пользователь 1                    👤 Пользователь 2                    👤 Пользователь N
     │                                    │                                    │
     │ WebSocket + JWT                    │ WebSocket + JWT                    │ WebSocket + JWT
     │                                    │                                    │
     └────────────────────────────────────┼────────────────────────────────────┘
                                          │
                                          ↓
                            ┌─────────────────────────┐
                            │   FastAPI Server        │
                            │   router_new.py         │
                            │   - Аутентификация      │
                            │   - Endpoint: /ws       │
                            └─────────────────────────┘
                                          │
                                          ↓
                            ┌─────────────────────────┐
                            │  websocket_handler.py   │
                            │  - Обработка сообщений  │
                            │  - VAD                  │
                            │  - Pipeline управление  │
                            └─────────────────────────┘
                                          │
                                          ↓
                            ┌─────────────────────────┐
                            │   SessionManager        │
                            │   - Лимит: 100 сессий   │
                            │   - Пул: 10 воркеров    │
                            └─────────────────────────┘
                    ┌───────────────┼───────────────┐
                    ↓               ↓               ↓
        ┌───────────────┐ ┌───────────────┐ ┌───────────────┐
        │ UserSession 1 │ │ UserSession 2 │ │ UserSession N │
        │ - VAD        │ │ - VAD        │ │ - VAD        │
        │ - STT        │ │ - STT        │ │ - STT        │
        │ - GPT        │ │ - GPT        │ │ - GPT        │
        │ - TTS        │ │ - TTS        │ │ - TTS        │
        │ - История    │ │ - История    │ │ - История    │
        └───────────────┘ └───────────────┘ └───────────────┘
                    │               │               │
                    └───────────────┼───────────────┘
                                    ↓
                        ┌───────────────────────┐
                        │ VoiceTrainingDBService│
                        │ - Сохранение сессий   │
                        │ - Сохранение сообщений│
                        │ - История диалога     │
                        └───────────────────────┘
                                    │
                                    ↓
                        ┌───────────────────────┐
                        │  База данных          │
                        │  - training_sessions  │
                        │  - voice_messages     │
                        └───────────────────────┘
```

## 🚀 Быстрый старт (3 команды)

### 1. Миграция БД
```bash
cd app && alembic upgrade head
```

### 2. Запуск сервера
```bash
python app/main.py
```

### 3. Проверка
```bash
curl http://localhost:8000/voice-training/stats
```

**Ожидаемый ответ:**
```json
{
  "status": "ok",
  "sessions": {
    "total_sessions": 0,
    "max_sessions": 100,
    "capacity_percent": 0,
    "stt_workers": 10,
    "active_users": 0
  }
}
```

## 📊 Мониторинг в реальном времени

### Статистика сервера
```bash
# Текущая загрузка
curl http://localhost:8000/voice-training/stats

# Информация о конкретной сессии
curl http://localhost:8000/voice-training/session/{uuid}

# Закрыть сессию
curl -X POST http://localhost:8000/voice-training/session/{uuid}/end
```

### Логи
```bash
# Следить за сессиями
tail -f app/server.log | grep "session="

# Фильтр по user_id
tail -f app/server.log | grep "user_id=42"
```

### База данных
```sql
-- Активные сессии прямо сейчас
SELECT user_id, training_id, websocket_session_id, started_at
FROM training_sessions
WHERE status = 'active' AND session_type = 'voice';

-- Топ-10 самых активных пользователей
SELECT user_id, COUNT(*) as sessions_count, 
       SUM(duration_seconds) as total_time
FROM training_sessions
WHERE session_type = 'voice'
GROUP BY user_id
ORDER BY sessions_count DESC
LIMIT 10;

-- История диалога конкретной сессии
SELECT role, text, timestamp
FROM voice_training_messages
WHERE session_id = 123
ORDER BY timestamp;
```

## 🎛️ Настройка производительности

### Увеличить количество пользователей

Отредактируйте `voice_assistant/session_manager.py`:

```python
def get_session_manager() -> SessionManager:
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager(
            max_concurrent_sessions=200,  # ← Было 100
            max_workers=20                # ← Было 10
        )
    return _session_manager
```

### Настройка VAD

В `voice_assistant/websocket_handler.py`:

```python
# Параметры VAD
SILENCE_THRESHOLD = 0.65  # Секунды тишины (меньше = быстрее)
SPEECH_THRESHOLD = 0.02   # Порог речи (меньше = чувствительнее)
```

### Таймаут неактивности

```python
# Очищать сессии неактивные > 30 минут
await session_manager.cleanup_inactive_sessions(timeout_seconds=1800)
```

## 📈 Масштабирование

### 🟢 100-200 пользователей (текущая конфигурация)
- ✅ Один сервер
- CPU: 4-8 ядер
- RAM: 8-16 GB
- Изменения: не требуются

### 🟡 200-500 пользователей
```python
SessionManager(
    max_concurrent_sessions=500,
    max_workers=30
)
```
- CPU: 8-16 ядер
- RAM: 32 GB
- PostgreSQL вместо SQLite

### 🔴 500+ пользователей

#### Вариант 1: Вертикальное масштабирование
- Мощный выделенный сервер
- PostgreSQL на отдельном сервере
- Redis для кэширования

#### Вариант 2: Горизонтальное масштабирование
```
          Load Balancer (nginx)
                  │
       ┌──────────┼──────────┐
       ↓          ↓          ↓
   FastAPI 1  FastAPI 2  FastAPI N
       │          │          │
       └──────────┼──────────┘
                  ↓
          PostgreSQL + Redis
```

**Конфигурация nginx:**
```nginx
upstream voice_training {
    ip_hash;  # Sticky sessions по IP
    server 127.0.0.1:8001;
    server 127.0.0.1:8002;
    server 127.0.0.1:8003;
}

server {
    location /voice-training/ws {
        proxy_pass http://voice_training;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

## 🔐 Безопасность

### JWT аутентификация

WebSocket требует валидный токен:
```
wss://server/voice-training/ws?token=<JWT>&training_id=<ID>
```

### Получение токена

```javascript
// После логина
const response = await fetch('/api/login', {
    method: 'POST',
    body: JSON.stringify({email, password})
});
const {access_token} = await response.json();
localStorage.setItem('access_token', access_token);
```

### Проверка токена на сервере

```python
# voice_assistant/router_new.py
user = await get_current_user_from_token(token, db)
if not user:
    await websocket.close(code=1008, reason="Unauthorized")
    return
```

## 📚 API Reference

### WebSocket протокол

#### Клиент → Сервер

| Тип | Данные | Описание |
|-----|--------|----------|
| `audio` | `{audio: base64}` | Аудио чанк (Float32Array) |
| `text` | `{text: string}` | Текстовый запрос |
| `stop` | `{}` | Остановить обработку |
| `end_session` | `{}` | Завершить сессию |

#### Сервер → Клиент

| Тип | Данные | Описание |
|-----|--------|----------|
| `connected` | `{session_id, message}` | Подключение OK |
| `status` | `{status, message}` | Статус: listening/thinking/synthesizing |
| `user_text` | `{text}` | Распознанный текст |
| `ai_text` | `{text}` | Ответ ИИ |
| `audio_start` | `{}` | Начало озвучивания |
| `audio_chunk` | `{audio: base64}` | Чанк аудио (WAV) |
| `audio_end` | `{}` | Конец озвучивания |
| `error` | `{message}` | Ошибка |

### REST API

| Метод | Endpoint | Описание |
|-------|----------|----------|
| GET | `/voice-training/stats` | Статистика сервера |
| GET | `/voice-training/session/{id}` | Инфо о сессии |
| POST | `/voice-training/session/{id}/end` | Закрыть сессию |

## 🐛 Решение проблем

### ❌ "Достигнут лимит пользователей"

**Причина:** Все 100 слотов заняты

**Решение:**
```python
# Увеличить лимит в session_manager.py
max_concurrent_sessions=200

# ИЛИ очистить неактивные сессии
await session_manager.cleanup_inactive_sessions()
```

### ❌ "Unauthorized: Invalid token"

**Причина:** Токен невалидный или истёк

**Решение:**
```javascript
// Проверить наличие токена
const token = localStorage.getItem('access_token');
if (!token) {
    // Перенаправить на логин
    window.location.href = '/login';
}
```

### ❌ Медленная обработка

**Причина:** STT воркеры перегружены

**Решение:**
```python
# Увеличить количество воркеров
max_workers=20  # Было 10
```

### ❌ Сессия не сохраняется в БД

**Причина:** Миграция не применена

**Решение:**
```bash
cd app && alembic upgrade head
```

## 📁 Структура файлов

```
voice_assistant/
├── session_manager.py      # Менеджер сессий
├── db_service.py           # Сервис БД
├── websocket_handler.py    # WebSocket обработчик
├── router_new.py           # API endpoints
├── vad.py                  # Voice Activity Detection
├── stt_reactive.py         # Speech-to-Text
├── gpt_logic.py            # GPT диалог
└── tts_response.py         # Text-to-Speech

app/
├── models.py               # Модели БД (обновлено)
└── main.py                 # Подключение роутеров (обновлено)

alembic/versions/
└── 005_add_voice_training_fields.py  # Миграция

Документация:
├── VOICE_TRAINING_SCALABLE.md        # Полная документация
├── QUICK_START_SCALABLE_TRAINING.md  # Быстрый старт
└── SCALABLE_TRAINING_SUMMARY.md      # Резюме изменений
```

## 🎉 Результат

✅ **Масштабируется** до 100+ пользователей  
✅ **Изолирует** каждого пользователя  
✅ **Сохраняет** всё в БД автоматически  
✅ **Защищён** JWT аутентификацией  
✅ **Мониторится** в реальном времени  
✅ **Готов** к production  

---

**Поддержка:** Смотрите полную документацию в `VOICE_TRAINING_SCALABLE.md`  
**Быстрый старт:** `QUICK_START_SCALABLE_TRAINING.md`  
**Версия:** 1.0 | **Дата:** 12 января 2025

