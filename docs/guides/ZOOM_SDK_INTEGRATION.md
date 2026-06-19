# 🚀 Zoom Meeting SDK Integration с ИИ-агентом

## 📋 Обзор

Эта интеграция позволяет ИИ-агенту подключаться к Zoom встречам как полноценный участник, используя Zoom Meeting SDK. Агент может слышать участников, отвечать голосом и участвовать в разговоре в реальном времени.

## 🏗 Архитектура

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Frontend      │    │   Backend       │    │  AI Agent      │
│   (Browser)     │◄──►│   (FastAPI)     │◄──►│  Service        │
└─────────────────┘    └─────────────────┘    └─────────────────┘
                                │                        │
                                │                        │ WebSocket
                                ▼                        ▼
                       ┌─────────────────┐    ┌─────────────────┐
                       │  Zoom API       │    │  SDK Runner     │
                       │  (REST)         │    │  (Node.js)      │
                       └─────────────────┘    └─────────────────┘
                                                        │
                                                        ▼
                                               ┌─────────────────┐
                                               │  Zoom Meeting   │
                                               │  SDK (Browser)  │
                                               └─────────────────┘
```

## 🔧 Компоненты

### 1. **Signature Service** (`app/services/signature_service.py`)
- Генерирует JWT подписи для Zoom Meeting SDK
- Использует HS256 алгоритм с SDK Key/Secret
- Токены действительны 2 часа

### 2. **SDK Runner** (`sdk-runner/`)
- Node.js сервис с Puppeteer
- Запускает headless Chromium с Zoom Meeting SDK
- Подключается к встречам как участник
- Обрабатывает аудио потоки через WebAudio API

### 3. **WebSocket Bridge** (`ai_agent_service/services/websocket_client.py`)
- Связывает AI Agent Service с SDK Runner
- Передает аудио данные в реальном времени
- Поддерживает barge-in функциональность

### 4. **Audio Pipeline** (`ai_agent_service/pipeline/audio_pipeline.py`)
- Обрабатывает аудио: STT → LLM → TTS
- Поддерживает прерывание TTS при речи пользователя
- Ведет полный транскрипт встречи

## 🔄 Процесс работы

### **Создание встречи:**
1. Пользователь создает встречу через веб-интерфейс
2. Backend создает встречу через Zoom API
3. Сохраняет данные в БД с `agent_active: false`

### **Запуск агента:**
1. Пользователь нажимает "Запустить агента"
2. Backend генерирует JWT подпись для SDK
3. SDK Runner запускается и подключается к встрече
4. AI Agent Service подключается к SDK Runner через WebSocket
5. Агент появляется в Zoom как участник "ИИ-Агент"

### **Обработка аудио:**
```
Пользователь говорит
        ↓
Zoom Meeting → SDK Runner (WebAudio API)
        ↓
WebSocket → AI Agent Service
        ↓
STT (Whisper/Deepgram) → текст
        ↓
LLM (GPT-4o) → ответ
        ↓
TTS (ElevenLabs) → аудио
        ↓
WebSocket → SDK Runner
        ↓
Zoom Meeting → Пользователь слышит ответ
```

### **Barge-in (прерывание):**
1. Во время воспроизведения TTS пользователь начинает говорить
2. SDK Runner обнаруживает входящий аудио
3. Отправляет команду остановки TTS в AI Agent Service
4. TTS останавливается ≤ 300ms
5. Начинается обработка новой речи пользователя

## 📊 API Эндпоинты

### **Signature Generation:**
```http
POST /api/zoom/sdk-signature
Content-Type: application/json

{
  "meeting_number": "123456789",
  "role": 0,
  "user_identity": "ai_assistant"
}

Response:
{
  "signature": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
  "sdk_key": "your_sdk_key",
  "meeting_number": "123456789",
  "role": 0,
  "user_identity": "ai_assistant"
}
```

### **Agent Management:**
```http
# Запуск агента
POST /api/zoom/meetings/{id}/start-agent

# Остановка агента  
POST /api/zoom/meetings/{id}/stop-agent

# Статус агента
GET /api/zoom/meetings/{id}/agent-status
```

### **SDK Runner:**
```http
# Подключение к встрече
POST http://sdk-runner:3001/join
{
  "meetingNumber": "123456789",
  "signature": "jwt_signature",
  "userName": "ИИ-Агент",
  "sdkKey": "sdk_key"
}

# Отключение от встречи
POST http://sdk-runner:3001/leave
{
  "meetingNumber": "123456789"
}

# Статус сессии
GET http://sdk-runner:3001/status/{meetingNumber}
```

## 🛠 Настройка и запуск

### **1. Переменные окружения:**
```bash
# Zoom Meeting SDK
ZOOM_SDK_KEY=your_sdk_key
ZOOM_SDK_SECRET=your_sdk_secret

# Zoom API (уже настроено)
ZOOM_CLIENT_ID=your_client_id
ZOOM_CLIENT_SECRET=your_client_secret
ZOOM_ACCOUNT_ID=your_account_id
```

### **2. Получение SDK credentials:**
1. Идите в [Zoom Marketplace](https://marketplace.zoom.us/)
2. Создайте "Meeting SDK" приложение
3. Получите SDK Key и SDK Secret
4. Добавьте домены в whitelist

### **3. Запуск:**
```bash
# Пересоберем все сервисы
docker-compose down
docker-compose up -d --build

# Проверим статус
docker-compose ps
docker-compose logs sdk-runner
```

## 🔍 Мониторинг и отладка

### **Логи сервисов:**
```bash
# Backend логи
docker-compose logs backend -f

# AI Agent Service логи  
docker-compose logs ai_agent_service -f

# SDK Runner логи
docker-compose logs sdk-runner -f
```

### **Health checks:**
```bash
# Backend
curl http://localhost:8000/health

# AI Agent Service
curl http://localhost:8001/health

# SDK Runner (внутренняя сеть)
docker-compose exec backend curl http://sdk-runner:3001/health
```

## ⚠️ Известные ограничения

1. **HTTPS требование** - Zoom SDK требует HTTPS в продакшене
2. **CORS настройки** - нужно добавить домены в Zoom App
3. **Аудио права** - браузер может запросить разрешения на микрофон
4. **Производительность** - Puppeteer требует ресурсов

## 🔧 Troubleshooting

### **Агент не подключается:**
1. Проверьте SDK Key/Secret
2. Убедитесь, что домен в whitelist
3. Проверьте логи SDK Runner

### **Нет аудио:**
1. Проверьте WebSocket соединение
2. Убедитесь, что AI Agent Service получает аудио
3. Проверьте настройки WebAudio API

### **TTS не работает:**
1. Проверьте ElevenLabs API ключ
2. Убедитесь, что аудио отправляется в SDK Runner
3. Проверьте настройки микрофона в Zoom

## 🚀 Что дальше

1. **Улучшить barge-in** - более точное определение речи
2. **Добавить видео аватар** - интеграция с Ready Player Me
3. **Оптимизировать латентность** - streaming STT/TTS
4. **Добавить аналитику** - метрики производительности

---

**Теперь ваш ИИ-агент может реально участвовать в Zoom встречах! 🎉**
