# 🚀 Руководство по развёртыванию масштабируемой голосовой тренировки

## 📋 Содержание

1. [Предварительные требования](#предварительные-требования)
2. [Локальная установка](#локальная-установка)
3. [Docker установка](#docker-установка)
4. [Production установка](#production-установка)
5. [Тестирование](#тестирование)
6. [Мониторинг](#мониторинг)
7. [Решение проблем](#решение-проблем)

## 🔧 Предварительные требования

### Минимальные требования
- Python 3.9+
- SQLite (для разработки) или PostgreSQL (для production)
- 4 GB RAM
- 2 CPU ядра

### Рекомендуемые требования (для 100 пользователей)
- Python 3.10+
- PostgreSQL 14+
- 8-16 GB RAM
- 4-8 CPU ядер
- SSD диск

### Зависимости Python
```bash
pip install -r requirements.txt
```

Основные пакеты:
- fastapi
- uvicorn
- sqlalchemy
- alembic
- openai (для STT/GPT/TTS)
- numpy
- aiohttp

## 📦 Локальная установка

### Шаг 1: Клонирование репозитория
```bash
git clone <repository>
cd <project>
```

### Шаг 2: Установка зависимостей
```bash
pip install -r requirements.txt
```

### Шаг 3: Настройка переменных окружения
```bash
cp env.example .env
```

Отредактируйте `.env`:
```bash
# OpenAI API
OPENAI_API_KEY=sk-...

# База данных (для development используйте SQLite)
DATABASE_URL=sqlite:///./app/app.db

# JWT
SECRET_KEY=ваш-секретный-ключ
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

# Session Manager
MAX_CONCURRENT_SESSIONS=100
MAX_STT_WORKERS=10
```

### Шаг 4: Применение миграций
```bash
cd app
alembic upgrade head
cd ..
```

### Шаг 5: Создание админа (опционально)
```bash
python app/create_admin.py
```

### Шаг 6: Запуск сервера
```bash
python app/main.py
```

Или через uvicorn:
```bash
cd app
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Шаг 7: Проверка установки
```bash
# В новом терминале
python test_scalable_training.py
```

Должны пройти все тесты:
```
✅ PASS | Доступность сервера
✅ PASS | База данных
✅ PASS | Модули
✅ PASS | Одновременные сессии
✅ PASS | Производительность
```

## 🐳 Docker установка

### Шаг 1: Создание образа
```bash
docker-compose build
```

### Шаг 2: Запуск контейнеров
```bash
docker-compose up -d
```

### Шаг 3: Применение миграций
```bash
docker-compose exec app alembic upgrade head
```

### Шаг 4: Проверка
```bash
curl http://localhost:8000/voice-training/stats
```

### Просмотр логов
```bash
docker-compose logs -f app
```

## 🌐 Production установка

### Вариант 1: Systemd Service (Ubuntu/Debian)

#### 1. Создайте systemd service
```bash
sudo nano /etc/systemd/system/voice-training.service
```

Содержимое:
```ini
[Unit]
Description=Voice Training Service
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/var/www/voice-training
Environment="PATH=/var/www/voice-training/venv/bin"
ExecStart=/var/www/voice-training/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
Restart=always

[Install]
WantedBy=multi-user.target
```

#### 2. Запустите сервис
```bash
sudo systemctl daemon-reload
sudo systemctl enable voice-training
sudo systemctl start voice-training
sudo systemctl status voice-training
```

### Вариант 2: Nginx + Gunicorn/Uvicorn

#### 1. Установите Nginx
```bash
sudo apt install nginx
```

#### 2. Настройте Nginx
```bash
sudo nano /etc/nginx/sites-available/voice-training
```

Содержимое:
```nginx
upstream voice_training_app {
    server 127.0.0.1:8000;
}

server {
    listen 80;
    server_name your-domain.com;

    # Редирект на HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com;

    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    location / {
        proxy_pass http://voice_training_app;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # WebSocket для голосовой тренировки
    location /voice-training/ws {
        proxy_pass http://voice_training_app;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    # Статика
    location /static {
        alias /var/www/voice-training/app/static;
        expires 30d;
    }
}
```

#### 3. Активируйте конфигурацию
```bash
sudo ln -s /etc/nginx/sites-available/voice-training /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

#### 4. SSL сертификат (Let's Encrypt)
```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

### Вариант 3: Kubernetes (для масштабирования)

#### deployment.yaml
```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: voice-training
spec:
  replicas: 3
  selector:
    matchLabels:
      app: voice-training
  template:
    metadata:
      labels:
        app: voice-training
    spec:
      containers:
      - name: voice-training
        image: your-registry/voice-training:latest
        ports:
        - containerPort: 8000
        env:
        - name: DATABASE_URL
          valueFrom:
            secretKeyRef:
              name: db-secret
              key: url
        - name: OPENAI_API_KEY
          valueFrom:
            secretKeyRef:
              name: openai-secret
              key: api-key
        resources:
          requests:
            memory: "2Gi"
            cpu: "1000m"
          limits:
            memory: "4Gi"
            cpu: "2000m"
---
apiVersion: v1
kind: Service
metadata:
  name: voice-training
spec:
  type: LoadBalancer
  ports:
  - port: 80
    targetPort: 8000
  selector:
    app: voice-training
```

## ✅ Тестирование

### Автоматическое тестирование
```bash
python test_scalable_training.py
```

### Ручное тестирование

#### 1. Проверка API
```bash
# Статистика
curl http://localhost:8000/voice-training/stats

# Health check
curl http://localhost:8000/health
```

#### 2. Проверка WebSocket
```javascript
// В консоли браузера
const token = localStorage.getItem('access_token');
const ws = new WebSocket(`ws://localhost:8000/voice-training/ws?token=${token}&training_id=1`);

ws.onopen = () => console.log('✅ Подключено');
ws.onmessage = (e) => console.log('Сообщение:', JSON.parse(e.data));
ws.onerror = (e) => console.error('❌ Ошибка:', e);
```

#### 3. Нагрузочное тестирование
```bash
# Установите k6
brew install k6  # MacOS
# или
sudo apt install k6  # Ubuntu

# Создайте load_test.js
```

```javascript
import ws from 'k6/ws';
import { check } from 'k6';

export let options = {
  stages: [
    { duration: '1m', target: 50 },   // Поднимаем до 50 пользователей
    { duration: '3m', target: 50 },   // Держим 50 пользователей
    { duration: '1m', target: 100 },  // Поднимаем до 100
    { duration: '3m', target: 100 },  // Держим 100
    { duration: '1m', target: 0 },    // Спускаем до 0
  ],
};

export default function () {
  const token = 'YOUR_TEST_TOKEN';
  const url = `ws://localhost:8000/voice-training/ws?token=${token}&training_id=1`;
  
  ws.connect(url, function (socket) {
    socket.on('open', () => {
      console.log('Connected');
    });
    
    socket.on('message', (data) => {
      console.log('Message received:', data);
    });
    
    socket.on('close', () => {
      console.log('Disconnected');
    });
  });
}
```

Запуск:
```bash
k6 run load_test.js
```

## 📊 Мониторинг

### Prometheus + Grafana

#### 1. Добавьте метрики в приложение
```python
# app/main.py
from prometheus_fastapi_instrumentator import Instrumentator

@app.on_event("startup")
async def startup():
    Instrumentator().instrument(app).expose(app)
```

#### 2. Настройте Prometheus
```yaml
# prometheus.yml
scrape_configs:
  - job_name: 'voice-training'
    static_configs:
      - targets: ['localhost:8000']
```

#### 3. Добавьте дашборд в Grafana
- URL: http://localhost:3000
- Import ID: 17375 (FastAPI dashboard)

### Логирование

#### Настройка logrotate
```bash
sudo nano /etc/logrotate.d/voice-training
```

Содержимое:
```
/var/www/voice-training/app/server.log {
    daily
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 www-data www-data
    sharedscripts
    postrotate
        systemctl reload voice-training
    endscript
}
```

### Алерты

#### Пример для email алертов
```python
# app/monitoring.py
import smtplib
from email.mime.text import MIMEText

async def send_alert(message):
    if session_manager.get_stats()['capacity_percent'] > 90:
        msg = MIMEText(f"⚠️ Загрузка сервера: {capacity}%")
        msg['Subject'] = 'Voice Training Alert'
        msg['From'] = 'alerts@example.com'
        msg['To'] = 'admin@example.com'
        
        with smtplib.SMTP('localhost') as server:
            server.send_message(msg)
```

## 🔧 Оптимизация производительности

### 1. Увеличение воркеров
```python
# voice_assistant/session_manager.py
SessionManager(
    max_concurrent_sessions=200,
    max_workers=20
)
```

### 2. Использование PostgreSQL
```bash
# .env
DATABASE_URL=postgresql://user:password@localhost/voice_training
```

### 3. Redis для кэширования
```python
import redis

cache = redis.Redis(host='localhost', port=6379, db=0)

# Кэширование частых запросов
@lru_cache(maxsize=1000)
def get_training_prompt(training_id):
    # ...
```

### 4. Горизонтальное масштабирование
```bash
# Запуск нескольких экземпляров
uvicorn app.main:app --port 8001 &
uvicorn app.main:app --port 8002 &
uvicorn app.main:app --port 8003 &

# Настройка nginx для балансировки
upstream voice_training {
    server 127.0.0.1:8001;
    server 127.0.0.1:8002;
    server 127.0.0.1:8003;
}
```

## 🐛 Решение проблем

### Проблема: "Module not found: voice_assistant"

**Решение:**
```bash
# Проверьте что все файлы на месте
ls -la voice_assistant/
# Должны быть: session_manager.py, db_service.py, websocket_handler.py, router_new.py
```

### Проблема: "No such table: voice_training_messages"

**Решение:**
```bash
cd app
alembic upgrade head
```

### Проблема: Высокое использование памяти

**Решение:**
1. Уменьшите `max_concurrent_sessions`
2. Включите автоочистку:
```python
# Добавьте в app/main.py
@app.on_event("startup")
async def cleanup_task():
    async def cleanup():
        while True:
            await asyncio.sleep(3600)  # Каждый час
            await session_manager.cleanup_inactive_sessions()
    asyncio.create_task(cleanup())
```

### Проблема: WebSocket отключается через 60 секунд

**Решение:**
```nginx
# В nginx.conf
proxy_read_timeout 3600s;
proxy_send_timeout 3600s;
```

## 📚 Дополнительные ресурсы

- **Полная документация:** `VOICE_TRAINING_SCALABLE.md`
- **Быстрый старт:** `QUICK_START_SCALABLE_TRAINING.md`
- **Резюме изменений:** `SCALABLE_TRAINING_SUMMARY.md`
- **README:** `README_SCALABLE_VOICE_TRAINING.md`

## 📞 Поддержка

При возникновении проблем:
1. Проверьте логи: `tail -f app/server.log`
2. Запустите тесты: `python test_scalable_training.py`
3. Проверьте статистику: `curl http://localhost:8000/voice-training/stats`

---

**Версия:** 1.0  
**Дата:** 12 января 2025  
**Статус:** ✅ Production Ready

