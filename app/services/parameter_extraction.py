"""
Сервис извлечения структурированных параметров из транскрипта звонка.
Работает последовательно ПОСЛЕ основного анализа pipeline.
Параметры берутся динамически из таблицы parameter_definitions.
Все параметры (включая числовые метрики) извлекаются через GPT-4o.
"""

import json
import asyncio
import logging
from typing import Optional, List, Dict, Any  # noqa: F401

from openai import OpenAI
from sqlalchemy.orm import Session

from models import ParameterDefinition, ParameterValue, Conversation
from database import SessionLocal

import os
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("main")

from services.ai_provider import get_llm_client, model_main, json_mode_kwargs, extract_json, clamp_max_tokens  # noqa: E402

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
_client = get_llm_client()


def _build_extraction_prompt(
    param_defs: List[ParameterDefinition],
    research_mode: bool = False,
) -> str:
    """
    Генерирует промпт для извлечения ВСЕХ параметров динамически из справочника.

    При research_mode=True добавляет требование вернуть поле reasoning для каждого
    параметра — для прозрачности оценок (включается админом через Research Mode).
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
        
        entry: Dict[str, Any] = {"value": example_val, "confidence": 80}
        if research_mode:
            entry["reasoning"] = "Почему такое значение и confidence (до 250 символов)"
        example_json[p.code] = entry
    
    params_list = "\n".join(params_desc)
    example_str = json.dumps(example_json, ensure_ascii=False, indent=2)

    research_rule = (
        '- Добавь поле "reasoning" для КАЖДОГО параметра — '
        'РАЗВЁРНУТОЕ объяснение (3–5 предложений, 300–600 символов) по схеме: '
        '[1] какой кусок диалога ты использовал (краткая цитата или таймкод); '
        '[2] какие альтернативные значения ты рассматривал; '
        '[3] почему выбрал именно это value; '
        '[4] что обосновывает уровень confidence (что повышало/снижало). '
        'Если значение null — объясни, ЧТО ИМЕННО ты искал и почему не нашёл.\n'
        if research_mode else ""
    )
    
    prompt = f"""Ты — аналитик телефонных продаж. Проанализируй транскрипт звонка и извлеки параметры.

ПАРАМЕТРЫ ({len(param_defs)} шт):
{params_list}

ПРАВИЛА:
- Верни строго JSON-объект с ключами = кодам параметров.
- Для числовых параметров — число (int или float).
- Для boolean — true или false.
- Для text параметров — строка (если JSON — оставь как строку).
- Добавь поле "confidence" для каждого параметра. Используй ТОЛЬКО эти значения:
  * 0 — параметр невозможно определить (value = null)
  * 50 — очень низкая уверенность, данных почти нет
  * 75 — средняя уверенность, данные неоднозначные
  * 90 — высокая уверенность, данные чёткие
  * 99 — абсолютная уверенность, прямое упоминание в тексте
- Если параметр невозможно определить — поставь value: null и confidence: 0.
{research_rule}
ФОРМАТ ОТВЕТА (строго JSON, без markdown):
{example_str}

ТРАНСКРИПТ:
"""
    return prompt


async def extract_parameters(
    conversation_id: int,
    dialogue_json_str: str,
    db: Optional[Session] = None,
    research: Optional[object] = None,
):
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
        prompt = _build_extraction_prompt(param_defs, research_mode=research is not None) + dialogue_json_str

        # В research-режиме reasoning по каждому параметру → больше токенов
        param_call_kwargs = {
            "model": model_main(),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            **json_mode_kwargs(),
        }
        if research is not None:
            param_call_kwargs["max_tokens"] = clamp_max_tokens(16000)
        response = await asyncio.to_thread(
            lambda: _client.chat.completions.create(**param_call_kwargs)
        )

        raw = response.choices[0].message.content.strip()
        data = extract_json(raw)

        if research is not None:
            try:
                # Собираем человекочитаемый reasoning по каждому параметру:
                # почему ИИ выбрал такое значение и такую уверенность.
                reasoning_lines: List[str] = [
                    f"ИИ извлёк значения для {len(data)} параметров из транскрипта. "
                    f"Ниже — обоснование по каждому (где модель его вернула):",
                    "",
                ]
                shown = 0
                for code, entry in data.items():
                    pdef = code_to_def.get(code)
                    title = pdef.title if pdef else code
                    if isinstance(entry, dict):
                        val = entry.get("value")
                        conf = entry.get("confidence")
                        rsn = entry.get("reasoning")
                    else:
                        val, conf, rsn = entry, None, None
                    line = f"━━━ {title} ({code}) ━━━"
                    reasoning_lines.append(line)
                    reasoning_lines.append(
                        f"Значение: {val}" + (f" · confidence {conf}" if conf is not None else "")
                    )
                    if rsn:
                        reasoning_lines.append(f"Почему: {rsn}")
                    reasoning_lines.append("")
                    shown += 1
                reasoning_text = "\n".join(reasoning_lines)

                research.capture_stage(
                    stage_name="Извлечение параметров (динамический справочник)",
                    model=model_main(),
                    prompt=prompt,
                    raw_response=raw,
                    parsed_decisions=data,
                    usage=getattr(response, "usage", None),
                    reasoning_override=reasoning_text,
                )
            except Exception as research_err:  # noqa: BLE001
                logger.warning(f"Research capture (parameter_extraction) failed: {research_err}")
        
        # Логируем ответ GPT для отладки
        logger.info(f"GPT вернул {len(data)} ключей верхнего уровня: {list(data.keys())[:10]}...")
        
        # Логируем первые 5 значений для отладки
        sample_items = list(data.items())[:5]
        for k, v in sample_items:
            logger.info(f"  Пример: {k} = {v}")
        
        # Если GPT вернул вложенную структуру (например, {"parameters": {...}})
        if len(data) == 1 and isinstance(list(data.values())[0], dict):
            nested_key = list(data.keys())[0]
            if nested_key not in code_to_def:
                logger.info(f"Распаковываем вложенный объект из ключа '{nested_key}'")
                data = data[nested_key]

        # Проверяем, есть ли уже записи для этого conversation_id,
        # чтобы не падать с UniqueViolation и не ломать транзакцию.
        existing_param_ids = set(
            row[0]
            for row in db.query(ParameterValue.parameter_id)
            .filter(ParameterValue.conversation_id == conversation_id)
            .all()
        )
        if existing_param_ids:
            logger.info(
                f"Параметры для conversation_id={conversation_id} уже существуют "
                f"({len(existing_param_ids)} записей) — пропускаю повторную вставку"
            )
            return

        saved = 0
        saved_with_value = 0
        saved_null = 0
        
        for code, pdef in code_to_def.items():
            entry = data.get(code)
            
            # Извлекаем значение и confidence
            if entry is None:
                val = None
                confidence = 0
            elif isinstance(entry, dict):
                val = entry.get("value")
                confidence = entry.get("confidence", 0) if val is None else entry.get("confidence", 80)
            else:
                val = entry
                confidence = 80

            pv = ParameterValue(
                conversation_id=conversation_id,
                parameter_id=pdef.id,
                confidence=confidence,
                created_at=call_date,
            )

            # Записываем значение (или null)
            if pdef.value_type == "number":
                pv.value_number = float(val) if val is not None else None
            elif pdef.value_type == "boolean":
                pv.value_bool = bool(val) if val is not None else None
            elif pdef.value_type == "text":
                pv.value_text = str(val) if val is not None else None

            db.add(pv)
            saved += 1
            if val is not None:
                saved_with_value += 1
            else:
                saved_null += 1

        db.commit()
        logger.info(f"Параметры записаны: {saved}/{len(code_to_def)} для conversation_id={conversation_id} ({saved_with_value} со значением, {saved_null} null)")

    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга JSON от GPT при извлечении параметров: {e}")
    except Exception as e:
        logger.error(f"Ошибка извлечения параметров для conversation_id={conversation_id}: {e}", exc_info=True)
    finally:
        if own_session:
            db.close()
