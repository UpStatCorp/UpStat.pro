# Полный мануал по внедрению голосового ассистента в проект

## Оглавление

1. [Обзор](#обзор)
2. [Структура файлов](#структура-файлов)
3. [Шаг 1: Копирование файлов](#шаг-1-копирование-файлов)
4. [Шаг 2: Установка зависимостей](#шаг-2-установка-зависимостей)
5. [Шаг 3: Настройка конфигурации](#шаг-3-настройка-конфигурации)
6. [Шаг 4: Интеграция в существующий проект](#шаг-4-интеграция-в-существующий-проект)
7. [Шаг 5: Тестирование](#шаг-5-тестирование)
8. [Устранение проблем](#устранение-проблем)

---

## Обзор

Интеграция голосового ассистента как отдельной страницы в существующий FastAPI проект. Изменения в основном проекте минимальны - только добавление роутера.

---

## Структура файлов

### Что нужно скопировать из `reactive_voice_trener`:

```
voice_assistant/              # Новая папка в вашем проекте
├── __init__.py              # ✅ Создан
├── router.py                # ✅ Создан (роутер для FastAPI)
├── config.py                # ⚠️ Скопировать из reactive_voice_trener
├── vad.py                    # ⚠️ Скопировать из reactive_voice_trener
├── stt_reactive.py           # ⚠️ Скопировать из reactive_voice_trener
├── gpt_logic.py              # ⚠️ Скопировать из reactive_voice_trener
├── tts_response.py           # ⚠️ Скопировать из reactive_voice_trener
├── utils/                    # ⚠️ Скопировать из reactive_voice_trener
│   ├── __init__.py          # ✅ Создан
│   ├── audio_utils.py       # ⚠️ Скопировать
│   └── logger.py            # ⚠️ Скопировать
└── web/                      # ⚠️ Скопировать из reactive_voice_trener
    └── index.html            # ⚠️ Скопировать
```

---

## Шаг 1: Копирование файлов

### 1.1. Скопируйте файлы из `reactive_voice_trener`

Выполните следующие команды (замените путь на ваш):

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

### 1.2. Обновите WebSocket URL в index.html

Откройте `voice_assistant/web/index.html` и найдите строку с WebSocket URL:

```javascript
// Найдите эту строку (примерно строка 300-350):
const WS_URL = `ws://localhost:8000/ws`;

// Замените на:
const WS_URL = `ws://${window.location.host}/voice-assistant/ws`;
```

Или используйте автоматическое определение:

```javascript
// В начале файла добавьте:
const WS_BASE_URL = window.location.origin;
const WS_URL = `${WS_BASE_URL.replace('http', 'ws')}/voice-assistant/ws`;
```

---

## Шаг 2: Установка зависимостей

### 2.1. Добавьте зависимости в requirements.txt

Добавьте в `requirements.txt` вашего проекта:

```txt
# Голосовой ассистент - зависимости
openai>=1.12.0
faster-whisper>=1.0.0
numpy>=1.24.0
webrtcvad>=2.0.10
elevenlabs>=1.0.0
python-dotenv>=1.0.0
pydub>=0.25.1
websockets>=12.0
```

### 2.2. Установите зависимости

```bash
pip install -r requirements.txt
```

### 2.3. Системные зависимости (если нужно)

**macOS:**
```bash
brew install ffmpeg portaudio
```

**Linux:**
```bash
sudo apt-get update
sudo apt-get install ffmpeg portaudio19-dev python3-dev
```

---

## Шаг 3: Настройка конфигурации

### 3.1. Создайте или обновите .env файл

В корне вашего проекта создайте или обновите `.env`:

```env
# OpenAI API ключ (обязательно)
OPENAI_API_KEY=your-openai-api-key-here

# Модель GPT
GPT_MODEL=gpt-4o-mini

# Провайдер STT (Speech-to-Text)
# "whisper_local" - локальная модель (офлайн)
# "whisper_openai" - OpenAI Whisper API (рекомендуется)
# "elevenlabs" - ElevenLabs STT
STT_PROVIDER=whisper_openai

# Провайдер TTS (Text-to-Speech)
# "openai" - OpenAI TTS
# "elevenlabs" - ElevenLabs TTS (рекомендуется)
TTS_PROVIDER=elevenlabs

# ElevenLabs (опционально, если используете)
ELEVENLABS_API_KEY=your-elevenlabs-api-key-here
ELEVENLABS_VOICE_ID=your-voice-id-here
ELEVENLABS_MODEL=eleven_multilingual_v2
```

### 3.2. Обновите config.py (если нужно)

Если нужно изменить настройки по умолчанию, отредактируйте `voice_assistant/config.py`.

---

## Шаг 4: Интеграция в существующий проект

### 4.1. Обновите main.py

В файле `app/main.py` добавьте импорт и подключите роутер:

```python
# В начале файла добавьте импорт:
from voice_assistant.router import router as voice_assistant_router

# В функции create_app() добавьте:
app.include_router(voice_assistant_router)
```

**Полный пример:**

```python
def create_app() -> FastAPI:
    """Create and configure a FastAPI application."""
    load_dotenv()
    
    # ... существующий код ...
    
    app = FastAPI(title="SaaS MVP (FastAPI)")
    # ... middleware и настройки ...
    
    # Существующие роутеры
    app.include_router(public.router)
    app.include_router(auth.router)
    # ... остальные роутеры ...
    
    # НОВЫЙ: Добавляем роутер голосового ассистента
    from voice_assistant.router import router as voice_assistant_router
    app.include_router(voice_assistant_router)
    
    return app
```

---

## Шаг 5: Тестирование

### 5.1. Запустите сервер

```bash
cd app
uvicorn main:app --reload
```

или

```bash
python app/main.py
```

### 5.2. Откройте страницу

Откройте в браузере:

```
http://localhost:8000/voice-assistant/
```

### 5.3. Проверьте health endpoint

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

---

## Устранение проблем

### Проблема 1: Модуль не найден

**Ошибка:** `ModuleNotFoundError: No module named 'voice_assistant'`

**Решение:**
1. Убедитесь, что `voice_assistant/__init__.py` существует
2. Проверьте, что проект запускается из корневой директории
3. Добавьте корневую директорию в PYTHONPATH:
   ```bash
   export PYTHONPATH="${PYTHONPATH}:$(pwd)"
   ```

### Проблема 2: WebSocket не подключается

**Ошибка:** `WebSocket connection failed`

**Решение:**
1. Проверьте URL в `index.html` (должен быть `/voice-assistant/ws`)
2. Убедитесь, что сервер запущен
3. Проверьте CORS настройки (если нужно)

### Проблема 3: API ключи не работают

**Ошибка:** `OpenAI API key not found`

**Решение:**
1. Проверьте `.env` файл в корне проекта
2. Убедитесь, что `python-dotenv` установлен
3. Перезапустите сервер после изменения `.env`

### Проблема 4: Аудио не распознается

**Решение:**
1. Проверьте разрешения микрофона в браузере
2. Убедитесь, что говорите достаточно громко
3. Проверьте `VAD_THRESHOLD` в `config.py` (можно снизить до 0.2)

### Проблема 5: Модули не найдены при импорте

**Ошибка:** `ImportError: cannot import name 'VAD' from 'voice_assistant.vad'`

**Решение:**
1. Убедитесь, что все файлы скопированы из `reactive_voice_trener`
2. Проверьте, что файлы имеют правильные имена
3. Убедитесь, что в файлах нет синтаксических ошибок

---

## Итоговая структура проекта

После интеграции структура должна выглядеть так:

```
your_project/
├── .env                          # API ключи
├── app/
│   ├── main.py                   # Главный файл (добавлен роутер)
│   └── ...
├── requirements.txt              # Обновлен с зависимостями
├── voice_assistant/              # НОВАЯ ПАПКА
│   ├── __init__.py              # ✅ Создан
│   ├── router.py                # ✅ Создан
│   ├── config.py                # ⚠️ Скопировать
│   ├── vad.py                    # ⚠️ Скопировать
│   ├── stt_reactive.py           # ⚠️ Скопировать
│   ├── gpt_logic.py              # ⚠️ Скопировать
│   ├── tts_response.py           # ⚠️ Скопировать
│   ├── utils/
│   │   ├── __init__.py          # ✅ Создан
│   │   ├── audio_utils.py       # ⚠️ Скопировать
│   │   └── logger.py            # ⚠️ Скопировать
│   └── web/
│       └── index.html            # ⚠️ Скопировать
└── ... (остальные файлы проекта)
```

---

## Использование

После интеграции голосовой ассистент будет доступен по адресу:

- **Веб-интерфейс:** `http://your-domain.com/voice-assistant/`
- **WebSocket:** `ws://your-domain.com/voice-assistant/ws`
- **Health check:** `http://your-domain.com/voice-assistant/health`

---

## Дополнительные настройки

### Изменение префикса маршрута

Если хотите изменить `/voice-assistant` на другой путь:

В `voice_assistant/router.py`:

```python
router = APIRouter(prefix="/your-custom-path", tags=["Voice Assistant"])
```

### Добавление аутентификации

Если нужно защитить роуты:

```python
from fastapi import Depends
from app.deps import require_user

@router.get("/")
async def get_index(current_user = Depends(require_user)):
    # ... ваш код
```

---

## Следующие шаги

1. ✅ Структура папок создана
2. ✅ Роутер создан
3. ⚠️ **Скопируйте модули из `reactive_voice_trener`**
4. ⚠️ **Обновите `main.py` для подключения роутера**
5. ⚠️ **Обновите `requirements.txt` с зависимостями**
6. ⚠️ **Настройте `.env` файл**
7. ⚠️ **Обновите WebSocket URL в `index.html`**

---

**Готово!** Голосовой ассистент интегрирован как отдельная страница в ваш проект. Если нужны дополнительные детали или помощь с конкретными шагами, дайте знать.


