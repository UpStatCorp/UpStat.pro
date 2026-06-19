# Быстрый старт Google OAuth

## 🚀 Что уже готово

✅ **Google OAuth интеграция полностью реализована:**
- Кнопки "Войти через Google" на страницах входа и регистрации
- Сервис для работы с Google OAuth API
- Обновленная модель пользователя с поддержкой OAuth
- Маршруты для авторизации и callback
- Миграция базы данных
- Docker конфигурация

## 📋 Что нужно сделать

### 1. Создать проект в Google Cloud Console

1. Перейдите в [Google Cloud Console](https://console.cloud.google.com/)
2. Создайте новый проект
3. Включите Google+ API в разделе "APIs & Services" > "Library"

### 2. Настроить OAuth 2.0

1. Перейдите в "APIs & Services" > "Credentials"
2. Нажмите "Create Credentials" > "OAuth 2.0 Client IDs"
3. Выберите "Web application"
4. Настройте:
   - **Name**: SaaS Оценка
   - **Authorized JavaScript origins**: 
     - `http://localhost:8000` (для разработки)
     - `https://up-stat.com` (для продакшена)
   - **Authorized redirect URIs**: 
     - `http://localhost:8000/auth/google/callback` (для разработки)
     - `https://up-stat.com/auth/google/callback` (для продакшена)

### 3. Добавить переменные окружения

Создайте файл `.env` в корне проекта и добавьте:

```env
# Google OAuth настройки
GOOGLE_CLIENT_ID=ваш_client_id_здесь
GOOGLE_CLIENT_SECRET=ваш_client_secret_здесь
GOOGLE_REDIRECT_URI=https://up-stat.com/auth/google/callback

# Окружение (development/production)
ENVIRONMENT=production
```

### 4. Запустить приложение

```bash
docker-compose up --build backend
```

### 5. Протестировать

1. Откройте `http://localhost:8000/login`
2. Нажмите "Войти через Google"
3. Авторизуйтесь через Google
4. Вы будете перенаправлены в кабинет

## 🔧 Технические детали

### Новые поля в базе данных:
- `google_id` - уникальный ID пользователя в Google
- `is_oauth_user` - флаг OAuth пользователя
- `password_hash` - теперь nullable для OAuth пользователей

### Новые маршруты:
- `GET /auth/google` - инициация OAuth авторизации
- `GET /auth/google/callback` - обработка callback от Google

### Безопасность:
- CSRF защита через state параметр
- Валидация токенов
- Связывание аккаунтов по email

## 🎯 Возможности

- **Автоматическая регистрация** новых пользователей через Google
- **Связывание аккаунтов** - если пользователь уже существует с таким email
- **Обновление профиля** - синхронизация имени и аватара из Google
- **Безопасность** - защита от CSRF атак

## 🚨 Важные замечания

1. **Никогда не коммитьте** `.env` файл в репозиторий
2. **Используйте HTTPS** в продакшене
3. **Настройте правильные redirect URIs** для каждого окружения
4. **Регулярно обновляйте** Client Secret

## 📞 Поддержка

Если возникли проблемы, проверьте:
1. Правильность Client ID и Client Secret
2. Настройки redirect URI в Google Console
3. Логи приложения в Docker контейнере
4. Настройки OAuth consent screen
