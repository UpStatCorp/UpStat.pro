#!/usr/bin/env python3
"""
Скрипт для тестирования использования промпта в чате тренера
"""
import asyncio
import sys
import os

# Добавляем путь к приложению
sys.path.append('app')

from database import SessionLocal
from services.prompt_service import PromptService
from services.pipeline_trener import safe_format_prompt

async def test_prompt_usage():
    """Тестирует, какой промпт будет использоваться"""
    print("🔍 Тестирование использования промпта в чате тренера")
    print("=" * 60)
    
    db = SessionLocal()
    try:
        prompt_service = PromptService(db)
        
        # Проверяем активный промпт
        active_prompt = prompt_service.get_active_prompt("sales_trainer")
        
        if active_prompt:
            print("✅ НАЙДЕН АКТИВНЫЙ ПРОМПТ:")
            print(f"   📋 Версия: v{active_prompt.version}")
            print(f"   📝 Название: {active_prompt.title}")
            print(f"   👤 Автор: {active_prompt.creator.name if active_prompt.creator else 'Неизвестно'}")
            print(f"   📅 Создан: {active_prompt.created_at.strftime('%d.%m.%Y %H:%M') if active_prompt.created_at else 'Неизвестно'}")
            print(f"   📏 Размер: {len(active_prompt.content)} символов")
            print(f"   🔤 Начало промпта:")
            print(f"   {active_prompt.content[:300]}...")
            print()
            
            # Симулируем форматирование промпта
            test_data = {
                "name": "Тестовый чек-лист",
                "items": [
                    {"id": 1, "text": "Тестовый пункт 1", "weight": 1.0},
                    {"id": 2, "text": "Тестовый пункт 2", "weight": 1.0}
                ]
            }
            
            test_dialogue = '{"speakers": [{"id": "speaker_1", "label": "Менеджер"}], "turns": [{"speaker": "speaker_1", "text": "Привет!", "start": 0, "end": 2}]}'
            
            try:
                # Используем новую универсальную функцию форматирования
                formatted_prompt = safe_format_prompt(
                    active_prompt.content,
                    data=str(test_data),
                    dialogue_json_str=test_dialogue,
                    checklist_title="Тестовый чек-лист",
                    checklist_name="test_checklist"
                )
                print("✅ ПРОМПТ УСПЕШНО ФОРМАТИРУЕТСЯ:")
                print(f"   📊 Итоговый размер: {len(formatted_prompt)} символов")
                print(f"   🎯 Начало итогового промпта:")
                print(f"   {formatted_prompt[:400]}...")
            except Exception as e:
                print(f"❌ ОШИБКА ПРИ ФОРМАТИРОВАНИИ: {e}")
                
        else:
            print("❌ АКТИВНЫЙ ПРОМПТ НЕ НАЙДЕН!")
            print("   Будет использоваться встроенный fallback промпт")
            
        print()
        print("📋 ВСЕ ВЕРСИИ ПРОМПТОВ:")
        all_prompts = prompt_service.get_prompt_versions("sales_trainer")
        for p in all_prompts:
            status = "✅ АКТИВЕН" if p.is_active else "⏸️ Неактивен"
            print(f"   - v{p.version}: {p.title} ({status})")
            
    except Exception as e:
        print(f"❌ Ошибка: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(test_prompt_usage())

