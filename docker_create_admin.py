#!/usr/bin/env python3
"""
Скрипт для создания админской учетки через Docker
"""

import sys
from pathlib import Path

# Добавляем путь к приложению
sys.path.append(str(Path(__file__).parent))

from database import SessionLocal
from models import User
from werkzeug.security import generate_password_hash

def create_admin():
    print("=== Создание админской учетки ===")
    
    # Получаем данные из переменных окружения или используем дефолтные
    import os
    
    name = os.getenv('ADMIN_NAME', 'Администратор')
    email = os.getenv('ADMIN_EMAIL', 'admin@example.com')
    password = os.getenv('ADMIN_PASSWORD', 'admin123')
    
    print(f"Создаю админа: {name} ({email})")
    
    db = SessionLocal()
    try:
        # Проверяем, существует ли уже пользователь с таким email
        existing_user = db.query(User).filter(User.email == email).first()
        if existing_user:
            print(f"⚠️ Пользователь с email {email} уже существует!")
            
            # Делаем его админом
            existing_user.role = "admin"
            db.commit()
            print(f"✅ Пользователь {existing_user.name} теперь администратор!")
            return
        
        # Создаем нового админа
        admin_user = User(
            name=name,
            email=email,
            password_hash=generate_password_hash(password),
            role="admin"
        )
        
        db.add(admin_user)
        db.commit()
        db.refresh(admin_user)
        
        print(f"✅ Администратор создан успешно!")
        print(f"   ID: {admin_user.id}")
        print(f"   Имя: {admin_user.name}")
        print(f"   Email: {admin_user.email}")
        print(f"   Роль: {admin_user.role}")
        
    except Exception as e:
        print(f"❌ Ошибка при создании администратора: {e}")
        db.rollback()
        raise
    finally:
        db.close()

def list_admins():
    """Показать всех админов"""
    db = SessionLocal()
    try:
        admins = db.query(User).filter(User.role == "admin").all()
        
        if not admins:
            print("📋 Администраторов не найдено")
            return
        
        print("📋 Список администраторов:")
        for admin in admins:
            print(f"   ID: {admin.id} | {admin.name} | {admin.email}")
            
    except Exception as e:
        print(f"❌ Ошибка при получении списка админов: {e}")
    finally:
        db.close()

def main():
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "list":
        list_admins()
    else:
        create_admin()

if __name__ == "__main__":
    main()

