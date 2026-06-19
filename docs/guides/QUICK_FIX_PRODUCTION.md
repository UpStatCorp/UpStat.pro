# 🚨 Быстрое исправление Google OAuth на сервере

## Что нужно сделать СЕЙЧАС:

### 1. Обновите Google Cloud Console (5 минут)
1. Зайдите в [Google Cloud Console](https://console.cloud.google.com/)
2. Найдите ваш OAuth 2.0 Client ID
3. В "Authorized redirect URIs" добавьте:
   ```
   https://up-stat.com/auth/google/callback
   ```
4. В "Authorized JavaScript origins" добавьте:
   ```
   https://up-stat.com
   ```

### 2. Обновите .env файл на сервере (2 минуты)
Добавьте или измените в .env файле:
```env
ENVIRONMENT=production
GOOGLE_REDIRECT_URI=https://up-stat.com/auth/google/callback
```

### 3. Перезапустите приложение (1 минута)
```bash
docker-compose down
docker-compose up --build -d
```

### 4. Проверьте работу (1 минута)
Откройте `https://up-stat.com/login` и попробуйте войти через Google.

## 🔧 Если не работает - проверьте:

### Проверка переменных окружения:
```bash
docker-compose exec backend env | grep GOOGLE
```

### Проверка логов:
```bash
docker-compose logs backend | grep -i "oauth\|google\|error"
```

### Проверка Google Console:
- Убедитесь, что redirect URI точно `https://up-stat.com/auth/google/callback`
- Проверьте, что используется HTTPS (не HTTP)
- Убедитесь, что домен up-stat.com добавлен в authorized domains

## ⚡ Автоматический скрипт:
```bash
./setup_production_oauth.sh
```

## 📞 Если ничего не помогает:
1. Проверьте, что SSL сертификат работает на up-stat.com
2. Убедитесь, что порт 443 открыт
3. Проверьте, что nginx/прокси правильно настроен
4. Посмотрите логи nginx на предмет ошибок

---
**Время выполнения: ~10 минут**










