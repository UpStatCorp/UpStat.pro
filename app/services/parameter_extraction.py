"""
Сервис извлечения структурированных параметров из транскрипта звонка.
Работает последовательно ПОСЛЕ основного анализа pipeline.
Параметры берутся динамически из таблицы parameter_definitions.
Все параметры (включая числовые метрики) извлекаются через GPT-4o.
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


def _build_extraction_prompt(param_defs: List[ParameterDefinition]) -> str:
    """
    Генерирует промпт для извлечения ВСЕХ параметров динамически из справочника.
    """
    params_desc = []
    example_json = {}
    
    for i, p in enumerate(param_defs, start=1):
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

ПАРАМЕТРЫ ({len(param_defs)} шт):
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
    Извлекает ВСЕ активные параметры из транскрипта через GPT-4o и сохраняет в parameter_values.
    Вызывается последовательно после основного анализа pipeline.
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

        # Извлекаем ВСЕ параметры через GPT
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
        data = json.loads(raw)

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
        logger.info(f"Параметры извлечены: {saved}/{len(code_to_def)} для conversation_id={conversation_id}")

    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга JSON от GPT при извлечении параметров: {e}")
    except Exception as e:
        logger.error(f"Ошибка извлечения параметров для conversation_id={conversation_id}: {e}", exc_info=True)
    finally:
        if own_session:
            db.close()
