#!/usr/bin/env python3
"""
Скрипт для исправления схемы базы данных для поддержки Google OAuth
"""
import sqlite3
import os
import sys

def fix_database_schema():
    """Исправляет схему базы данных для поддержки Google OAuth"""
    
    # Определяем путь к базе данных
    db_path = "app.db"
    if not os.path.exists(db_path):
        print(f"❌ База данных {db_path} не найдена")
        return False
    
    print(f"🔧 Исправляем схему базы данных: {db_path}")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Проверяем текущую схему таблицы users
        cursor.execute("PRAGMA table_info(users)")
        columns = cursor.fetchall()
        
        print("📋 Текущие колонки в таблице users:")
        for col in columns:
            print(f"  - {col[1]} ({col[2]}) - nullable: {not col[3]}")
        
        # Проверяем, есть ли уже поля для OAuth
        column_names = [col[1] for col in columns]
        
        # Добавляем google_id если его нет
        if 'google_id' not in column_names:
            cursor.execute("ALTER TABLE users ADD COLUMN google_id VARCHAR(255)")
            print("✅ Добавлено поле google_id")
        else:
            print("✅ Поле google_id уже существует")
        
        # Добавляем is_oauth_user если его нет
        if 'is_oauth_user' not in column_names:
            cursor.execute("ALTER TABLE users ADD COLUMN is_oauth_user BOOLEAN DEFAULT 0")
            print("✅ Добавлено поле is_oauth_user")
        else:
            print("✅ Поле is_oauth_user уже существует")
        
        # Создаем индекс для google_id если его нет
        cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='ix_users_google_id'")
        if not cursor.fetchone():
            cursor.execute("CREATE UNIQUE INDEX ix_users_google_id ON users(google_id)")
            print("✅ Создан индекс ix_users_google_id")
        else:
            print("✅ Индекс ix_users_google_id уже существует")
        
        # ПРОБЛЕМА: SQLite не поддерживает ALTER COLUMN для изменения NOT NULL
        # Нужно создать новую таблицу с правильной схемой
        
        print("\n🔧 Исправляем ограничение NOT NULL для password_hash...")
        
        # Создаем новую таблицу с правильной схемой
        cursor.execute("""
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
        """)
        
        # Копируем данные из старой таблицы
        cursor.execute("""
            INSERT INTO users_new 
            (id, email, password_hash, name, phone, avatar, role, google_id, is_oauth_user, created_at, updated_at)
            SELECT 
                id, email, password_hash, name, phone, avatar, role, 
                COALESCE(google_id, NULL) as google_id,
                COALESCE(is_oauth_user, 0) as is_oauth_user,
                created_at, updated_at
            FROM users
        """)
        
        # Удаляем старую таблицу
        cursor.execute("DROP TABLE users")
        
        # Переименовываем новую таблицу
        cursor.execute("ALTER TABLE users_new RENAME TO users")
        
        # Создаем индексы
        cursor.execute("CREATE UNIQUE INDEX ix_users_email ON users(email)")
        cursor.execute("CREATE INDEX ix_users_id ON users(id)")
        cursor.execute("CREATE INDEX ix_users_role ON users(role)")
        cursor.execute("CREATE UNIQUE INDEX ix_users_google_id ON users(google_id)")
        
        conn.commit()
        print("✅ Схема базы данных успешно исправлена!")
        
        # Проверяем результат
        cursor.execute("PRAGMA table_info(users)")
        new_columns = cursor.fetchall()
        
        print("\n📋 Новая схема таблицы users:")
        for col in new_columns:
            nullable = "NULL" if not col[3] else "NOT NULL"
            print(f"  - {col[1]} ({col[2]}) - {nullable}")
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при исправлении схемы: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def test_oauth_user_creation():
    """Тестирует создание OAuth пользователя"""
    db_path = "app.db"
    if not os.path.exists(db_path):
        print(f"❌ База данных {db_path} не найдена")
        return False
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Тестируем вставку OAuth пользователя
        test_data = (
            'test@example.com',
            None,  # password_hash = NULL для OAuth пользователя
            'Test User',
            None,  # phone
            'https://example.com/avatar.jpg',
            'user',
            '123456789',  # google_id
            1,  # is_oauth_user = True
            '2025-01-16 15:30:00',
            None
        )
        
        cursor.execute("""
            INSERT INTO users 
            (email, password_hash, name, phone, avatar, role, google_id, is_oauth_user, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, test_data)
        
        # Удаляем тестового пользователя
        cursor.execute("DELETE FROM users WHERE email = 'test@example.com'")
        
        conn.commit()
        print("✅ Тест создания OAuth пользователя прошел успешно!")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при тестировании: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

if __name__ == "__main__":
    print("🚀 Исправление схемы базы данных для Google OAuth")
    print("=" * 60)
    
    # Создаем резервную копию
    if os.path.exists("app.db"):
        import shutil
        shutil.copy2("app.db", "app.db.backup")
        print("💾 Создана резервная копия: app.db.backup")
    
    # Исправляем схему
    if fix_database_schema():
        print("\n🧪 Тестируем создание OAuth пользователя...")
        if test_oauth_user_creation():
            print("\n🎉 Все исправлено! Google OAuth должен работать.")
        else:
            print("\n❌ Тест не прошел. Проверьте схему базы данных.")
    else:
        print("\n❌ Не удалось исправить схему базы данных.")
    
    print("\n" + "=" * 60)
    print("✅ Готово!")










