#!/usr/bin/env python3
"""
Скрипт для отладки промпта тренера
"""
import sys
import os
sys.path.append('app')

from sqlalchemy.orm import Session
from database import SessionLocal
from models import Prompt
from services.prompt_service import PromptService

def debug_trainer_prompt():
    """Отладка промпта тренера"""
    db = SessionLocal()
    try:
        prompt_service = PromptService(db)
        
        print("🔍 Отладка промпта тренера")
        print("=" * 50)
        
        # Проверяем все версии промпта тренера
        trainer_prompts = prompt_service.get_prompt_versions("sales_trainer")
        
        print(f"📋 Найдено {len(trainer_prompts)} версий промпта 'sales_trainer':")
        
        if not trainer_prompts:
            print("❌ Промпт 'sales_trainer' не найден в базе данных!")
            print("💡 Нужно создать промпт через админ-панель")
            return
        
        active_prompt = None
        for i, prompt in enumerate(trainer_prompts, 1):
            status = "🟢 АКТИВЕН" if prompt.is_active else "⚪ Неактивен"
            print(f"\n{i}. v{prompt.version}: {prompt.title}")
            print(f"   Статус: {status}")
            print(f"   ID: {prompt.id}")
            print(f"   Создан: {prompt.created_at.strftime('%d.%m.%Y %H:%M') if prompt.created_at else 'Неизвестно'}")
            print(f"   Автор: {prompt.creator.name if prompt.creator else 'Неизвестно'}")
            print(f"   Размер: {len(prompt.content)} символов")
            
            # Показываем начало содержимого
            content_preview = prompt.content[:200].replace('\n', ' ')
            print(f"   Начало: {content_preview}...")
            
            if prompt.is_active:
                active_prompt = prompt
        
        # Проверяем активный промпт
        print(f"\n🎯 АКТИВНЫЙ ПРОМПТ:")
        if active_prompt:
            print(f"✅ Активен: v{active_prompt.version} - {active_prompt.title}")
            print(f"   ID: {active_prompt.id}")
            
            # Проверяем, содержит ли он текст рассказчика
            if "рассказчик" in active_prompt.content.lower() or "дорогой мой друг" in active_prompt.content.lower():
                print("✅ Содержит стиль рассказчика")
            else:
                print("❌ НЕ содержит стиль рассказчика")
                print("💡 Возможно, активирована старая версия")
        else:
            print("❌ Нет активного промпта!")
            print("💡 Нужно активировать одну из версий")
        
        # Проверяем, что система получает при запросе
        print(f"\n🔍 ПРОВЕРКА СИСТЕМЫ:")
        retrieved_prompt = prompt_service.get_active_prompt("sales_trainer")
        
        if retrieved_prompt:
            print(f"✅ Система получает: v{retrieved_prompt.version} - {retrieved_prompt.title}")
            print(f"   ID: {retrieved_prompt.id}")
            
            # Проверяем содержимое
            if "рассказчик" in retrieved_prompt.content.lower():
                print("✅ Содержит стиль рассказчика - должно работать!")
            else:
                print("❌ НЕ содержит стиль рассказчика - нужно активировать правильную версию")
        else:
            print("❌ Система не может получить активный промпт!")
        
        # Предлагаем решение
        print(f"\n💡 РЕКОМЕНДАЦИИ:")
        
        if not active_prompt:
            print("1. Активируйте одну из версий промпта через админ-панель")
        elif "рассказчик" not in active_prompt.content.lower():
            print("1. Найдите версию с текстом рассказчика и активируйте её")
            print("2. Или создайте новую версию с правильным текстом")
        else:
            print("1. Промпт настроен правильно")
            print("2. Проверьте, что анализ запускается через правильный пайплайн")
        
        print("\n🔧 КАК АКТИВИРОВАТЬ ПРОМПТ:")
        print("1. Зайдите в админ-панель: https://up-stat.com/admin/prompts")
        print("2. Найдите версию с текстом рассказчика")
        print("3. Нажмите кнопку 'Активировать'")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
    finally:
        db.close()

def show_prompt_content(prompt_id: int):
    """Показывает содержимое конкретного промпта"""
    db = SessionLocal()
    try:
        prompt = db.query(Prompt).filter(Prompt.id == prompt_id).first()
        
        if not prompt:
            print(f"❌ Промпт с ID {prompt_id} не найден")
            return
        
        print(f"📄 Содержимое промпта v{prompt.version} - {prompt.title}")
        print("=" * 80)
        print(prompt.content)
        print("=" * 80)
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        try:
            prompt_id = int(sys.argv[1])
            show_prompt_content(prompt_id)
        except ValueError:
            print("❌ Неверный ID промпта")
    else:
        debug_trainer_prompt()









