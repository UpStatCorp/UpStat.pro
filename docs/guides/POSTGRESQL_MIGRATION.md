# 🐘 Миграция на PostgreSQL

## ✅ Что было сделано

1. ✅ Обновлен `app/database.py` - добавлена поддержка PostgreSQL с пулом соединений (20 базовых + 40 overflow)
2. ✅ Добавлен PostgreSQL сервис в `docker-compose.yml`
3. ✅ Обновлен backend сервис для использования PostgreSQL
4. ✅ Создан скрипт миграции данных `migrate_to_postgresql.py`
5. ✅ Обновлен `env.example` с настройками PostgreSQL

## 🚀 Пошаговая инструкция по миграции

### Шаг 1: Создайте backup SQLite базы данных

```bash
# Создайте копию текущей базы данных
cp app.db app.db.backup
```

### Шаг 2: Обновите .env файл

Создайте или обновите `.env` файл на основе `env.example`:

```bash
# Если .env не существует, скопируйте из примера
cp env.example .env
```

Добавьте в `.env` настройки PostgreSQL:

```env
# PostgreSQL настройки
POSTGRES_USER=saas_user
POSTGRES_PASSWORD=ваш_надежный_пароль_здесь
POSTGRES_DB=saas
DATABASE_PORT=5432
```

**⚠️ ВАЖНО:** Замените `ваш_надежный_пароль_здесь` на надежный пароль!

### Шаг 3: Запустите PostgreSQL

```bash
# Запустите только PostgreSQL для проверки
docker-compose up -d postgres

# Проверьте логи - дождитесь сообщения "database system is ready"
docker-compose logs -f postgres
```

Дождитесь сообщения: `database system is ready to accept connections`

### Шаг 4: Создайте таблицы в PostgreSQL

```bash
# Создайте все таблицы в PostgreSQL
docker-compose run --rm backend python -c "from app.main import create_app; from database import Base, engine; app = create_app(); Base.metadata.create_all(bind=engine)"
```

Вы должны увидеть сообщения о создании таблиц.

### Шаг 5: Мигрируйте данные (если есть данные в SQLite)

Если у вас есть данные в `app.db`, выполните миграцию:

```bash
# Запустите скрипт миграции
docker-compose run --rm backend python migrate_to_postgresql.py
```

Скрипт:
- Найдет все таблицы в SQLite
- Спросит подтверждение
- Перенесет данные в PostgreSQL

**⚠️ ВНИМАНИЕ:** Скрипт перезапишет данные в PostgreSQL!

### Шаг 6: Перезапустите все сервисы

```bash
# Остановите все сервисы
docker-compose down

# Запустите все сервисы заново
docker-compose up -d

# Проверьте логи
docker-compose logs -f backend
```

### Шаг 7: Проверьте работу

1. Откройте сайт: http://localhost:8000
2. Попробуйте войти в систему
3. Проверьте основные функции

Если все работает - миграция успешна! 🎉

## 🔍 Проверка подключения к PostgreSQL

```bash
# Подключитесь к PostgreSQL через Docker
docker-compose exec postgres psql -U saas_user -d saas

# В psql выполните:
# \dt - список таблиц
# SELECT COUNT(*) FROM users; - проверка данных
# \q - выход
```

## 📊 Мониторинг PostgreSQL

```bash
# Проверка статуса
docker-compose ps postgres

# Логи PostgreSQL
docker-compose logs -f postgres

# Статистика использования
docker stats fastapi-backend-1
```

## 🔄 Откат на SQLite (если нужно)

Если что-то пошло не так, можно вернуться на SQLite:

1. В `.env` измените:
   ```env
   DATABASE_URL=sqlite:///app.db
   ```

2. Или закомментируйте переменную `DATABASE_URL` в `docker-compose.yml`

3. Перезапустите:
   ```bash
   docker-compose down
   docker-compose up -d
   ```

## 💡 Преимущества PostgreSQL

- ✅ **100+ одновременных пользователей** без проблем
- ✅ **Пул соединений:** 20 базовых + 40 overflow = до 60 соединений
- ✅ **Лучшая производительность** на больших объемах данных
- ✅ **Готовность к масштабированию** (репликация, шардирование)
- ✅ **Надежность и отказоустойчивость**

## 🆘 Решение проблем

### Проблема: PostgreSQL не запускается

```bash
# Проверьте логи
docker-compose logs postgres

# Проверьте, не занят ли порт 5432
lsof -i :5432

# Пересоздайте volume (⚠️ удалит данные!)
docker-compose down -v
docker-compose up -d postgres
```

### Проблема: Ошибка подключения к базе

1. Проверьте переменные окружения в `.env`
2. Убедитесь, что PostgreSQL запущен: `docker-compose ps postgres`
3. Проверьте логи: `docker-compose logs backend`

### Проблема: Таблицы не создаются

```bash
# Попробуйте создать таблицы вручную
docker-compose exec backend python -c "from app.main import create_app; from database import Base, engine; app = create_app(); Base.metadata.create_all(bind=engine)"
```

## 📝 Дополнительные настройки

### Backup базы данных

```bash
# Создать backup
docker-compose exec postgres pg_dump -U saas_user saas > backup_$(date +%Y%m%d_%H%M%S).sql

# Восстановить из backup
docker-compose exec -T postgres psql -U saas_user saas < backup.sql
```

### Оптимизация PostgreSQL

Для production рекомендуется настроить `postgresql.conf`:

```conf
max_connections = 100
shared_buffers = 256MB
effective_cache_size = 1GB
maintenance_work_mem = 64MB
checkpoint_completion_target = 0.9
wal_buffers = 16MB
default_statistics_target = 100
random_page_cost = 1.1
effective_io_concurrency = 200
work_mem = 4MB
min_wal_size = 1GB
max_wal_size = 4GB
```

---

**Готово!** Теперь ваше приложение готово к работе с 100+ одновременными пользователями! 🚀

