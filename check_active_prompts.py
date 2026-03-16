#!/usr/bin/env python3
"""
Проверка всех активных промптов в базе данных
"""
import sys
sys.path.append('app')

from database import SessionLocal
from models import Prompt

def check_all_active_prompts():
    """Проверяет все активные промпты в БД"""
    print("🔍 Проверка всех активных промптов:")
    print("=" * 50)
    
    db = SessionLocal()
    try:
        # Получаем все активные промпты
        active_prompts = db.query(Prompt).filter(Prompt.is_active == True).all()
        
        print(f"📊 Найдено активных промптов: {len(active_prompts)}")
        print()
        
        for prompt in active_prompts:
            print(f"✅ АКТИВЕН: {prompt.name}")
            print(f"   📝 Название: {prompt.title}")
            print(f"   📋 Версия: v{prompt.version}")
            print(f"   👤 Автор: {prompt.creator.name if prompt.creator else 'Неизвестно'}")
            print(f"   📅 Создан: {prompt.created_at.strftime('%d.%m.%Y %H:%M') if prompt.created_at else 'Неизвестно'}")
            print(f"   📏 Размер: {len(prompt.content)} символов")
            print(f"   🔤 Начало: {prompt.content[:100]}...")
            print()
            
        # Проверяем конкретно sales_trainer
        trainer_prompt = db.query(Prompt).filter(
            Prompt.name == "sales_trainer", 
            Prompt.is_active == True
        ).first()
        
        if trainer_prompt:
            print("🎯 ПРОМПТ ТРЕНЕРА НАЙДЕН:")
            print(f"   📝 Название: {trainer_prompt.title}")
            print(f"   🔤 Начало промпта:")
            print(f"   {trainer_prompt.content[:200]}...")
            print()
            
            # Проверяем, есть ли в промпте элементы рассказчика
            content_lower = trainer_prompt.content.lower()
            if "дорогой мой друг" in content_lower:
                print("✅ ПРОМПТ РАССКАЗЧИКА ОБНАРУЖЕН!")
            else:
                print("❌ ПРОМПТ РАССКАЗЧИКА НЕ ОБНАРУЖЕН!")
                
        else:
            print("❌ АКТИВНЫЙ ПРОМПТ ТРЕНЕРА НЕ НАЙДЕН!")
            
    except Exception as e:
        print(f"❌ Ошибка: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    check_all_active_prompts()
