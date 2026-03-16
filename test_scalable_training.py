#!/usr/bin/env python3
"""
Скрипт для тестирования масштабируемой голосовой тренировки.
Проверяет работу системы и даёт рекомендации.
"""

import asyncio
import aiohttp
import time
from datetime import datetime
import sys

BASE_URL = "http://localhost:8000"


async def test_health():
    """Проверка доступности сервера"""
    print("🔍 Проверка доступности сервера...")
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BASE_URL}/voice-training/stats") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    print("✅ Сервер доступен")
                    print(f"   Активных сессий: {data['sessions']['total_sessions']}")
                    print(f"   Максимум сессий: {data['sessions']['max_sessions']}")
                    print(f"   Загрузка: {data['sessions']['capacity_percent']}%")
                    print(f"   Воркеры STT: {data['sessions']['stt_workers']}")
                    return True
                else:
                    print(f"❌ Сервер вернул статус {resp.status}")
                    return False
    except Exception as e:
        print(f"❌ Ошибка подключения: {e}")
        print("   Убедитесь что сервер запущен: python app/main.py")
        return False


async def test_database():
    """Проверка таблиц БД"""
    print("\n🔍 Проверка базы данных...")
    
    try:
        from app.database import SessionLocal
        from app.models import TrainingSession, VoiceTrainingMessage
        
        db = SessionLocal()
        
        # Проверяем наличие полей
        try:
            session = db.query(TrainingSession).first()
            if session:
                # Проверяем новые поля
                _ = session.session_type
                _ = session.websocket_session_id
                _ = session.conversation_history_json
                _ = session.status
                print("✅ Таблица training_sessions обновлена")
            else:
                print("⚠️  Таблица training_sessions пуста (но существует)")
        except AttributeError as e:
            print(f"❌ Миграция не применена: {e}")
            print("   Выполните: cd app && alembic upgrade head")
            db.close()
            return False
        
        # Проверяем таблицу сообщений
        try:
            db.query(VoiceTrainingMessage).first()
            print("✅ Таблица voice_training_messages существует")
        except Exception as e:
            print(f"❌ Таблица voice_training_messages не найдена: {e}")
            db.close()
            return False
        
        db.close()
        return True
        
    except Exception as e:
        print(f"❌ Ошибка БД: {e}")
        return False


async def test_modules():
    """Проверка наличия необходимых модулей"""
    print("\n🔍 Проверка модулей...")
    
    modules = [
        ("voice_assistant.session_manager", "SessionManager"),
        ("voice_assistant.db_service", "VoiceTrainingDBService"),
        ("voice_assistant.websocket_handler", "handle_websocket_connection"),
        ("voice_assistant.router_new", "router"),
    ]
    
    all_ok = True
    for module_name, obj_name in modules:
        try:
            module = __import__(module_name, fromlist=[obj_name])
            getattr(module, obj_name)
            print(f"✅ {module_name}.{obj_name}")
        except ImportError:
            print(f"❌ {module_name} не найден")
            all_ok = False
        except AttributeError:
            print(f"❌ {module_name}.{obj_name} не найден")
            all_ok = False
    
    return all_ok


async def test_concurrent_sessions():
    """Тест создания нескольких сессий"""
    print("\n🔍 Тест одновременных сессий...")
    
    try:
        from voice_assistant.session_manager import get_session_manager
        
        manager = get_session_manager()
        
        # Создаём 5 тестовых сессий
        sessions = []
        for i in range(5):
            session = await manager.create_session(
                user_id=1000 + i,
                training_id=1
            )
            if session:
                sessions.append(session)
        
        print(f"✅ Создано {len(sessions)} тестовых сессий")
        
        # Проверяем статистику
        stats = manager.get_stats()
        print(f"   Активных сессий: {stats['total_sessions']}")
        print(f"   Загрузка: {stats['capacity_percent']}%")
        
        # Закрываем тестовые сессии
        for session in sessions:
            await manager.close_session(session.session_id)
        
        print("✅ Тестовые сессии закрыты")
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка теста сессий: {e}")
        return False


async def performance_test():
    """Тест производительности"""
    print("\n🔍 Тест производительности...")
    
    try:
        from voice_assistant.session_manager import get_session_manager
        
        manager = get_session_manager()
        
        # Тест скорости создания сессий
        start = time.time()
        sessions = []
        
        for i in range(10):
            session = await manager.create_session(
                user_id=2000 + i,
                training_id=1
            )
            if session:
                sessions.append(session)
        
        create_time = time.time() - start
        
        print(f"✅ Создано 10 сессий за {create_time:.2f}s ({create_time/10*1000:.1f}ms на сессию)")
        
        # Тест скорости закрытия
        start = time.time()
        for session in sessions:
            await manager.close_session(session.session_id)
        
        close_time = time.time() - start
        
        print(f"✅ Закрыто 10 сессий за {close_time:.2f}s ({close_time/10*1000:.1f}ms на сессию)")
        
        # Рекомендации
        if create_time / 10 > 0.1:  # >100ms на сессию
            print("⚠️  Создание сессий медленное. Рекомендуется оптимизация.")
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка теста производительности: {e}")
        return False


async def main():
    """Основная функция"""
    print("=" * 60)
    print("🧪 Тестирование масштабируемой голосовой тренировки")
    print("=" * 60)
    
    results = []
    
    # Тесты
    results.append(("Доступность сервера", await test_health()))
    results.append(("База данных", await test_database()))
    results.append(("Модули", await test_modules()))
    results.append(("Одновременные сессии", await test_concurrent_sessions()))
    results.append(("Производительность", await performance_test()))
    
    # Итоги
    print("\n" + "=" * 60)
    print("📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ")
    print("=" * 60)
    
    passed = 0
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status:10} | {name}")
        if result:
            passed += 1
    
    print("=" * 60)
    print(f"Пройдено: {passed}/{len(results)} тестов")
    
    if passed == len(results):
        print("\n🎉 ВСЕ ТЕСТЫ ПРОЙДЕНЫ!")
        print("   Система готова к использованию.")
        print("\n📚 Документация:")
        print("   - VOICE_TRAINING_SCALABLE.md (полная документация)")
        print("   - QUICK_START_SCALABLE_TRAINING.md (быстрый старт)")
    else:
        print("\n⚠️  НЕКОТОРЫЕ ТЕСТЫ НЕ ПРОШЛИ")
        print("   Проверьте ошибки выше и исправьте их.")
        print("\n🔧 Частые проблемы:")
        print("   1. Сервер не запущен: python app/main.py")
        print("   2. Миграция не применена: cd app && alembic upgrade head")
        print("   3. Модули не на месте: проверьте voice_assistant/")
    
    print("=" * 60)
    
    return passed == len(results)


if __name__ == "__main__":
    try:
        success = asyncio.run(main())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\n⚠️  Тестирование прервано")
        sys.exit(1)

