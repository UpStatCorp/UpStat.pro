#!/usr/bin/env python3
"""
Скрипт для создания администратора в PostgreSQL базе данных
Использование:
  docker-compose exec backend python create_postgres_admin.py <email> <password> [name]
  или
  docker-compose run --rm backend python create_postgres_admin.py <email> <password> [name]
"""

import sys
import os

# Добавляем путь к app в PYTHONPATH
# В контейнере: скрипт в /app/, код в /app/app/
script_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.join(script_dir, 'app')
if app_dir not in sys.path:
    sys.path.insert(0, app_dir)

# Импорты из app
from database import SessionLocal
from models import User
from security import hash_password, validate_password_strength, validate_email

def create_admin(email: str, password: str, name: str = None):
    """Создает администратора с указанным email и паролем"""
    
    # Валидация email
    is_valid, error_msg = validate_email(email)
    if not is_valid:
        print(f"❌ Ошибка валидации email: {error_msg}")
        return False
    
    # Валидация пароля
    is_strong, error_msg = validate_password_strength(password)
    if not is_strong:
        print(f"⚠️  Предупреждение о пароле: {error_msg}")
        response = input("Продолжить с этим паролем? (y/n): ").strip().lower()
        if response != 'y':
            print("❌ Создание администратора отменено")
            return False
    
    # Используем переданное имя или дефолтное
    if not name:
        name = "Администратор"
    
    db = SessionLocal()
    try:
        # Проверяем, существует ли пользователь
        existing_user = db.query(User).filter(User.email == email.lower().strip()).first()
        
        if existing_user:
            print(f"⚠️  Пользователь с email {email} уже существует!")
            print(f"   ID: {existing_user.id}")
            print(f"   Имя: {existing_user.name}")
            print(f"   Текущая роль: {existing_user.role}")
            
            # Предлагаем сделать его админом
            if existing_user.role == "admin":
                print("✅ Пользователь уже является администратором")
                return True
            
            make_admin = input("Сделать этого пользователя администратором? (y/n): ").strip().lower()
            if make_admin == 'y':
                existing_user.role = "admin"
                if name and name != "Администратор":
                    existing_user.name = name
                db.commit()
                print(f"✅ Пользователь {existing_user.name} теперь администратор!")
                return True
            else:
                print("❌ Операция отменена")
                return False
        
        # Создаем нового администратора
        admin_user = User(
            email=email.lower().strip(),
            name=name,
            password_hash=hash_password(password),
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
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при создании администратора: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
        return False
    finally:
        db.close()

def list_admins():
    """Показать всех администраторов"""
    db = SessionLocal()
    try:
        admins = db.query(User).filter(User.role == "admin").all()
        
        if not admins:
            print("📋 Администраторов не найдено")
            return
        
        print(f"📋 Найдено администраторов: {len(admins)}")
        print("-" * 60)
        for admin in admins:
            print(f"   ID: {admin.id:4d} | {admin.name:30s} | {admin.email}")
        print("-" * 60)
            
    except Exception as e:
        print(f"❌ Ошибка при получении списка админов: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

def main():
    if len(sys.argv) < 2:
        # Интерактивный режим
        print("🔧 Создание администратора в PostgreSQL")
        print("=" * 60)
        
        email = input("Введите email администратора: ").strip()
        if not email:
            print("❌ Email не может быть пустым!")
            sys.exit(1)
        
        password = input("Введите пароль администратора: ").strip()
        if not password:
            print("❌ Пароль не может быть пустым!")
            sys.exit(1)
        
        name = input("Введите имя администратора (Enter для 'Администратор'): ").strip()
        if not name:
            name = None
        
        success = create_admin(email, password, name)
        sys.exit(0 if success else 1)
    
    if sys.argv[1] == "list":
        list_admins()
    elif sys.argv[1] in ["-h", "--help", "help"]:
        print("🔧 Управление администраторами PostgreSQL")
        print("\nИспользование:")
        print("  Интерактивный режим (без аргументов):")
        print("    docker-compose exec backend python create_postgres_admin.py")
        print("\n  Создать администратора (с аргументами):")
        print("    docker-compose exec backend python create_postgres_admin.py <email> <password> [name]")
        print("\n  Показать список администраторов:")
        print("    docker-compose exec exec backend python create_postgres_admin.py list")
        print("\nПример:")
        print("  docker-compose exec backend python create_postgres_admin.py admin@example.com MySecurePass123 Админ")
    else:
        if len(sys.argv) < 3:
            print("❌ Необходимо указать email и пароль")
            print("Использование: python create_postgres_admin.py <email> <password> [name]")
            print("Или запустите без аргументов для интерактивного режима")
            sys.exit(1)
        
        email = sys.argv[1]
        password = sys.argv[2]
        name = sys.argv[3] if len(sys.argv) > 3 else None
        
        success = create_admin(email, password, name)
        sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()

