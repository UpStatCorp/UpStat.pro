FROM python:3.11-slim

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    gcc \
    ffmpeg \
    portaudio19-dev \
    python3-dev \
    libmagic1 \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Копирование requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование кода приложения
COPY app/ ./app/
COPY main.py .
COPY alembic/ ./alembic/
COPY alembic.ini .
COPY voice_assistant/ ./voice_assistant/
COPY checklists/ ./checklists/
COPY checklists_trener/ ./checklists_trener/

# Создание пользователя для безопасности
RUN useradd --create-home --shell /bin/bash app && chown -R app:app /app
USER app

# Установка переменной окружения для Python
ENV PYTHONPATH=/app:/app/voice_assistant

# Запуск приложения
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--ws-ping-interval", "30", "--ws-ping-timeout", "120"]
