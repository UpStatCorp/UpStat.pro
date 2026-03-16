# Инструкция по перезапуску Docker для голосового ассистента

## Что было изменено:

1. ✅ Обновлен `Dockerfile` - добавлено копирование `voice_assistant/`
2. ✅ Обновлен `docker-compose.yml` - добавлен volume mount для `voice_assistant/`
3. ✅ Обновлен `requirements.txt` - добавлены зависимости для голосового ассистента

## Шаги для перезапуска:

### 1. Остановить текущие контейнеры

```bash
docker-compose down
```

### 2. Пересобрать образ (чтобы установить новые зависимости)

```bash
docker-compose build --no-cache backend
```

Или пересобрать все сервисы:

```bash
docker-compose build --no-cache
```

### 3. Запустить контейнеры

```bash
docker-compose up -d
```

### 4. Проверить логи

```bash
docker-compose logs -f backend
```

Должны увидеть:
```
Voice assistant router loaded successfully
```

Если модули еще не скопированы, увидите:
```
Voice assistant router not available: ...
```

## Быстрая команда (все сразу):

```bash
docker-compose down && \
docker-compose build --no-cache backend && \
docker-compose up -d && \
docker-compose logs -f backend
```

## Проверка работоспособности:

После перезапуска проверьте:

1. Health check голосового ассистента:
   ```
   http://localhost:8000/voice-assistant/health
   ```

2. Страница голосового ассистента:
   ```
   http://localhost:8000/voice-assistant/
   ```

## Важно:

- `--no-cache` нужен, чтобы переустановить зависимости из обновленного `requirements.txt`
- Volume mount `./voice_assistant:/app/voice_assistant` позволяет редактировать код без пересборки образа
- Если модули еще не скопированы в `voice_assistant/`, роутер загрузится, но компоненты будут недоступны


