#!/bin/bash

# Скрипт для настройки Google OAuth на продакшене up-stat.com

echo "🚀 Настройка Google OAuth для продакшена up-stat.com"
echo "=================================================="

# Проверяем наличие .env файла
if [ ! -f ".env" ]; then
    echo "❌ Файл .env не найден!"
    echo "📝 Создайте файл .env на основе env.example"
    echo "   cp env.example .env"
    echo "   Затем отредактируйте .env файл с вашими настройками"
    exit 1
fi

echo "✅ Файл .env найден"

# Проверяем наличие необходимых переменных
echo "🔍 Проверяем переменные окружения..."

if ! grep -q "GOOGLE_CLIENT_ID" .env; then
    echo "❌ GOOGLE_CLIENT_ID не найден в .env файле"
    exit 1
fi

if ! grep -q "GOOGLE_CLIENT_SECRET" .env; then
    echo "❌ GOOGLE_CLIENT_SECRET не найден в .env файле"
    exit 1
fi

if ! grep -q "ENVIRONMENT=production" .env; then
    echo "⚠️  ENVIRONMENT не установлен в production"
    echo "📝 Добавьте в .env файл: ENVIRONMENT=production"
fi

echo "✅ Переменные окружения проверены"

# Останавливаем текущие контейнеры
echo "🛑 Останавливаем текущие контейнеры..."
docker-compose down

# Пересобираем и запускаем
echo "🔨 Пересобираем и запускаем контейнеры..."
docker-compose up --build -d

# Ждем запуска
echo "⏳ Ждем запуска сервисов..."
sleep 10

# Проверяем статус
echo "📊 Проверяем статус сервисов..."
docker-compose ps

# Проверяем логи
echo "📋 Последние логи backend:"
docker-compose logs --tail=20 backend

echo ""
echo "🎉 Настройка завершена!"
echo ""
echo "📋 Следующие шаги:"
echo "1. Убедитесь, что в Google Cloud Console добавлен redirect URI:"
echo "   https://up-stat.com/auth/google/callback"
echo ""
echo "2. Проверьте работу OAuth:"
echo "   https://up-stat.com/login"
echo ""
echo "3. Для мониторинга логов используйте:"
echo "   docker-compose logs -f backend"
echo ""
echo "4. Для проверки переменных окружения:"
echo "   docker-compose exec backend env | grep GOOGLE"
echo ""
echo "✅ Готово!"










