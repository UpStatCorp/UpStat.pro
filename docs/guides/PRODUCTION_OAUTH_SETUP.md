# Настройка Google OAuth для продакшена (up-stat.com)

## 🚀 Быстрая настройка для up-stat.com

### 1. Обновите Google Cloud Console

1. Перейдите в [Google Cloud Console](https://console.cloud.google.com/)
2. Найдите ваш OAuth 2.0 Client ID
3. В разделе "Authorized redirect URIs" добавьте:
   ```
   https://up-stat.com/auth/google/callback
   ```
4. В разделе "Authorized JavaScript origins" добавьте:
   ```
   https://up-stat.com
   ```

### 2. Создайте .env файл на сервере

```env
# Основные настройки
ENVIRONMENT=production
SECRET_KEY=your_very_secure_secret_key_here

# Google OAuth настройки
GOOGLE_CLIENT_ID=your_google_client_id_here
GOOGLE_CLIENT_SECRET=your_google_client_secret_here
GOOGLE_REDIRECT_URI=https://up-stat.com/auth/google/callback

# Остальные настройки...
ZOOM_CLIENT_ID=your_zoom_client_id
ZOOM_CLIENT_SECRET=your_zoom_client_secret
# ... и т.д.
```

### 3. Запустите приложение

```bash
# Остановите текущие контейнеры
docker-compose down

# Пересоберите и запустите
docker-compose up --build -d
```

### 4. Проверьте работу

1. Откройте `https://up-stat.com/login`
2. Нажмите "Войти через Google"
3. Авторизуйтесь через Google
4. Вы должны быть перенаправлены в кабинет

## 🔧 Возможные проблемы и решения

### Ошибка "redirect_uri_mismatch"
- Убедитесь, что в Google Console добавлен точно `https://up-stat.com/auth/google/callback`
- Проверьте, что используется HTTPS (не HTTP)

### Ошибка "invalid_client"
- Проверьте правильность Client ID и Client Secret в .env файле
- Убедитесь, что переменные окружения загружены в Docker

### Ошибка "access_denied"
- Проверьте настройки OAuth consent screen в Google Console
- Убедитесь, что домен up-stat.com добавлен в authorized domains

### Проверка переменных окружения в Docker

```bash
# Проверьте, что переменные загружены
docker-compose exec backend env | grep GOOGLE
```

Должно показать:
```
GOOGLE_CLIENT_ID=your_client_id
GOOGLE_CLIENT_SECRET=your_client_secret
GOOGLE_REDIRECT_URI=https://up-stat.com/auth/google/callback
```

## 📊 Логи для диагностики

```bash
# Смотрите логи в реальном времени
docker-compose logs -f backend

# Ищите ошибки OAuth
docker-compose logs backend | grep -i "oauth\|google\|error"
```

## 🎯 Автоматическое определение окружения

Система автоматически определяет окружение по переменной `ENVIRONMENT`:

- `ENVIRONMENT=development` → использует `http://localhost:8000`
- `ENVIRONMENT=production` → использует `https://up-stat.com`

Если переменная не задана, по умолчанию используется development.

## ✅ Чек-лист для продакшена

- [ ] Google OAuth настроен для домена up-stat.com
- [ ] Redirect URI: `https://up-stat.com/auth/google/callback`
- [ ] JavaScript origins: `https://up-stat.com`
- [ ] .env файл создан с правильными настройками
- [ ] ENVIRONMENT=production установлен
- [ ] Docker контейнеры перезапущены
- [ ] HTTPS работает корректно
- [ ] OAuth consent screen настроен
- [ ] Тестирование авторизации прошло успешно

## 🚨 Важные замечания

1. **Никогда не коммитьте .env файл** в репозиторий
2. **Используйте сильный SECRET_KEY** для продакшена
3. **Регулярно обновляйте Client Secret** в Google Console
4. **Мониторьте логи** на предмет ошибок OAuth
5. **Настройте мониторинг** для отслеживания проблем










