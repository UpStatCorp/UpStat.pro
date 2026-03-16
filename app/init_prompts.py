#!/usr/bin/env python3
"""
Скрипт для инициализации базовых промптов
"""

import sys
import os
from sqlalchemy.orm import Session
from database import SessionLocal
from models import User, Prompt
from services.prompt_service import PromptService

def init_default_prompts():
    """Инициализация базовых промптов"""
    db = SessionLocal()
    try:
        # Получаем первого администратора
        admin_user = db.query(User).filter(User.role == "admin").first()
        if not admin_user:
            print("❌ Администратор не найден. Создайте администратора сначала.")
            return False
        
        prompt_service = PromptService(db)
        
        # Базовый промпт для аудита продаж
        default_prompt = """Ты — аудитор качества продаж. У тебя есть СТРОГО JSON-диалог двух спикеров с таймкодами.
Формат JSON: { speakers:[{id,label}], role_map:{manager,client}, turns:[{speaker,start,end,text}] }.
role_map сейчас unknown — сперва определи роли.

ШАГ 0 (обязателен): Определи роли manager/client.
- Проанализируй реплики и поведение: кто представляется, квалифицирует, презентует продукт,
  обрабатывает возражения, называет цену/условия, делает call-to-action — обычно это менеджер.
- Зафиксируй соответствие: speaker_1 → manager|client, speaker_2 → manager|client.
- Приведи 2–4 короткие цитаты в кавычках «...» с таймкодами [t=мм:сс–мм:сс], подтверждающие выбор.
- Если неоднозначно — выбери более вероятный вариант и объясни кратко.

ШАГ 1: Проверь чек-лист ТОЛЬКО по данным диалога (без домыслов).
Для каждого пункта чек-листа укажи:
1) Статус: Да / Нет / Частично.
2) Короткий комментарий по репликам.
3) Если «Нет» или «Частично» — приведи 1–3 ТОЧНЫЕ ЦИТАТЫ МЕНЕДЖЕРА «...» с таймкодами [t=мм:сс–мм:сс] из turns.
4) Если данных нет — «Не обнаружено в диалоге».

СТРОГО не выдумывай фразы и факты — цитируй только то, что есть в JSON.

ЧЕК-ЛИСТ:
{data}

ДИАЛОГ_JSON:
{dialogue_json_str}
Формат ответа:
=== ROLE MAPPING ===
- speaker_1: manager|client — доказательства: «…» [t=мм:сс–мм:сс]; «…» [t=мм:сс–мм:сс]
- speaker_2: manager|client — доказательства: «…» [t=мм:сс–мм:сс]
=== {НАЗВАНИЕ ЧЕК-ЛИСТА} ===
- [Пункт 1]: Да/Нет/Частично — комментарий. Цитаты (если есть): «…» [t=00:12–00:18]
- [Пункт 2]: ..."""
        
        # Проверяем, есть ли уже промпт sales_audit
        existing_prompt = prompt_service.get_active_prompt("sales_audit")
        if existing_prompt:
            print("✅ Промпт 'sales_audit' уже существует")
            return True
        
        # Создаем базовый промпт
        prompt_service.create_prompt_version(
            name="sales_audit",
            title="Аудит качества продаж",
            content=default_prompt,
            description="Базовый промпт для анализа качества продаж по диалогам с клиентами",
            created_by=admin_user.id
        )
        
        print("✅ Базовый промпт 'sales_audit' создан успешно")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при создании промптов: {e}")
        db.rollback()
        return False
    finally:
        db.close()

def main():
    if init_default_prompts():
        print("🎉 Инициализация промптов завершена успешно!")
    else:
        print("💥 Ошибка при инициализации промптов")
        sys.exit(1)

if __name__ == "__main__":
    main()

