"""
Сервис извлечения 11 структурированных параметров из транскрипта звонка.
Работает последовательно ПОСЛЕ основного анализа pipeline.
"""

import json
import asyncio
import logging
from typing import Optional

from openai import OpenAI
from sqlalchemy.orm import Session

from models import ParameterDefinition, ParameterValue
from database import SessionLocal

import os
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("main")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
_client = OpenAI(api_key=OPENAI_API_KEY)

EXTRACTION_PROMPT = """Ты — аналитик телефонных продаж. Проанализируй транскрипт звонка и извлеки 11 параметров.

ПАРАМЕТРЫ:
1. talk_listen_ratio — Процент времени речи менеджера (число 0–100). Если менеджер говорит 70% времени → 70.
2. avg_manager_reply_len — Среднее кол-во слов в одной реплике менеджера (число).
3. avg_client_reply_len — Среднее кол-во слов в одной реплике клиента (число).
4. dialogue_density — Кол-во смен реплик (ролей) за минуту разговора (число). Посчитай общее число реплик / примерную длительность разговора в минутах.
5. manager_questions_count — Сколько вопросов задал менеджер (число).
6. questions_by_stage — JSON-объект: распределение вопросов менеджера по этапам. Этапы: "greeting", "needs_discovery", "presentation", "objection_handling", "closing". Пример: {"greeting": 1, "needs_discovery": 5, "presentation": 2, "objection_handling": 1, "closing": 1}
7. system_identified — Выявил ли менеджер текущую систему/процесс клиента (true/false).
8. problem_identified — Выявил ли менеджер проблему/боль клиента (true/false).
9. consequences_identified — Обсудил ли менеджер последствия нерешённой проблемы (true/false).
10. price_devaluation — Обесценивал ли менеджер собственный продукт/цену, давал необоснованные скидки (true/false).
11. objections_count — Сколько возражений высказал клиент (число).

ПРАВИЛА:
- Верни строго JSON-объект с ключами = кодам параметров.
- Для числовых параметров — число (int или float).
- Для boolean — true или false.
- Для questions_by_stage — JSON-объект строкой.
- Добавь поле "confidence" (0-100) для каждого параметра — насколько ты уверен в значении.
- Если параметр невозможно определить из текста — поставь null.

ФОРМАТ ОТВЕТА (строго JSON, без markdown):
{
  "talk_listen_ratio": {"value": 65, "confidence": 80},
  "avg_manager_reply_len": {"value": 18, "confidence": 85},
  "avg_client_reply_len": {"value": 12, "confidence": 85},
  "dialogue_density": {"value": 6.5, "confidence": 70},
  "manager_questions_count": {"value": 8, "confidence": 90},
  "questions_by_stage": {"value": "{\\"greeting\\": 1, \\"needs_discovery\\": 4, \\"presentation\\": 2, \\"objection_handling\\": 0, \\"closing\\": 1}", "confidence": 75},
  "system_identified": {"value": true, "confidence": 85},
  "problem_identified": {"value": true, "confidence": 90},
  "consequences_identified": {"value": false, "confidence": 80},
  "price_devaluation": {"value": false, "confidence": 95},
  "objections_count": {"value": 3, "confidence": 85}
}

ТРАНСКРИПТ:
"""


async def extract_parameters(conversation_id: int, dialogue_json_str: str, db: Optional[Session] = None):
    """
    Извлекает 11 параметров из транскрипта и сохраняет в parameter_values.
    Вызывается последовательно после основного анализа pipeline.
    """
    own_session = db is None
    if own_session:
        db = SessionLocal()

    try:
        param_defs = db.query(ParameterDefinition).filter(
            ParameterDefinition.is_active == True
        ).all()
        if not param_defs:
            logger.warning("Справочник параметров пуст — пропускаю извлечение")
            return

        code_to_def = {p.code: p for p in param_defs}

        prompt = EXTRACTION_PROMPT + dialogue_json_str

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
