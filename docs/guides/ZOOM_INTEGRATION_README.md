# 🚀 Интеграция Zoom с ИИ-агентом

Этот проект добавляет возможность создания Zoom встреч с ИИ-агентом, который может слушать участников, генерировать ответы и озвучивать их в реальном времени.

## 🏗️ Архитектура системы

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Web Frontend  │    │  FastAPI Backend │    │ AI Agent       │
│                 │    │                  │    │ Service        │
│ - Zoom Dashboard│◄──►│ - Zoom API       │◄──►│ - STT Pipeline │
│ - Meeting List  │    │ - Meeting Mgmt   │    │ - LLM Service  │
│ - Reports       │    │ - User Auth      │    │ - TTS Pipeline │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                                │                        │
                                ▼                        ▼
                       ┌──────────────────┐    ┌─────────────────┐
                       │   Database       │    │   Zoom Meeting  │
                       │                  │    │                 │
                       │ - Users         │    │ - Audio Stream  │
                       │ - Meetings      │    │ - Video Stream  │
                       │ - Transcripts   │    │ - Bot Presence  │
                       │ - Reports       │    └─────────────────┘
                       └──────────────────┘
```

## 📋 Что реализовано

### ✅ Backend API
- [x] Модели данных для Zoom встреч и транскриптов
- [x] Роутер для управления Zoom встречами
- [x] Сервис интеграции с Zoom API
- [x] Pydantic схемы для валидации данных

### ✅ AI Agent Service
- [x] Микросервис на FastAPI
- [x] STT сервис (Whisper + Deepgram)
- [x] LLM сервис (GPT-4o)
- [x] TTS сервис (ElevenLabs + XTTS)
- [x] Асинхронный аудио пайплайн
- [x] Zoom клиент для подключения к встречам

### ✅ Frontend
- [x] UI компоненты для управления встречами
- [x] Модальные окна создания встреч
- [x] Список встреч с действиями
- [x] Детальный просмотр встреч и транскриптов
- [x] JavaScript для взаимодействия с API

### ✅ Инфраструктура
- [x] Docker Compose для оркестрации
- [x] PostgreSQL база данных
- [x] Redis для кэширования
- [x] Nginx для проксирования

## 🚀 Быстрый старт

### 1. Клонирование и настройка

```bash
# Клонируйте репозиторий
git clone <your-repo>
cd saas_ocenka-main

# Скопируйте файл с переменными окружения
cp env.example .env

# Отредактируйте .env файл, добавив ваши API ключи
nano .env
```

### 2. Настройка переменных окружения

Обязательно заполните следующие переменные в `.env` файле:

```bash
# Zoom API - Server-to-Server OAuth (получите на https://marketplace.zoom.us/)
ZOOM_CLIENT_ID=your_zoom_client_id
ZOOM_CLIENT_SECRET=your_zoom_client_secret
ZOOM_ACCOUNT_ID=your_zoom_account_id
```

**Важно:** Используйте только Server-to-Server OAuth приложение. JWT App устарел и не поддерживается.

#### Настройка Zoom OAuth приложения:

1. Перейдите на [Zoom App Marketplace](https://marketplace.zoom.us/)
2. Нажмите **Develop** → **Build App**
3. Выберите **Server-to-Server OAuth**
4. После создания приложения в разделе **App Credentials** получите:
   - **Client ID** → `ZOOM_CLIENT_ID`
   - **Client Secret** → `ZOOM_CLIENT_SECRET`
   - **Account ID** → `ZOOM_ACCOUNT_ID`

# OpenAI API (получите на https://platform.openai.com/)
OPENAI_API_KEY=your_openai_api_key

# ElevenLabs API (получите на https://elevenlabs.io/)
ELEVENLABS_API_KEY=your_elevenlabs_api_key

# Опционально: Deepgram API (альтернатива Whisper)
DEEPGRAM_API_KEY=your_deepgram_api_key
```

### 3. Запуск через Docker Compose

```bash
# Запуск всех сервисов
docker-compose up -d

# Проверка статуса
docker-compose ps

# Просмотр логов
docker-compose logs -f
```

### 4. Проверка работоспособности

```bash
# Основной бэкенд
curl http://localhost:8000/health

# AI Agent Service
curl http://localhost:8001/health

# База данных
docker-compose exec db pg_isready -U user -d saas
```

## 🔧 Ручной запуск (без Docker)

### Запуск основного бэкенда

```bash
cd app
pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Запуск AI Agent Service

```bash
cd ai_agent_service
pip install -r requirements.txt
python -m uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

### Запуск базы данных

```bash
# PostgreSQL
sudo systemctl start postgresql
sudo -u postgres createdb saas
sudo -u postgres psql -c "CREATE USER user WITH PASSWORD 'pass';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE saas TO user;"
```

## 📱 Использование

### 1. Создание Zoom встречи

1. Откройте дашборд в браузере: `http://localhost:8000`
2. Войдите в систему
3. Нажмите "Создать встречу" в секции Zoom встреч
4. Заполните форму:
   - Тема встречи
   - Время начала
   - Длительность
   - Включить ИИ-агента
5. Нажмите "Создать встречу"

### 2. Запуск встречи с ИИ-агентом

1. В списке встреч найдите созданную встречу
2. Нажмите "Запустить" для активации ИИ-агента
3. Присоединитесь к встрече по ссылке
4. ИИ-агент автоматически подключится и начнет слушать

### 3. Завершение встречи и получение отчета

1. Нажмите "Завершить" в списке встреч
2. Система автоматически:
   - Остановит ИИ-агента
   - Сгенерирует транскрипт
   - Создаст краткое резюме
3. Просмотрите отчет, нажав "Детали"

## 🔌 API Endpoints

### Zoom Meetings API

```bash
# Создание встречи
POST /api/zoom/meetings/create

# Список встреч пользователя
GET /api/zoom/meetings

# Детали встречи
GET /api/zoom/meetings/{meeting_id}

# Запуск встречи с ИИ-агентом
POST /api/zoom/meetings/{meeting_id}/start

# Завершение встречи
POST /api/zoom/meetings/{meeting_id}/end

# Удаление встречи
DELETE /api/zoom/meetings/{meeting_id}

# Получение транскрипта
GET /api/zoom/meetings/{meeting_id}/transcript
```

### AI Agent Service API

```bash
# Проверка здоровья
GET /health

# Запуск встречи
POST /meetings/start

# Завершение встречи
POST /meetings/end

# Обработка аудио
POST /audio/chunk

# Активные встречи
GET /meetings/active
```

## 🛠️ Разработка

### Структура проекта

```
saas_ocenka-main/
├── app/                          # Основной FastAPI бэкенд
│   ├── models.py                 # Модели данных
│   ├── schemas.py                # Pydantic схемы
│   ├── routers/
│   │   └── zoom_meetings.py      # Zoom API роутер
│   └── services/
│       └── zoom_service.py       # Zoom интеграция
├── ai_agent_service/             # Микросервис ИИ-агента
│   ├── main.py                   # Точка входа
│   ├── config.py                 # Конфигурация
│   ├── services/
│   │   ├── stt_service.py        # Speech-to-Text
│   │   ├── llm_service.py        # GPT-4o интеграция
│   │   ├── tts_service.py        # Text-to-Speech
│   │   └── zoom_client.py        # Zoom клиент
│   └── pipeline/
│       └── audio_pipeline.py     # Аудио пайплайн
├── docker-compose.yml            # Оркестрация сервисов
└── env.example                   # Переменные окружения
```

### Добавление новых функций

#### Новый STT провайдер

1. Создайте новый сервис в `ai_agent_service/services/`
2. Реализуйте интерфейс `STTService`
3. Добавьте конфигурацию в `config.py`
4. Обновите `STTService.__init__()` для выбора провайдера

#### Новый TTS провайдер

1. Создайте новый сервис в `ai_agent_service/services/`
2. Реализуйте интерфейс `TTSService`
3. Добавьте конфигурацию в `config.py`
4. Обновите `TTSService.__init__()` для выбора провайдера

#### Новый LLM провайдер

1. Создайте новый сервис в `ai_agent_service/services/`
2. Реализуйте интерфейс `LLMService`
3. Добавьте конфигурацию в `config.py`
4. Обновите `LLMService.__init__()` для выбора провайдера

## 🧪 Тестирование

### Запуск тестов

```bash
# Тесты основного бэкенда
cd app
pytest

# Тесты AI Agent Service
cd ai_agent_service
pytest

# Тесты с покрытием
pytest --cov=. --cov-report=html
```

### Тестовые данные

```bash
# Создание тестовой встречи
curl -X POST http://localhost:8000/api/zoom/meetings/create \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "topic": "Тестовая встреча",
    "start_time": "2024-01-15T10:00:00",
    "duration_minutes": 60,
    "ai_agent_enabled": true
  }'
```

## 🚨 Устранение неполадок

### Частые проблемы

#### 1. Ошибка подключения к Zoom API

```bash
# Проверьте переменные окружения
echo $ZOOM_CLIENT_ID
echo $ZOOM_CLIENT_SECRET

# Проверьте логи
docker-compose logs ai_agent_service
```

#### 2. Ошибка OpenAI API

```bash
# Проверьте API ключ
echo $OPENAI_API_KEY

# Проверьте лимиты на https://platform.openai.com/usage
```

#### 3. Ошибка ElevenLabs API

```bash
# Проверьте API ключ
echo $ELEVENLABS_API_KEY

# Проверьте лимиты на https://elevenlabs.io/
```

#### 4. Проблемы с базой данных

```bash
# Проверьте подключение
docker-compose exec db psql -U user -d saas -c "SELECT 1;"

# Пересоздайте базу
docker-compose down -v
docker-compose up -d db
```

### Логи и отладка

```bash
# Логи всех сервисов
docker-compose logs -f

# Логи конкретного сервиса
docker-compose logs -f backend
docker-compose logs -f ai_agent_service

# Логи с временными метками
docker-compose logs -f --timestamps
```

## 📊 Мониторинг

### Метрики здоровья

- **Backend**: `http://localhost:8000/health`
- **AI Agent**: `http://localhost:8001/health`
- **Database**: `docker-compose exec db pg_isready`

### Статистика использования

```bash
# Активные встречи
curl http://localhost:8001/meetings/active

# Статистика пайплайна
curl http://localhost:8001/stats
```

## 🔒 Безопасность

### Рекомендации

1. **API ключи**: Никогда не коммитьте `.env` файл в Git
2. **HTTPS**: В продакшене используйте SSL/TLS
3. **Аутентификация**: Все API endpoints защищены
4. **Валидация**: Все входные данные валидируются через Pydantic
5. **Логирование**: Ведется детальное логирование всех операций

### Переменные безопасности

```bash
# Обязательно измените в продакшене
SECRET_KEY=your_very_long_random_secret_key
DEBUG=false
ENVIRONMENT=production
```

## 🚀 Развертывание в продакшене

### 1. Подготовка сервера

```bash
# Обновление системы
sudo apt update && sudo apt upgrade -y

# Установка Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Установка Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/download/v2.20.0/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
```

### 2. Настройка продакшена

```bash
# Создание продакшен .env
cp env.example .env.prod

# Редактирование настроек
nano .env.prod

# Запуск в продакшене
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

### 3. SSL сертификат

```bash
# Установка Certbot
sudo apt install certbot python3-certbot-nginx

# Получение сертификата
sudo certbot --nginx -d yourdomain.com

# Автообновление
sudo crontab -e
# Добавьте: 0 12 * * * /usr/bin/certbot renew --quiet
```

## 📚 Дополнительные ресурсы

### Документация API

- [Zoom API](https://marketplace.zoom.us/docs/api-reference/zoom-api)
- [OpenAI API](https://platform.openai.com/docs/api-reference)
- [ElevenLabs API](https://elevenlabs.io/docs/api-reference)
- [Deepgram API](https://developers.deepgram.com/docs)

### Полезные ссылки

- [FastAPI документация](https://fastapi.tiangolo.com/)
- [SQLAlchemy документация](https://docs.sqlalchemy.org/)
- [Docker документация](https://docs.docker.com/)
- [PostgreSQL документация](https://www.postgresql.org/docs/)

## 🤝 Поддержка

Если у вас возникли вопросы или проблемы:

1. Проверьте раздел "Устранение неполадок"
2. Просмотрите логи сервисов
3. Создайте issue в репозитории
4. Обратитесь к документации API провайдеров

## 📄 Лицензия

Этот проект распространяется под лицензией MIT. См. файл `LICENSE` для подробностей.

---

**Удачной разработки! 🚀**
