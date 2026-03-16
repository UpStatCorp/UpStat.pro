# Исправление проблемы с загрузкой модулей в Docker

## Проблема

Health check возвращает:
```json
{
  "status": "partial",
  "components": {
    "vad": "not_available",
    "stt": "not_available",
    "gpt": "not_available",
    "tts": "not_available"
  }
}
```

## Причина

Модули `voice_assistant` не загружаются в Docker контейнере из-за проблем с путями импорта.

## Решение

### 1. Обновлен Dockerfile

Добавлен `voice_assistant` в PYTHONPATH:
```dockerfile
ENV PYTHONPATH=/app:/app/voice_assistant
```

### 2. Обновлен app/main.py

Добавлена явная настройка sys.path перед импортом:
```python
import sys
import os
voice_assistant_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'voice_assistant')
if voice_assistant_path not in sys.path:
    sys.path.insert(0, voice_assistant_path)
```

## Шаги для применения исправлений

### 1. Пересобрать Docker образ

```bash
docker-compose down
docker-compose build --no-cache backend
docker-compose up -d
```

### 2. Проверить логи

```bash
docker-compose logs -f backend | grep -i "voice"
```

Должны увидеть:
```
Voice assistant router loaded successfully
```

### 3. Проверить health endpoint

```bash
curl http://localhost:8000/voice-assistant/health
```

Должен вернуть:
```json
{
  "status": "ok",
  "components": {
    "vad": "ready",
    "stt": "ready",
    "gpt": "ready",
    "tts": "ready"
  }
}
```

### 4. Если проблема сохраняется

Проверьте структуру файлов в контейнере:

```bash
docker-compose exec backend ls -la /app/voice_assistant/
```

Должны увидеть все файлы:
- `__init__.py`
- `router.py`
- `config.py`
- `vad.py`
- `stt_reactive.py`
- `gpt_logic.py`
- `tts_response.py`
- `utils/`
- `web/`

### 5. Проверьте импорты в контейнере

```bash
docker-compose exec backend python3 -c "from voice_assistant.router import router; print('OK')"
```

Если видите ошибку, проверьте логи:
```bash
docker-compose exec backend python3 -c "from voice_assistant.router import router" 2>&1
```

## Альтернативное решение

Если проблема сохраняется, можно использовать абсолютный импорт:

В `app/main.py`:
```python
import sys
sys.path.insert(0, '/app')
from voice_assistant.router import router as voice_assistant_router
```


