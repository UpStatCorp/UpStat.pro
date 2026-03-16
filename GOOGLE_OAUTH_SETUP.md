# Настройка Google OAuth

## 1. Создание проекта в Google Cloud Console

1. Перейдите в [Google Cloud Console](https://console.cloud.google.com/)
2. Создайте новый проект или выберите существующий
3. Включите Google+ API:
   - Перейдите в "APIs & Services" > "Library"
   - Найдите "Google+ API" и включите его

## 2. Настройка OAuth 2.0

1. Перейдите в "APIs & Services" > "Credentials"
2. Нажмите "Create Credentials" > "OAuth 2.0 Client IDs"
3. Выберите "Web application"
4. Настройте:
   - **Name**: SaaS Оценка (или любое другое название)
   - **Authorized JavaScript origins**: 
     - `http://localhost:8000` (для разработки)
     - `https://up-stat.com` (для продакшена)
   - **Authorized redirect URIs**:
     - `http://localhost:8000/auth/google/callback` (для разработки)
     - `https://up-stat.com/auth/google/callback` (для продакшена)

## 3. Получение учетных данных

После создания OAuth 2.0 клиента вы получите:
- **Client ID** - скопируйте в `GOOGLE_CLIENT_ID`
- **Client Secret** - скопируйте в `GOOGLE_CLIENT_SECRET`

## 4. Настройка переменных окружения

Создайте файл `.env` на основе `env.example` и добавьте:

```env
# Google OAuth настройки
GOOGLE_CLIENT_ID=your_google_client_id_here
GOOGLE_CLIENT_SECRET=your_google_client_secret_here
GOOGLE_REDIRECT_URI=https://up-stat.com/auth/google/callback

# Окружение (development/production)
ENVIRONMENT=production
```

## 5. Установка зависимостей

```bash
pip install -r requirements.txt
```

## 6. Применение миграций

```bash
cd app
alembic upgrade head
```

## 7. Запуск приложения

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

## 8. Тестирование

1. Откройте `http://localhost:8000/login`
2. Нажмите кнопку "Войти через Google"
3. Авторизуйтесь через Google
4. Вы должны быть перенаправлены в кабинет

## Безопасность

- Никогда не коммитьте `.env` файл в репозиторий
- Используйте HTTPS в продакшене
- Регулярно обновляйте Client Secret
- Настройте правильные redirect URIs для каждого окружения

## Возможные проблемы

### Ошибка "redirect_uri_mismatch"
- Проверьте, что redirect URI в Google Console точно совпадает с `GOOGLE_REDIRECT_URI`
- Убедитесь, что используется правильный протокол (http/https)

### Ошибка "invalid_client"
- Проверьте правильность Client ID и Client Secret
- Убедитесь, что OAuth consent screen настроен

### Ошибка "access_denied"
- Пользователь отклонил разрешения
- Проверьте настройки OAuth consent screen
