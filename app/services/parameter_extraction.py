"""
Сервис извлечения структурированных параметров из транскрипта звонка.
Работает последовательно ПОСЛЕ основного анализа pipeline.
Параметры берутся динамически из таблицы parameter_definitions.

Гибридный подход:
- Первые 4 числовых параметра (talk_listen_ratio, avg_manager_reply_len, avg_client_reply_len, dialogue_density) 
  рассчитываются программно из dialogue_json
- Остальные 61 параметр извлекаются через GPT-4o
"""

import json
import asyncio
import logging
from typing import Optional, List, Dict, Any

from openai import OpenAI
from sqlalchemy.orm import Session

from models import ParameterDefinition, ParameterValue, Conversation
from database import SessionLocal

import os
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("main")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
_client = OpenAI(api_key=OPENAI_API_KEY)


def _calculate_dialogue_metrics(dialogue_json_str: str) -> Dict[str, Any]:
    """
    Программно рассчитывает 4 базовых числовых параметра из dialogue_json:
    1. talk_listen_ratio - процент времени речи менеджера
    2. avg_manager_reply_len - средняя длина реплики менеджера (слов)
    3. avg_client_reply_len - средняя длина реплики клиента (слов)
    4. dialogue_density - количество смен ролей на минуту
    """
    try:
        dialogue = json.loads(dialogue_json_str)
        
        manager_words = 0
        client_words = 0
        manager_turns = 0
        client_turns = 0
        total_turns = 0
        
        # Определяем роли
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
        
        # 1. talk_listen_ratio
        talk_listen_ratio = round((manager_words / total_words * 100), 1) if total_words > 0 else 50.0
        
        # 2. avg_manager_reply_len
        avg_manager_reply_len = round(manager_words / manager_turns, 1) if manager_turns > 0 else 0.0
        
        # 3. avg_client_reply_len
        avg_client_reply_len = round(client_words / client_turns, 1) if client_turns > 0 else 0.0
        
        # 4. dialogue_density (реплик/минуту, предполагаем ~5 слов/сек = 300 слов/мин)
        estimated_duration_min = total_words / 150  # примерная длительность в минутах
        dialogue_density = round(total_turns / estimated_duration_min, 1) if estimated_duration_min > 0 else 0.0
        
        return {
            "talk_listen_ratio": {"value": talk_listen_ratio, "confidence": 95},
            "avg_manager_reply_len": {"value": avg_manager_reply_len, "confidence": 95},
            "avg_client_reply_len": {"value": avg_client_reply_len, "confidence": 95},
            "dialogue_density": {"value": dialogue_density, "confidence": 85},
        }
    
    except Exception as e:
        logger.warning(f"Ошибка расчёта метрик диалога: {e}")
        return {}


def _build_extraction_prompt(param_defs: List[ParameterDefinition]) -> str:
    """
    Генерирует промпт для извлечения параметров динамически из справочника.
    Исключает параметры, которые рассчитываются программно.
    """
    # Исключаем параметры, которые рассчитываем программно
    excluded_codes = {"talk_listen_ratio", "avg_manager_reply_len", "avg_client_reply_len", "dialogue_density"}
    
    params_desc = []
    example_json = {}
    
    filtered_params = [p for p in param_defs if p.code not in excluded_codes]
    
    for i, p in enumerate(filtered_params, start=1):
        unit_str = f" (единица: {p.unit})" if p.unit else ""
        type_hint = ""
        example_val = None
        
        if p.value_type == "number":
            type_hint = "число"
            example_val = 75 if "%" in (p.unit or "") else 10
        elif p.value_type == "boolean":
            type_hint = "true/false"
            example_val = True
        elif p.value_type == "text":
            type_hint = "текст/JSON"
            example_val = "example_value"
        
        params_desc.append(
            f"{i}. {p.code} — {p.title}: {p.description or 'N/A'}{unit_str} [{type_hint}]"
        )
        
        example_json[p.code] = {"value": example_val, "confidence": 80}
    
    params_list = "\n".join(params_desc)
    example_str = json.dumps(example_json, ensure_ascii=False, indent=2)
    
    prompt = f"""Ты — аналитик телефонных продаж. Проанализируй транскрипт звонка и извлеки параметры.

ПАРАМЕТРЫ ({len(filtered_params)} шт):
{params_list}

ПРАВИЛА:
- Верни строго JSON-объект с ключами = кодам параметров.
- Для числовых параметров — число (int или float).
- Для boolean — true или false.
- Для text параметров — строка (если JSON — оставь как строку).
- Добавь поле "confidence" (0-100) для каждого параметра — насколько ты уверен в значении.
- Если параметр невозможно определить из текста — поставь null или пропусти.

ФОРМАТ ОТВЕТА (строго JSON, без markdown):
{example_str}

ТРАНСКРИПТ:
"""
    return prompt


async def extract_parameters(conversation_id: int, dialogue_json_str: str, db: Optional[Session] = None):
    """
    Извлекает все активные параметры из транскрипта и сохраняет в parameter_values.
    Вызывается последовательно после основного анализа pipeline.
    
    Гибридный подход:
    - Первые 4 числовых параметра рассчитываются программно
    - Остальные 61 параметр извлекаются через GPT-4o
    """
    own_session = db is None
    if own_session:
        db = SessionLocal()

    try:
        # Получаем дату звонка из conversation
        conversation = db.query(Conversation).filter(Conversation.id == conversation_id).first()
        if not conversation:
            logger.warning(f"Conversation {conversation_id} не найдена — пропускаю извлечение")
            return
        
        call_date = conversation.created_at
        
        param_defs = db.query(ParameterDefinition).filter(
            ParameterDefinition.is_active == True
        ).order_by(ParameterDefinition.id).all()
        
        if not param_defs:
            logger.warning("Справочник параметров пуст — пропускаю извлечение")
            return

        code_to_def = {p.code: p for p in param_defs}
        
        logger.info(f"Извлечение {len(param_defs)} параметров для conversation_id={conversation_id}")

        # 1. Программно рассчитываем первые 4 метрики
        calculated_metrics = _calculate_dialogue_metrics(dialogue_json_str)
        
        # 2. Извлекаем остальные параметры через GPT
        prompt = _build_extraction_prompt(param_defs) + dialogue_json_str

        response = await asyncio.to_thread(
            lambda: _client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                response_format={"type": "json_object"},
            )
        )

        raw = response.choices[0].message.content.strip()
        gpt_data = json.loads(raw)
        
        # 3. Объединяем программные расчёты и GPT результаты
        data = {**calculated_metrics, **gpt_data}

        saved = 0
        for code, pdef in code_to_def.items():
            entry = data.get(code)
            if entry is None:
                continue

            val = entry.get("value") if isinstance(entry, dict) else entry
            confidence = entry.get("confidence", 80) if isinstance(entry, dict) else 80

            if val is None:
                continue

            pv = ParameterValue(
                conversation_id=conversation_id,
                parameter_id=pdef.id,
                confidence=confidence,
                created_at=call_date,
            )

            if pdef.value_type == "number":
                pv.value_number = float(val) if val is not None else None
            elif pdef.value_type == "boolean":
                pv.value_bool = bool(val)
            elif pdef.value_type == "text":
                pv.value_text = str(val) if val is not None else None

            db.add(pv)
            saved += 1

        db.commit()
        logger.info(f"Параметры извлечены: {saved}/{len(code_to_def)} для conversation_id={conversation_id} (4 программных + {saved-4} через GPT)")

    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга JSON от GPT при извлечении параметров: {e}")
    except Exception as e:
        logger.error(f"Ошибка извлечения параметров для conversation_id={conversation_id}: {e}", exc_info=True)
    finally:
        if own_session:
            db.close()
