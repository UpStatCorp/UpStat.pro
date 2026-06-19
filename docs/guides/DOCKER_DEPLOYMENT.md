# 🐳 Инструкция по развертыванию с Docker

## Проблема и решение

**Проблема:** После добавления улучшений приложение не запускается в Docker из-за отсутствия зависимости `python-magic`.

**Решение:** Обновлен `requirements.txt` с добавлением `python-magic>=0.4.27`

---

## 🚀 Развертывание

### 1. Пересоздать Docker контейнеры

После обновления `requirements.txt` необходимо пересобрать образы:

```bash
# Остановить текущие контейнеры
docker-compose down

# Пересобрать образы (с очисткой кеша)
docker-compose build --no-cache

# Запустить контейнеры
docker-compose up -d
```

### 2. Проверить статус

```bash
# Проверить статус контейнеров
docker-compose ps

# Посмотреть логи
docker-compose logs -f backend
```

### 3. Проверить работу приложения

Откройте в браузере: http://localhost:8000

---

## 📦 Обновленные зависимости

Добавлено в `requirements.txt`:
```
# Валидация файлов
python-magic>=0.4.27
```

---

## 🔧 Альтернативный вариант (без пересборки)

Если не хотите пересобирать образы, можно установить зависимость в работающем контейнере:

```bash
# Установить зависимость в работающий контейнер
docker-compose exec backend pip install python-magic>=0.4.27

# Перезапустить контейнер
docker-compose restart backend
```

⚠️ **Внимание:** Этот способ временный - при пересоздании контейнера зависимость пропадет.

---

## 🐛 Отладка проблем

### Проблема: ModuleNotFoundError: No module named 'magic'

**Решение:**
1. Убедитесь, что `python-magic>=0.4.27` есть в `requirements.txt`
2. Пересоберите образ: `docker-compose build --no-cache backend`
3. Перезапустите контейнер: `docker-compose up -d backend`

### Проблема: Контейнер постоянно перезапускается

**Проверка логов:**
```bash
docker-compose logs --tail=100 backend
```

**Возможные причины:**
- Ошибки импорта модулей → проверьте requirements.txt
- Ошибки в коде → проверьте логи
- Проблемы с БД → проверьте подключение к redis/postgres

### Проблема: Приложение не отвечает

**Проверка:**
```bash
# Проверить, слушает ли порт 8000
docker-compose exec backend netstat -tlnp | grep 8000

# Проверить health check
curl http://localhost:8000/
```

---

## 📋 Чеклист после развертывания

- [ ] Контейнеры запущены (`docker-compose ps`)
- [ ] Логи без ошибок (`docker-compose logs backend`)
- [ ] Приложение доступно по http://localhost:8000
- [ ] Можно войти в систему
- [ ] Можно загрузить файл
- [ ] Валидация файлов работает корректно

---

## 🔄 Обновление production

Для обновления production сервера:

```bash
# 1. Сделать backup БД
docker-compose exec backend python -c "import shutil; shutil.copy('app.db', 'app.db.backup')"

# 2. Остановить приложение
docker-compose down

# 3. Обновить код (git pull или rsync)
git pull origin main

# 4. Пересобрать образы
docker-compose build --no-cache

# 5. Запустить
docker-compose up -d

# 6. Проверить логи
docker-compose logs -f backend
```

---

## 💡 Рекомендации

### Для разработки
```bash
# Использовать volume mount для live reload
docker-compose up -d
docker-compose logs -f backend
```

### Для production
```bash
# Использовать .env файл с production настройками
cp .env.example .env
nano .env  # Настроить переменные

# Запустить с production настройками
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

---

## 📊 Мониторинг

### Проверка использования ресурсов
```bash
docker stats
```

### Проверка логов
```bash
# Backend
docker-compose logs -f backend

# AI Agent Service
docker-compose logs -f ai_agent_service

# Redis
docker-compose logs -f redis
```

---

## 🆘 Поддержка

Если проблемы продолжаются:

1. Проверьте логи: `docker-compose logs --tail=200 backend`
2. Проверьте requirements.txt
3. Убедитесь, что все новые файлы скопированы в контейнер
4. Пересоберите с нуля: `docker-compose down -v && docker-compose build --no-cache && docker-compose up -d`

---

*Документ создан: 12 ноября 2025*

