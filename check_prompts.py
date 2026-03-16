#!/usr/bin/env python3
"""
Скрипт для проверки промптов в базе данных
"""
from database import SessionLocal
from models import Prompt
from services.prompt_service import PromptService

def check_prompts():
    """Проверяет наличие и статус промптов"""
    db = SessionLocal()
    try:
        prompt_service = PromptService(db)
        
        print("🔍 Проверка промптов в базе данных:")
        print("=" * 50)
        
        # Проверяем промпты для аудита
        audit_prompts = prompt_service.get_prompt_versions("sales_audit_summary")
        print(f"\n📊 Промпты для аудита (sales_audit_summary): {len(audit_prompts)}")
        for p in audit_prompts:
            status = "✅ АКТИВЕН" if p.is_active else "⏸️ Неактивен"
            print(f"  - v{p.version}: {p.title} ({status})")
            print(f"    Создан: {p.created_at.strftime('%d.%m.%Y %H:%M') if p.created_at else 'Неизвестно'}")
            print(f"    Автор: {p.creator.name if p.creator else 'Неизвестно'}")
            print(f"    Содержимое: {len(p.content)} символов")
            print()
        
        # Проверяем промпты для тренера
        trainer_prompts = prompt_service.get_prompt_versions("sales_trainer")
        print(f"\n🎯 Промпты для тренера (sales_trainer): {len(trainer_prompts)}")
        for p in trainer_prompts:
            status = "✅ АКТИВЕН" if p.is_active else "⏸️ Неактивен"
            print(f"  - v{p.version}: {p.title} ({status})")
            print(f"    Создан: {p.created_at.strftime('%d.%m.%Y %H:%M') if p.created_at else 'Неизвестно'}")
            print(f"    Автор: {p.creator.name if p.creator else 'Неизвестно'}")
            print(f"    Содержимое: {len(p.content)} символов")
            print()
        
        # Проверяем активные промпты
        active_audit = prompt_service.get_active_prompt("sales_audit_summary")
        active_trainer = prompt_service.get_active_prompt("sales_trainer")
        
        print("🎯 Активные промпты:")
        if active_audit:
            print(f"  ✅ Аудит: v{active_audit.version} - {active_audit.title}")
        else:
            print("  ❌ Аудит: Нет активного промпта")
            
        if active_trainer:
            print(f"  ✅ Тренер: v{active_trainer.version} - {active_trainer.title}")
        else:
            print("  ❌ Тренер: Нет активного промпта")
        
        # Статистика
        stats = prompt_service.get_prompt_statistics()
        print(f"\n📈 Статистика:")
        print(f"  - Всего версий: {stats['total_versions']}")
        print(f"  - Активных промптов: {stats['active_prompts']}")
        print(f"  - Уникальных промптов: {stats['unique_prompts']}")
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    check_prompts()










