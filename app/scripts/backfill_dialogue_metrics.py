"""
Скрипт для пересчёта первых 4 параметров для существующих звонков.
Находит все conversations без talk_listen_ratio и рассчитывает:
- talk_listen_ratio
- avg_manager_reply_len
- avg_client_reply_len
- dialogue_density

Использует ту же логику, что и parameter_extraction._calculate_dialogue_metrics()
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import json
from database import SessionLocal
from models import ParameterValue, Conversation, ParameterDefinition, Message, Attachment
from sqlalchemy.orm import joinedload

def calculate_dialogue_metrics(dialogue_json_str: str):
    """
    Программно рассчитывает 4 базовых числовых параметра из dialogue_json.
    """
    try:
        dialogue = json.loads(dialogue_json_str)
        
        manager_words = 0
        client_words = 0
        manager_turns = 0
        client_turns = 0
        total_turns = 0
        
        manager_roles = {"Менеджер", "Manager", "manager", "Продавец", "Seller"}
        
        for item in dialogue:
            role = item.get("role", "")
            text = item.get("text", "")
            words = len(text.split())
            
            is_manager = any(mr in role for mr in manager_roles)
            
            if is_manager:
                manager_words += words
                manager_turns += 1
            else:
                client_words += words
                client_turns += 1
            
            total_turns += 1
        
        total_words = manager_words + client_words
        
        talk_listen_ratio = round((manager_words / total_words * 100), 1) if total_words > 0 else 50.0
        avg_manager_reply_len = round(manager_words / manager_turns, 1) if manager_turns > 0 else 0.0
        avg_client_reply_len = round(client_words / client_turns, 1) if client_turns > 0 else 0.0
        
        estimated_duration_min = total_words / 150
        dialogue_density = round(total_turns / estimated_duration_min, 1) if estimated_duration_min > 0 else 0.0
        
        return {
            "talk_listen_ratio": talk_listen_ratio,
            "avg_manager_reply_len": avg_manager_reply_len,
            "avg_client_reply_len": avg_client_reply_len,
            "dialogue_density": dialogue_density,
        }
    
    except Exception as e:
        print(f"  ⚠️  Ошибка расчёта метрик: {e}")
        return None


def find_dialogue_json(db, conversation_id):
    """Ищет dialogue.json во вложениях сообщений."""
    messages = db.query(Message).filter(
        Message.conversation_id == conversation_id
    ).options(joinedload(Message.attachments)).all()
    
    for msg in messages:
        for att in msg.attachments:
            if att.file_name and "dialogue" in att.file_name.lower() and att.file_name.endswith(".json"):
                file_path = os.path.join("uploads", att.storage_key)
                if os.path.exists(file_path):
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            return f.read()
                    except:
                        pass
    return None


def main():
    db = SessionLocal()
    
    try:
        # Находим определения параметров
        params_map = {}
        for code in ["talk_listen_ratio", "avg_manager_reply_len", "avg_client_reply_len", "dialogue_density"]:
            pd = db.query(ParameterDefinition).filter(ParameterDefinition.code == code).first()
            if not pd:
                print(f"❌ Параметр {code} не найден в справочнике")
                return
            params_map[code] = pd
        
        print(f"✅ Найдены все 4 параметра в справочнике")
        
        # Находим conversations без talk_listen_ratio
        pd_tlr = params_map["talk_listen_ratio"]
        
        convs = db.query(Conversation).filter(
            ~db.query(ParameterValue).filter(
                ParameterValue.conversation_id == Conversation.id,
                ParameterValue.parameter_id == pd_tlr.id
            ).exists()
        ).all()
        
        print(f"\n📊 Найдено conversations без первых 4 параметров: {len(convs)}")
        
        if len(convs) == 0:
            print("✅ Все звонки уже имеют параметры!")
            return
        
        processed = 0
        skipped = 0
        
        for conv in convs:
            print(f"\n🔄 Обработка conversation_id={conv.id}: {conv.title}")
            
            # Ищем dialogue.json
            dialogue_json_str = find_dialogue_json(db, conv.id)
            
            if not dialogue_json_str:
                print(f"  ⚠️  dialogue.json не найден - пропускаем")
                skipped += 1
                continue
            
            # Рассчитываем метрики
            metrics = calculate_dialogue_metrics(dialogue_json_str)
            
            if not metrics:
                print(f"  ⚠️  Не удалось рассчитать метрики - пропускаем")
                skipped += 1
                continue
            
            # Сохраняем параметры
            call_date = conv.created_at
            
            for code, value in metrics.items():
                pdef = params_map[code]
                
                # Проверяем, нет ли уже такого параметра
                existing = db.query(ParameterValue).filter(
                    ParameterValue.conversation_id == conv.id,
                    ParameterValue.parameter_id == pdef.id
                ).first()
                
                if existing:
                    continue
                
                pv = ParameterValue(
                    conversation_id=conv.id,
                    parameter_id=pdef.id,
                    value_number=float(value),
                    confidence=95,
                    created_at=call_date,
                )
                db.add(pv)
            
            db.commit()
            processed += 1
            print(f"  ✅ Добавлено 4 параметра (дата: {call_date.date()})")
        
        print(f"\n{'='*60}")
        print(f"✅ Готово!")
        print(f"   Обработано: {processed}")
        print(f"   Пропущено: {skipped}")
        print(f"   Всего parameter_values: {db.query(ParameterValue).count()}")
        
    finally:
        db.close()


if __name__ == "__main__":
    main()
