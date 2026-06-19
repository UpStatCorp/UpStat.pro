# 🚨 Исправление ошибки Google OAuth на сервере

## Проблема:
```
(sqlite3.IntegrityError) NOT NULL constraint failed: users.password_hash
```

## Причина:
Поле `password_hash` в базе данных все еще имеет ограничение NOT NULL, но для OAuth пользователей мы пытаемся вставить NULL.

## 🔧 Быстрое исправление:

### Вариант 1: Автоматический скрипт (рекомендуется)
```bash
# Загрузите скрипт на сервер и запустите
python fix_oauth_database.py
```

### Вариант 2: Ручное исправление
```bash
# 1. Создайте резервную копию
cp app.db app.db.backup

# 2. Запустите исправление через Python
python3 -c "
import sqlite3
conn = sqlite3.connect('app.db')
cursor = conn.cursor()

# Создаем новую таблицу с правильной схемой
cursor.execute('''
    CREATE TABLE users_new (
        id INTEGER NOT NULL,
        email VARCHAR(255) NOT NULL,
        password_hash VARCHAR(255),
        name VARCHAR(120) NOT NULL,
        phone VARCHAR(20),
        avatar VARCHAR(512),
        role VARCHAR(10) NOT NULL DEFAULT 'user',
        google_id VARCHAR(255),
        is_oauth_user BOOLEAN NOT NULL DEFAULT 0,
        created_at DATETIME,
        updated_at VARCHAR,
        PRIMARY KEY (id)
    )
''')

# Копируем данные
cursor.execute('''
    INSERT INTO users_new 
    (id, email, password_hash, name, phone, avatar, role, google_id, is_oauth_user, created_at, updated_at)
    SELECT 
        id, email, password_hash, name, phone, avatar, role, 
        COALESCE(google_id, NULL) as google_id,
        COALESCE(is_oauth_user, 0) as is_oauth_user,
        created_at, updated_at
    FROM users
''')

# Заменяем таблицу
cursor.execute('DROP TABLE users')
cursor.execute('ALTER TABLE users_new RENAME TO users')

# Создаем индексы
cursor.execute('CREATE UNIQUE INDEX ix_users_email ON users(email)')
cursor.execute('CREATE INDEX ix_users_id ON users(id)')
cursor.execute('CREATE INDEX ix_users_role ON users(role)')
cursor.execute('CREATE UNIQUE INDEX ix_users_google_id ON users(google_id)')

conn.commit()
conn.close()
print('База данных исправлена!')
"
```

### Вариант 3: Перезапуск с обновленным кодом
```bash
# 1. Остановите приложение
docker-compose down

# 2. Обновите код (если еще не обновлен)
git pull

# 3. Запустите приложение (автоматически исправит схему)
docker-compose up --build -d
```

## ✅ Проверка исправления:

### 1. Проверьте схему таблицы:
```bash
docker-compose exec backend python3 -c "
import sqlite3
conn = sqlite3.connect('app.db')
cursor = conn.cursor()
cursor.execute('PRAGMA table_info(users)')
for col in cursor.fetchall():
    print(f'{col[1]}: {\"NULL\" if not col[3] else \"NOT NULL\"}')
conn.close()
"
```

### 2. Протестируйте создание OAuth пользователя:
```bash
docker-compose exec backend python3 -c "
import sqlite3
conn = sqlite3.connect('app.db')
cursor = conn.cursor()
try:
    cursor.execute('''
        INSERT INTO users 
        (email, password_hash, name, role, google_id, is_oauth_user, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', ('test@example.com', None, 'Test User', 'user', '123456789', 1, '2025-01-16 15:30:00'))
    cursor.execute('DELETE FROM users WHERE email = ?', ('test@example.com',))
    conn.commit()
    print('✅ Тест прошел успешно!')
except Exception as e:
    print(f'❌ Ошибка: {e}')
finally:
    conn.close()
"
```

### 3. Проверьте работу OAuth:
1. Откройте `https://up-stat.com/login`
2. Нажмите "Войти через Google"
3. Авторизуйтесь через Google

## 🚨 Если ничего не помогает:

### Проверьте логи:
```bash
docker-compose logs backend | grep -i "error\|exception\|oauth"
```

### Проверьте переменные окружения:
```bash
docker-compose exec backend env | grep GOOGLE
```

### Восстановите из резервной копии:
```bash
cp app.db.backup app.db
```

## ⏱️ Время выполнения: 5-10 минут

---
**После исправления Google OAuth должен работать корректно!** ✅










