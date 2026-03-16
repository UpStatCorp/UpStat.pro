# Настройка переменных окружения

## Создайте файл .env в корневой директории проекта:

```bash
# ===== Zoom API Credentials =====
# Получите эти credentials на https://marketplace.zoom.us/
# 1. Войдите в аккаунт Zoom
# 2. Перейдите в "Develop" → "Build App"
# 3. Выберите "Server-to-Server OAuth"
# 4. Скопируйте credentials из раздела "App Credentials"

ZOOM_CLIENT_ID=your_zoom_client_id_here
ZOOM_CLIENT_SECRET=your_zoom_client_secret_here
ZOOM_ACCOUNT_ID=your_zoom_account_id_here

# ===== Zoom Meeting SDK Credentials =====
# Получите эти credentials на https://marketplace.zoom.us/
# 1. Войдите в аккаунт Zoom
# 2. Перейдите в "Develop" → "Build App"
# 3. Выберите "Meeting SDK"
# 4. Заполните информацию о приложении:
#    - App name: "AI Agent Bot"
#    - App type: "Meeting SDK"
#    - User type: "Account"
# 5. В разделе "App Credentials" скопируйте SDK Key и SDK Secret
# 6. В разделе "Meeting SDK" настройте:
#    - Enable Meeting SDK
#    - Add domains: localhost, 127.0.0.1

ZOOM_SDK_KEY=your_zoom_sdk_key_here
ZOOM_SDK_SECRET=your_zoom_sdk_secret_here

# ===== AI Services API Keys =====
# Получите API ключи на соответствующих сайтах

OPENAI_API_KEY=your_openai_api_key_here
ELEVENLABS_API_KEY=your_elevenlabs_api_key_here
DEEPGRAM_API_KEY=your_deepgram_api_key_here

# ===== Application Settings =====
SECRET_KEY=dev_secret_key_change_me_in_production
```

## Пошаговая инструкция получения Zoom Meeting SDK credentials:

### 1. Перейдите на https://marketplace.zoom.us/
### 2. Войдите в аккаунт Zoom
### 3. Нажмите "Develop" → "Build App"
### 4. Выберите "Meeting SDK"
### 5. Заполните информацию о приложении:
   - **App name**: "AI Agent Bot"
   - **App type**: "Meeting SDK"
   - **User type**: "Account"
### 6. В разделе "App Credentials" скопируйте:
   - **SDK Key**
   - **SDK Secret**
### 7. В разделе "Meeting SDK" настройте:
   - ✅ **Enable Meeting SDK**
   - **Add domains**: `localhost`, `127.0.0.1`
### 8. Сохраните изменения

## Запуск после настройки:

```bash
# Обновите .env файл с вашими credentials
# Затем запустите:
docker-compose build sdk-runner
docker-compose up -d
```

## Проверка работы:

1. Создайте Zoom встречу через веб-интерфейс
2. Нажмите "Войти в встречу"
3. Агент должен автоматически подключиться через 3 секунды
4. Агент будет бесконечно повторять "Привет, друг!" каждые 10 секунд


