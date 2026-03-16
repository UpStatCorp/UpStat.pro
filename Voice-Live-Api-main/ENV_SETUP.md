# Настройка переменных окружения

Проект теперь использует файл `.env` для хранения конфигурации Azure Voice Live API.

## Создание .env файла

1. Создайте файл `.env` в корневой директории проекта
2. Добавьте следующие переменные:

```env
AZURE_ENDPOINT=https://your-resource.cognitiveservices.azure.com/
AZURE_API_KEY=your-api-key-here
```

3. Замените значения на ваши реальные данные:
   - `AZURE_ENDPOINT` - ваш Azure Cognitive Services endpoint
   - `AZURE_API_KEY` - ваш Azure API ключ

## Пример .env файла

```env
AZURE_ENDPOINT=https://myresource.cognitiveservices.azure.com/
AZURE_API_KEY=1234567890abcdef1234567890abcdef
```

## Важно

- Файл `.env` не должен попадать в систему контроля версий (git)
- Убедитесь, что файл `.env` находится в той же директории, что и `serve.py`
- После изменения `.env` файла перезапустите сервер

## Использование

После создания `.env` файла:
1. Запустите сервер: `python serve.py`
2. Поля для ввода endpoint и API key на сайте будут скрыты
3. Значения будут автоматически загружены из `.env` файла

