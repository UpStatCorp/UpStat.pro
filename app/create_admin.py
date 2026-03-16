#!/usr/bin/env python3
"""
Скрипт для создания первого администратора
Использование: python create_admin.py <email> <password>
"""

import sys
import os
from sqlalchemy.orm import Session
from database import SessionLocal
from models import User
from security import hash_password

def create_admin(email: str, password: str):
    """Создает администратора с указанным email и паролем"""
    db = SessionLocal()
    try:
        # Проверяем, существует ли пользователь
        existing_user = db.query(User).filter(User.email == email.lower().strip()).first()
        
        if existing_user:
            # Обновляем роль существующего пользователя
            existing_user.role = "admin"
            db.commit()
            print(f"✅ Пользователь {email} теперь администратор")
        else:
            # Создаем нового администратора
            admin_user = User(
                email=email.lower().strip(),
                name="Администратор",
                password_hash=hash_password(password),
                role="admin"
            )
            db.add(admin_user)
            db.commit()
            print(f"✅ Создан новый администратор: {email}")
            
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        db.rollback()
    finally:
        db.close()

def main():
    if len(sys.argv) != 3:
        print("Использование: python create_admin.py <email> <password>")
        print("Пример: python create_admin.py admin@example.com mypassword")
        sys.exit(1)
    
    email = sys.argv[1]
    password = sys.argv[2]
    
    if len(password) < 6:
        print("❌ Пароль должен быть не менее 6 символов")
        sys.exit(1)
    
    create_admin(email, password)

if __name__ == "__main__":
    main()

