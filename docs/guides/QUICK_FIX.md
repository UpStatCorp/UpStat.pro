# 🚨 Быстрое исправление проблемы с Docker

## Проблема
```
ModuleNotFoundError: No module named 'magic'
```

## ✅ Решение (2 минуты)

### Вариант 1: Пересборка (рекомендуется)

```bash
# Перейти в директорию проекта
cd "путь/к/проекту"

# Остановить контейнеры
docker-compose down

# Пересобрать с новыми зависимостями
docker-compose build --no-cache backend

# Запустить
docker-compose up -d

# Проверить логи
docker-compose logs -f backend
```

### Вариант 2: Быстрая установка (временное решение)

```bash
# Установить зависимость в работающий контейнер
docker-compose exec backend pip install python-magic

# Перезапустить
docker-compose restart backend
```

---

## 📝 Что было исправлено

1. ✅ Добавлено `python-magic>=0.4.27` в `requirements.txt`
2. ✅ Обновлен `app/utils/file_validator.py` - теперь работает без python-magic (fallback)
3. ✅ Обновлен `Dockerfile` - копируются все необходимые директории

---

## 🎯 Проверка работы

После перезапуска проверьте:

```bash
# 1. Контейнер запущен?
docker-compose ps

# 2. Нет ошибок в логах?
docker-compose logs --tail=50 backend

# 3. Приложение отвечает?
curl http://localhost:8000
```

Если все ОК, откройте в браузере: **http://localhost:8000**

---

## 💾 Что было добавлено в проект

### Новые модули (работают!):
- ✅ `app/services/error_handler.py` - обработка ошибок
- ✅ `app/utils/file_validator.py` - валидация файлов (с fallback)
- ✅ `app/middleware/rate_limit.py` - защита от DDoS
- ✅ `app/security.py` - улучшенная безопасность

### Обновленные файлы:
- ✅ `requirements.txt` - добавлена зависимость
- ✅ `Dockerfile` - копируются все директории
- ✅ `app/routers/chat.py` - интегрирована валидация
- ✅ `app/routers/chat_trener.py` - интегрирована валидация
- ✅ `app/services/pipeline.py` - улучшенная обработка ошибок

---

## 🔥 Если не работает

### Полная очистка и пересборка:

```bash
# Остановить и удалить все
docker-compose down -v

# Очистить образы (опционально)
docker system prune -f

# Пересобрать с нуля
docker-compose build --no-cache

# Запустить
docker-compose up -d

# Следить за логами
docker-compose logs -f backend
```

---

## 📞 Нужна помощь?

Проверьте:
1. `requirements.txt` содержит `python-magic>=0.4.27` ✓
2. Dockerfile обновлен ✓
3. Выполнена пересборка: `docker-compose build --no-cache` ✓

Если проблема остается, отправьте логи:
```bash
docker-compose logs backend > logs.txt
```

---

*Создано: 12 ноября 2025*

