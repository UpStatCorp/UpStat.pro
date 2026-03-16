# Устранение проблем с голосовым ассистентом

## Проблема: "Отключено. Переподключение..."

### Причины:

1. **Модули не загружены** - WebSocket закрывается, если модули (VAD, STT, GPT, TTS) не инициализированы
2. **Ошибки импорта** - неправильные относительные импорты
3. **Отсутствие зависимостей** - не установлены необходимые пакеты

### Решение:

#### 1. Проверьте логи сервера

```bash
# Если используете Docker
docker-compose logs -f backend

# Если запускаете локально
# Проверьте вывод сервера при запуске
```

Должны увидеть:
```
Voice assistant router loaded successfully
```

Если видите:
```
Voice assistant router not available: ...
```
То модули не загружены.

#### 2. Проверьте импорты

Все импорты должны быть относительными:
- `from .config import ...` (не `from config import ...`)
- `from .utils.logger import ...` (не `from utils.logger import ...`)

#### 3. Проверьте доступность модулей

```bash
python3 -c "from voice_assistant.router import router, vad, stt, gpt, tts; print(f'VAD: {vad is not None}, STT: {stt is not None}, GPT: {gpt is not None}, TTS: {tts is not None}')"
```

Все должны быть `True`.

#### 4. Проверьте health endpoint

```
http://localhost:8000/voice-assistant/health
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

#### 5. Проверьте WebSocket URL

В `voice_assistant/web/index.html` должен быть:
```javascript
const WS_URL = `ws://${window.location.host}/voice-assistant/ws`;
```

#### 6. Перезапустите сервер

```bash
# Docker
docker-compose restart backend

# Локально
# Перезапустите uvicorn
```

## Исправленные проблемы:

✅ Исправлены все относительные импорты:
- `from config import ...` → `from .config import ...`
- `from utils.logger import ...` → `from .utils.logger import ...`
- `from utils.audio_utils import ...` → `from .utils.audio_utils import ...`

✅ WebSocket endpoint теперь принимает соединение перед проверкой модулей

## Следующие шаги:

1. Перезапустите Docker контейнеры:
   ```bash
   docker-compose down
   docker-compose build --no-cache backend
   docker-compose up -d
   ```

2. Проверьте логи:
   ```bash
   docker-compose logs -f backend
   ```

3. Откройте страницу:
   ```
   http://localhost:8000/voice-assistant/
   ```

4. Проверьте консоль браузера (F12) на наличие ошибок WebSocket


