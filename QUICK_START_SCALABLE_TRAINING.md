# 🚀 Быстрый запуск масштабируемой голосовой тренировки

## Что нового?

✅ **Каждый пользователь** имеет свою изолированную сессию  
✅ **100+ пользователей** могут тренироваться одновременно  
✅ **Всё сохраняется** в базу данных автоматически  
✅ **История диалогов** не смешивается между пользователями  

## Быстрый старт (3 шага)

### 1️⃣ Применить миграцию БД

```bash
cd app
alembic upgrade head
```

### 2️⃣ Запустить сервер

```bash
# Из корня проекта
python app/main.py
```

### 3️⃣ Проверить работу

Откройте в браузере:
```
http://localhost:8000/voice-training/stats
```

Вы должны увидеть:
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

## Использование

### Клиентская сторона

Ничего не меняется! Код в `voice-training.js` уже обновлён:
- Автоматически передаёт JWT токен
- Автоматически передаёт training_id из URL
- Работает с новым endpoint `/voice-training/ws`

### Где посмотреть сохранённые тренировки?

Все сессии сохраняются в БД:

```sql
-- Посмотреть все сессии пользователя
SELECT * FROM training_sessions 
WHERE user_id = 1 
AND session_type = 'voice'
ORDER BY started_at DESC;

-- Посмотреть сообщения конкретной сессии
SELECT role, text, timestamp 
FROM voice_training_messages 
WHERE session_id = 123
ORDER BY timestamp;
```

## Настройка лимитов

Отредактируйте `voice_assistant/session_manager.py`:

```python
_session_manager = SessionManager(
    max_concurrent_sessions=200,  # Увеличить до 200 пользователей
    max_workers=20                # Больше воркеров для STT
)
```

## Мониторинг в реальном времени

```bash
# Статистика
curl http://localhost:8000/voice-training/stats

# Информация о конкретной сессии
curl http://localhost:8000/voice-training/session/{session_id}

# Принудительно закрыть сессию
curl -X POST http://localhost:8000/voice-training/session/{session_id}/end
```

## Логи

Следите за логами для мониторинга:

```bash
tail -f app/server.log | grep "session="
```

Вы увидите:
```
✨ Создана новая сессия: abc-123 для user_id=42
🔐 Аутентификация успешна: user_id=42, training_id=1
🗣️ Начало речи (session=abc-123)
📝 Распознано: 'Здравствуйте' (session=abc-123)
✅ Запрос обработан (session=abc-123)
```

## Что дальше?

📖 Полная документация: `VOICE_TRAINING_SCALABLE.md`

Там вы найдёте:
- Подробную архитектуру
- Все API endpoints
- Рекомендации по масштабированию
- Решение проблем

## Важно!

⚠️ **Старый endpoint** `/voice-assistant/ws` всё ещё работает (для обратной совместимости)  
✨ **Новый endpoint** `/voice-training/ws` использует изолированные сессии и БД

Для production **используйте только** `/voice-training/ws`!

---

Готово! Теперь ваш голосовой тренер готов к 100+ пользователям! 🎉

