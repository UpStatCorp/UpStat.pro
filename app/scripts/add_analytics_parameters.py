#!/usr/bin/env python3
"""
Скрипт добавления 54 новых параметров аналитики в parameter_definitions.
Требует PostgreSQL с уже существующей таблицей (11 базовых параметров).
Запуск: python3 -m app.scripts.add_analytics_parameters
       или: cd app && python3 scripts/add_analytics_parameters.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from sqlalchemy import text
from sqlalchemy.orm import Session

try:
    from app.database import engine
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from database import engine

# 54 новых параметра (objections_count уже есть в базовых 11)
NEW_PARAMETERS = [
    # БЛОК 0: contact_establishment
    ("greeting_quality", "Качество приветствия", "Оценка качества приветствия 0-100", "number", "contact_establishment", "%"),
    ("rapport_established", "Открытое общение клиента установлено", "Клиент отвечает развёрнуто, нет отказа", "boolean", "contact_establishment", None),
    ("client_engagement_start", "Вовлечённость клиента в начале", "Длина ответа клиента в начале звонка (слов)", "number", "contact_establishment", "слов"),
    ("opening_clarity", "Ясность цели звонка", "Есть цель звонка и объяснение причины", "boolean", "contact_establishment", None),
    ("permission_granted", "Разрешение на разговор", "Клиент дал разрешение («да, давайте», «можно»)", "boolean", "contact_establishment", None),
    ("trust_signal_start", "Сигнал доверия в начале", "Признаки доверия и вовлечённости в начале", "boolean", "contact_establishment", None),
    ("resistance_at_start", "Сопротивление в начале", "Клиент выразил сопротивление («мне не интересно», «не сейчас»)", "boolean", "contact_establishment", None),
    # БЛОК 1: structure
    ("structure_followed", "Соблюдена структура", "Соблюдена последовательность блоков разговора", "boolean", "structure", None),
    ("stage_sequence_correct", "Правильная последовательность этапов", "Этапы идут в правильном порядке", "boolean", "structure", None),
    ("conversation_control", "Контроль разговора", "Кто ведёт: manager / client / balanced", "text", "structure", None),
    ("clarity_of_flow", "Ясность хода разговора", "Оценка ясности 0-100", "number", "structure", "%"),
    ("stage_completion_rate", "Процент завершённых этапов", "Доля завершённых этапов 0-100", "number", "structure", "%"),
    ("chaos_flag", "Хаос в разговоре", "Признаки хаотичности разговора", "boolean", "structure", None),
    # БЛОК 2: needs_discovery
    ("open_questions_ratio", "Доля открытых вопросов", "Процент открытых вопросов (как/почему/что) 0-100", "number", "needs_discovery", "%"),
    ("depth_of_questions", "Глубина вопросов", "Оценка глубины вопросов 0-100", "number", "needs_discovery", "%"),
    ("needs_identified", "Потребности выявлены", "Менеджер выявил потребности клиента", "boolean", "needs_discovery", None),
    ("needs_clarity", "Чёткость понимания потребностей", "Оценка чёткости 0-100", "number", "needs_discovery", "%"),
    ("client_engagement", "Вовлечённость клиента", "Оценка вовлечённости клиента 0-100", "number", "needs_discovery", "%"),
    ("manager_interruptions", "Перебивания менеджером", "Количество перебиваний клиента менеджером", "number", "needs_discovery", "шт"),
    ("listening_quality", "Качество слушания", "Оценка качества слушания 0-100", "number", "needs_discovery", "%"),
    # БЛОК 3: presentation
    ("features_vs_benefits_ratio", "Соотношение функции/выгоды", "Доля выгод в презентации 0-100", "number", "presentation", "%"),
    ("benefits_presented", "Выгоды представлены", "Менеджер представил выгоды", "boolean", "presentation", None),
    ("benefit_clarity", "Ясность выгод", "Оценка ясности выгод 0-100", "number", "presentation", "%"),
    ("benefit_personalization", "Персонализация выгод", "Выгоды адаптированы под клиента", "boolean", "presentation", None),
    ("value_linked_to_needs", "Связка ценности с потребностями", "Ценность увязана с потребностями клиента", "boolean", "presentation", None),
    ("client_interest_during_presentation", "Интерес клиента при презентации", "Клиент проявлял интерес во время презентации", "boolean", "presentation", None),
    ("client_questions", "Вопросы клиента", "Количество вопросов клиента", "number", "presentation", "шт"),
    ("overload_of_information", "Перегруз информацией", "Слишком много информации за раз", "boolean", "presentation", None),
    ("clarity_of_explanation", "Ясность объяснения", "Оценка ясности 0-100", "number", "presentation", "%"),
    ("emotional_impact", "Эмоциональный эффект", "Оценка эмоционального эффекта 0-100", "number", "presentation", "%"),
    ("value_confirmation", "Подтверждение ценности", "Клиент подтвердил ценность предложения", "boolean", "presentation", None),
    # БЛОК 4: objections
    ("objection_types", "Типы возражений", 'JSON: ["дорого","подумаю","не сейчас"] и др.', "text", "objections", None),
    ("objection_handled", "Процент обработанных возражений", "Доля обработанных возражений 0-100", "number", "objections", "%"),
    ("handling_quality", "Качество обработки", "Оценка качества обработки возражений 0-100", "number", "objections", "%"),
    ("objection_ignored", "Возражение проигнорировано", "Менеджер проигнорировал возражение", "boolean", "objections", None),
    ("defensive_behavior", "Защитное поведение", "Менеджер вёл себя защищаясь", "boolean", "objections", None),
    ("objection_reframed", "Возражение переформулировано", "Возражение было переформулировано", "boolean", "objections", None),
    ("client_reaction", "Реакция клиента", "Оценка реакции клиента после ответа 0-100", "number", "objections", "%"),
    # БЛОК 5: closing
    ("closing_attempt", "Попытка закрытия", "Была попытка закрыть сделку", "boolean", "closing", None),
    ("closing_timing", "Тайминг закрытия", "Оценка тайминга закрытия 0-100", "number", "closing", "%"),
    ("next_step_defined", "Следующий шаг определён", "Определён следующий шаг", "boolean", "closing", None),
    ("next_step_confirmed", "Следующий шаг подтверждён", "Следующий шаг подтверждён клиентом", "boolean", "closing", None),
    ("client_commitment", "Обязательства клиента", "Клиент взял обязательства", "boolean", "closing", None),
    ("urgency_created", "Срочность создана", "Создана срочность принятия решения", "boolean", "closing", None),
    ("deal_momentum", "Импульс сделки", "Оценка импульса сделки 0-100", "number", "closing", "%"),
    # БЛОК 6: general_behavior
    ("filler_words", "Слова-паразиты", "Количество слов-паразитов", "number", "general_behavior", "шт"),
    ("confidence", "Уверенность", "Оценка уверенности менеджера 0-100", "number", "general_behavior", "%"),
    ("speech_clarity", "Чёткость речи", "Оценка чёткости речи 0-100", "number", "general_behavior", "%"),
    ("over_talking", "Чрезмерная речь", "Менеджер говорил слишком много", "boolean", "general_behavior", None),
    ("pressure_behavior", "Давление на клиента", "Признаки давления на клиента", "boolean", "general_behavior", None),
    ("empathy", "Взаимопонимание", "Оценка взаимопонимания 0-100", "number", "general_behavior", "%"),
    ("value_quality", "Качество ценности", "Оценка качества передачи ценности 0-100", "number", "general_behavior", "%"),
    ("critical_error", "Критическая ошибка", "Допущена критическая ошибка", "boolean", "general_behavior", None),
    ("strong_moment", "Сильный момент", "Был сильный момент в разговоре", "boolean", "general_behavior", None),
]


def main():
    with Session(engine) as db:
        existing = {row[0] for row in db.execute(text("SELECT code FROM parameter_definitions")).fetchall()}
        added = 0
        skipped = 0

        for code, title, description, value_type, category, unit in NEW_PARAMETERS:
            if code in existing:
                print(f"  Пропуск (уже есть): {code}")
                skipped += 1
                continue

            db.execute(text("""
                INSERT INTO parameter_definitions (code, title, description, value_type, category, unit)
                VALUES (:code, :title, :description, :value_type, :category, :unit)
                ON CONFLICT (code) DO NOTHING
            """), {
                "code": code,
                "title": title,
                "description": description,
                "value_type": value_type,
                "category": category,
                "unit": unit,
            })
            print(f"  + {code}")
            added += 1

        db.commit()
        total = db.execute(text("SELECT COUNT(*) FROM parameter_definitions")).scalar()
        print(f"\nГотово: добавлено {added}, пропущено {skipped}. Всего в справочнике: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
