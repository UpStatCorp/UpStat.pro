# Голосовой ассистент - Инструкция по интеграции

## ✅ Что уже сделано

1. ✅ Создана структура папок `voice_assistant/`
2. ✅ Создан роутер `router.py` для FastAPI
3. ✅ Обновлен `app/main.py` для подключения роутера
4. ✅ Обновлен `requirements.txt` с зависимостями
5. ✅ Создан мануал `VOICE_ASSISTANT_INTEGRATION.md`

## ⚠️ Что нужно сделать

### Шаг 1: Скопировать модули из `reactive_voice_trener`

Скопируйте следующие файлы из вашего проекта `reactive_voice_trener`:

```bash
# Основные модули
cp reactive_voice_trener/config.py voice_assistant/
cp reactive_voice_trener/vad.py voice_assistant/
cp reactive_voice_trener/stt_reactive.py voice_assistant/
cp reactive_voice_trener/gpt_logic.py voice_assistant/
cp reactive_voice_trener/tts_response.py voice_assistant/

# Utils
cp reactive_voice_trener/utils/audio_utils.py voice_assistant/utils/
cp reactive_voice_trener/utils/logger.py voice_assistant/utils/

# Веб-интерфейс
cp reactive_voice_trener/web/index.html voice_assistant/web/
```

### Шаг 2: Обновить WebSocket URL в index.html

Откройте `voice_assistant/web/index.html` и найдите строку с WebSocket URL:

```javascript
// Найдите эту строку (примерно строка 300-350):
const WS_URL = `ws://localhost:8000/ws`;

// Замените на:
const WS_URL = `ws://${window.location.host}/voice-assistant/ws`;
```

### Шаг 3: Установить зависимости

```bash
pip install -r requirements.txt
```

### Шаг 4: Настроить .env файл

Добавьте в `.env` файл в корне проекта:

```env
# OpenAI API ключ (обязательно)
OPENAI_API_KEY=your-openai-api-key-here

# Модель GPT
GPT_MODEL=gpt-4o-mini

# Провайдер STT (Speech-to-Text)
STT_PROVIDER=whisper_openai

# Провайдер TTS (Text-to-Speech)
TTS_PROVIDER=elevenlabs

# ElevenLabs (опционально, если используете)
ELEVENLABS_API_KEY=your-elevenlabs-api-key-here
ELEVENLABS_VOICE_ID=your-voice-id-here
ELEVENLABS_MODEL=eleven_multilingual_v2
```

### Шаг 5: Запустить сервер

```bash
cd app
uvicorn main:app --reload
```

### Шаг 6: Открыть страницу

Откройте в браузере:

```
http://localhost:8000/voice-assistant/
```

## Проверка работоспособности

### Health check

Откройте:

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

Если модули еще не скопированы, вернется:

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

## Структура файлов

После копирования модулей структура должна выглядеть так:

```
voice_assistant/
├── __init__.py              ✅ Создан
├── router.py                ✅ Создан
├── config.py                ⚠️ Скопировать
├── vad.py                    ⚠️ Скопировать
├── stt_reactive.py           ⚠️ Скопировать
├── gpt_logic.py              ⚠️ Скопировать
├── tts_response.py           ⚠️ Скопировать
├── utils/
│   ├── __init__.py          ✅ Создан
│   ├── audio_utils.py       ⚠️ Скопировать
│   └── logger.py            ⚠️ Скопировать
└── web/
    └── index.html           ⚠️ Скопировать
```

## Дополнительная информация

Полный мануал по интеграции находится в файле `VOICE_ASSISTANT_INTEGRATION.md` в корне проекта.

## Поддержка

Если возникли проблемы, проверьте:

1. Все файлы скопированы из `reactive_voice_trener`
2. WebSocket URL обновлен в `index.html`
3. Зависимости установлены (`pip install -r requirements.txt`)
4. `.env` файл настроен с API ключами
5. Сервер запущен и доступен


