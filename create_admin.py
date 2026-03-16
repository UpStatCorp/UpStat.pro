#!/usr/bin/env python3
"""
Скрипт для создания админской учетки на сервере
"""

import sys
from pathlib import Path

# Добавляем путь к приложению
sys.path.append(str(Path(__file__).parent / "app"))

from database import SessionLocal
from models import User
from werkzeug.security import generate_password_hash

def create_admin():
    print("=== Создание админской учетки ===")
    
    # Получаем данные от пользователя
    name = input("Введите имя администратора: ").strip()
    if not name:
        print("❌ Имя не может быть пустым!")
        return
    
    email = input("Введите email администратора: ").strip()
    if not email:
        print("❌ Email не может быть пустым!")
        return
    
    password = input("Введите пароль администратора: ").strip()
    if not password:
        print("❌ Пароль не может быть пустым!")
        return
    
    db = SessionLocal()
    try:
        # Проверяем, существует ли уже пользователь с таким email
        existing_user = db.query(User).filter(User.email == email).first()
        if existing_user:
            print(f"❌ Пользователь с email {email} уже существует!")
            
            # Предлагаем сделать его админом
            make_admin = input("Сделать этого пользователя админом? (y/n): ").strip().lower()
            if make_admin == 'y':
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
    print("🔧 Управление админскими учетками")
    print("1. Создать нового администратора")
    print("2. Показать список администраторов")
    print("3. Выход")
    
    choice = input("\nВыберите действие (1-3): ").strip()
    
    if choice == "1":
        create_admin()
    elif choice == "2":
        list_admins()
    elif choice == "3":
        print("👋 До свидания!")
    else:
        print("❌ Неверный выбор!")

if __name__ == "__main__":
    main()

