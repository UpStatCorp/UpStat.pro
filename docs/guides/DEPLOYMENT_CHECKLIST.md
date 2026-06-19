# ✅ Чеклист развертывания улучшений

## 📋 Перед развертыванием

### 1. Подготовка
- [ ] Сделан backup базы данных
- [ ] Сделан backup директории uploads
- [ ] Проверена версия Python (3.11+)
- [ ] Проверена версия Docker (если используется)

### 2. Обновление кода
- [ ] Получен последний код из репозитория
- [ ] Проверено наличие всех новых файлов:
  - [ ] `app/services/notification_service.py`
  - [ ] `app/services/progress_tracker.py`
  - [ ] `app/services/caching_service.py`
  - [ ] `app/services/db_optimizer.py`
  - [ ] `app/services/file_optimizer.py`
  - [ ] `app/services/error_handler.py`
  - [ ] `app/services/pipeline_enhanced.py`
  - [ ] `app/utils/file_validator.py`
  - [ ] `app/routers/notifications.py`
  - [ ] `app/routers/progress.py`
  - [ ] `app/routers/performance.py`
  - [ ] `app/middleware/rate_limit.py`
  - [ ] `app/static/js/notifications.js`
  - [ ] `app/static/js/progress-tracker.js`
  - [ ] `app/static/css/notifications.css`
  - [ ] `app/static/css/progress-tracker.css`
  - [ ] `app/templates/admin_performance.html`

### 3. Зависимости
- [ ] Обновлен `requirements.txt`
- [ ] Проверено наличие:
  - [ ] `python-magic>=0.4.27`
  - [ ] `psutil>=5.9.0`

### 4. Docker (если используется)
- [ ] Обновлен `Dockerfile` (добавлены libmagic1 и procps)
- [ ] Проверен `docker-compose.yml`

---

## 🚀 Развертывание

### Вариант 1: Без Docker

```bash
# 1. Обновить зависимости
pip install -r requirements.txt

# 2. Проверить миграции БД (если есть)
# (в данном случае миграции не требуются)

# 3. Перезапустить сервис
sudo systemctl restart upstat
# или
pm2 restart upstat

# 4. Проверить логи
tail -f /var/log/upstat.log
# или
pm2 logs upstat
```

### Вариант 2: С Docker

```bash
# 1. Остановить контейнеры
docker-compose down

# 2. Пересобрать образы
docker-compose build --no-cache

# 3. Запустить контейнеры
docker-compose up -d

# 4. Проверить логи
docker-compose logs -f backend

# 5. Проверить статус
docker-compose ps
```

---

## ✔️ Проверка работоспособности

### 1. Базовые проверки
- [ ] Приложение запустилось без ошибок
- [ ] Главная страница открывается
- [ ] Можно войти в систему

### 2. Новые функции

#### Уведомления
- [ ] Видны уведомления в правом верхнем углу (если есть)
- [ ] API `/api/notifications/unread` отвечает (200 OK)
- [ ] Уведомления появляются с анимацией
- [ ] Темная тема работает корректно

#### Прогресс-трекер
- [ ] При загрузке файла видно прогресс-бар
- [ ] API `/api/progress/active/list` отвечает (200 OK)
- [ ] Прогресс обновляется в реальном времени
- [ ] После завершения приходит уведомление

#### Валидация файлов
- [ ] Загрузка правильного файла работает
- [ ] Загрузка файла с неправильным расширением блокируется
- [ ] Загрузка слишком большого файла блокируется
- [ ] Сообщения об ошибках понятны

#### Производительность (только для админов)
- [ ] Страница `/admin/performance` открывается
- [ ] Видна статистика кеша
- [ ] Видна статистика БД
- [ ] Видна статистика хранилища
- [ ] Видна системная информация (CPU, память, диск)
- [ ] Кнопки управления работают:
  - [ ] Очистить кеш
  - [ ] Оптимизировать БД
  - [ ] Очистить старые файлы

#### Безопасность
- [ ] Rate limiting работает (слишком много запросов → 429)
- [ ] Слабый пароль не принимается при регистрации
- [ ] CSRF защита работает
- [ ] Security headers присутствуют в ответах

### 3. Интеграция

#### Pipeline
- [ ] Загрузка аудио файла → транскрибация → анализ → отчет
- [ ] Загрузка текстового файла → анализ → отчет
- [ ] При ошибках показываются понятные сообщения
- [ ] Прогресс отслеживается корректно

#### Чат
- [ ] Отправка сообщений работает
- [ ] Загрузка файлов работает
- [ ] Валидация файлов активна
- [ ] История сообщений загружается

---

## 🔍 Тестирование производительности

### Кеширование
```bash
# 1. Запросить данные первый раз (медленно)
curl http://localhost:8000/api/some-endpoint

# 2. Запросить те же данные второй раз (быстро)
curl http://localhost:8000/api/some-endpoint

# Второй запрос должен быть значительно быстрее
```

### БД оптимизация
```bash
# Проверить размер БД до оптимизации
curl http://localhost:8000/api/performance/database/stats

# Оптимизировать
curl -X POST http://localhost:8000/api/performance/database/optimize

# Проверить размер после
curl http://localhost:8000/api/performance/database/stats
```

### Очистка файлов
```bash
# Проверить размер хранилища
curl http://localhost:8000/api/performance/storage/stats

# Очистить старые файлы (>7 дней)
curl -X POST "http://localhost:8000/api/performance/storage/cleanup?days_to_keep=7"

# Проверить размер после
curl http://localhost:8000/api/performance/storage/stats
```

---

## 🐛 Troubleshooting

### Проблема: Уведомления не показываются
**Решение:**
1. Проверить консоль браузера (F12)
2. Убедиться что загружены скрипты:
   - `/static/js/notifications.js`
   - `/static/css/notifications.css`
3. Очистить кеш браузера

### Проблема: Прогресс не обновляется
**Решение:**
1. Проверить API: `curl http://localhost:8000/api/progress/active/list`
2. Проверить логи backend
3. Убедиться что `/static/js/progress-tracker.js` загружен

### Проблема: Ошибка импорта magic
**Решение:**
```bash
# Установить системные зависимости
apt-get install libmagic1  # Debian/Ubuntu
yum install file-devel      # RedHat/CentOS

# Переустановить python-magic
pip uninstall python-magic
pip install python-magic
```

### Проблема: psutil не установлен
**Решение:**
```bash
pip install psutil
```

### Проблема: Недостаточно прав для admin панели
**Решение:**
1. Войти как пользователь с ролью "admin"
2. Если нужно - обновить роль в БД:
```sql
UPDATE users SET role='admin' WHERE email='admin@example.com';
```

---

## 📊 Мониторинг после развертывания

### День 1
- [ ] Проверить логи на ошибки
- [ ] Проверить использование памяти
- [ ] Проверить размер БД
- [ ] Проверить работу кеша

### Неделя 1
- [ ] Проверить статистику производительности
- [ ] Очистить старые файлы
- [ ] Оптимизировать БД
- [ ] Проанализировать медленные запросы

### Месяц 1
- [ ] Проверить тренды производительности
- [ ] Настроить автоматическую очистку
- [ ] Оценить эффективность кеширования
- [ ] Собрать feedback от пользователей

---

## 📝 Rollback план (если что-то пошло не так)

### С Docker
```bash
# 1. Вернуться на предыдущую версию
docker-compose down
git checkout <previous-commit>
docker-compose build
docker-compose up -d

# 2. Восстановить backup БД (если нужно)
cp backup.db app.db
```

### Без Docker
```bash
# 1. Вернуться на предыдущую версию
git checkout <previous-commit>
pip install -r requirements.txt

# 2. Перезапустить
sudo systemctl restart upstat

# 3. Восстановить backup БД (если нужно)
cp backup.db app.db
```

---

## ✅ Финальная проверка

- [ ] Все пункты чеклиста пройдены
- [ ] Нет критических ошибок в логах
- [ ] Производительность не ухудшилась
- [ ] Новые функции работают
- [ ] Пользователи могут нормально работать
- [ ] Backup на месте (на всякий случай)

**Статус:** ________________  
**Дата:** ________________  
**Кто проверял:** ________________

---

**Поздравляем! Развертывание завершено! 🎉**

