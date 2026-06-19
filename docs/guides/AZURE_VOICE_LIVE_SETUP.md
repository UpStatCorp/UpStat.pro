# Настройка Azure Voice Live API

## Описание

Система голосовых тренировок теперь использует Azure Voice Live API для real-time взаимодействия с AI тренером. Это обеспечивает:

- ✅ Низкую задержку (real-time)
- ✅ Поддержку прерывания (barge-in)
- ✅ Высокое качество голоса (Azure Neural Voices)
- ✅ Встроенную обработку аудио (шумоподавление, эхоподавление)

## Требования

1. Azure аккаунт с доступом к Cognitive Services
2. Ресурс Azure Speech Services с поддержкой Voice Live API
3. API ключ или настроенная Azure AD аутентификация

## Настройка переменных окружения

Добавьте в файл `.env` следующие переменные:

```env
# Azure Voice Live API
AZURE_VOICE_LIVE_ENDPOINT=https://your-resource.cognitiveservices.azure.com/
AZURE_VOICE_LIVE_API_KEY=your-api-key-here
AZURE_VOICE_LIVE_MODEL=gpt-4o-realtime-preview
AZURE_VOICE_LIVE_API_VERSION=2025-05-01-preview
AZURE_VOICE_LIVE_VOICE=en-US-Ava:DragonHDLatestNeural
AZURE_VOICE_LIVE_TRANSCRIPTION_MODEL=gpt-4o-transcribe

# Включить Azure Voice Live API (по умолчанию true)
USE_AZURE_VOICE_LIVE=true
```

### Параметры

- **AZURE_VOICE_LIVE_ENDPOINT**: Endpoint вашего Azure Cognitive Services ресурса
- **AZURE_VOICE_LIVE_API_KEY**: API ключ (или используйте Azure AD токен)
- **AZURE_VOICE_LIVE_MODEL**: Модель для использования:
  - `gpt-4o-realtime-preview` (рекомендуется)
  - `gpt-4o-mini-realtime-preview`
  - `gpt-4o`
  - `gpt-4o-mini`
- **AZURE_VOICE_LIVE_VOICE**: Голос для синтеза речи (Azure Neural Voices)
- **AZURE_VOICE_LIVE_TRANSCRIPTION_MODEL**: Модель для транскрипции речи пользователя

## Альтернативная аутентификация через Azure AD

Если вы не хотите использовать API ключ, можно использовать Azure AD токен:

1. Установите Azure CLI: `az login`
2. Убедитесь что `AZURE_VOICE_LIVE_API_KEY` не установлен
3. Система автоматически получит токен через `DefaultAzureCredential`

## Установка зависимостей

Установите необходимые пакеты:

```bash
pip install -r requirements.txt
```

Или отдельно:

```bash
pip install azure-identity azure-core websockets
```

## Проверка работы

1. Убедитесь что все переменные окружения установлены
2. Запустите сервер
3. Откройте страницу тренировки
4. Нажмите "Начать тренировку"
5. Проверьте что соединение установлено и вы видите сообщение "✅ Подключение установлено"

## Отключение Azure Voice Live API

Если нужно вернуться к старой системе (STT -> GPT -> TTS), установите:

```env
USE_AZURE_VOICE_LIVE=false
```

## Доступные голоса

Azure Neural Voices поддерживает множество голосов. Примеры:

- `en-US-Ava:DragonHDLatestNeural` (женский, HD)
- `en-US-Andrew:DragonHDLatestNeural` (мужской, HD)
- `en-US-JennyMultilingualNeural` (женский, многоязычный)
- `en-US-GuyNeural` (мужской)

Полный список: https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts

## Устранение проблем

### Ошибка "Azure endpoint not configured"

Проверьте что `AZURE_VOICE_LIVE_ENDPOINT` установлен в `.env`

### Ошибка "Azure authentication failed"

- Проверьте правильность API ключа
- Или убедитесь что Azure CLI настроен (`az login`)

### Ошибка подключения к WebSocket

- Проверьте что endpoint правильный (должен быть `https://...`)
- Убедитесь что ресурс Azure имеет доступ к Voice Live API
- Проверьте что модель доступна в вашем регионе

## Дополнительная информация

- [Azure Voice Live API документация](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/voice-live-api)
- [Azure Neural Voices](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts)

