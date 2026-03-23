"""
Скрипт заполнения тестовых данных по всем 65 параметрам
для всех conversations участников команд.
Запуск: docker compose exec backend python scripts/fill_test_params.py
"""
import random
from datetime import datetime

from database import SessionLocal
from models import (
    ParameterDefinition, ParameterValue, Conversation,
    Team, TeamMember,
)
from sqlalchemy import exists

random.seed(42)

PARAM_RANGES = {
    "talk_listen_ratio": (25, 65),
    "avg_manager_reply_len": (8, 45),
    "avg_client_reply_len": (5, 35),
    "dialogue_density": (3, 15),
    "manager_questions_count": (2, 18),
    "objections_count": (0, 6),
    "greeting_quality": (30, 100),
    "client_engagement_start": (3, 25),
    "clarity_of_flow": (30, 100),
    "stage_completion_rate": (20, 100),
    "open_questions_ratio": (10, 90),
    "depth_of_questions": (15, 95),
    "needs_clarity": (20, 95),
    "client_engagement": (25, 95),
    "manager_interruptions": (0, 8),
    "listening_quality": (30, 100),
    "features_vs_benefits_ratio": (10, 90),
    "benefit_clarity": (20, 100),
    "client_questions": (0, 10),
    "clarity_of_explanation": (30, 100),
    "emotional_impact": (10, 90),
    "objection_handled": (0, 100),
    "handling_quality": (15, 95),
    "client_reaction": (20, 95),
    "closing_timing": (10, 100),
    "deal_momentum": (10, 95),
    "filler_words": (0, 15),
    "confidence": (30, 100),
    "speech_clarity": (40, 100),
    "client_ratio": (35, 75),
    "empathy": (20, 95),
    "value_quality": (20, 95),
}

TEXT_VALUES = {
    "questions_by_stage": [
        '{"начало": 2, "выявление": 5, "презентация": 1, "закрытие": 2}',
        '{"начало": 1, "выявление": 3, "презентация": 2, "закрытие": 1}',
        '{"начало": 3, "выявление": 6, "презентация": 3, "закрытие": 2}',
        '{"начало": 0, "выявление": 4, "презентация": 1, "закрытие": 0}',
    ],
    "conversation_control": ['"manager"', '"client"', '"balanced"', '"manager"', '"balanced"'],
    "objection_types": [
        '["дорого"]',
        '["подумаю", "не сейчас"]',
        '["дорого", "подумаю"]',
        '["уже есть решение"]',
        '["не интересно"]',
        '["дорого", "надо посоветоваться"]',
        '[]',
    ],
}


def gen_number(code: str) -> float:
    lo, hi = PARAM_RANGES.get(code, (10, 90))
    return round(random.uniform(lo, hi), 1)


def gen_bool(code: str) -> bool:
    weights = {
        "rapport_established": 0.65,
        "opening_clarity": 0.55,
        "permission_granted": 0.50,
        "trust_signal_start": 0.40,
        "resistance_at_start": 0.30,
        "structure_followed": 0.55,
        "stage_sequence_correct": 0.50,
        "chaos_flag": 0.20,
        "needs_identified": 0.60,
        "benefits_presented": 0.65,
        "benefit_personalization": 0.45,
        "value_linked_to_needs": 0.50,
        "client_interest_during_presentation": 0.55,
        "overload_of_information": 0.25,
        "value_confirmation": 0.45,
        "objection_ignored": 0.20,
        "defensive_behavior": 0.15,
        "objection_reframed": 0.40,
        "closing_attempt": 0.60,
        "next_step_defined": 0.55,
        "next_step_confirmed": 0.45,
        "client_commitment": 0.40,
        "urgency_created": 0.30,
        "over_talking": 0.25,
        "pressure_behavior": 0.15,
        "critical_error": 0.10,
        "strong_moment": 0.45,
        "system_identified": 0.50,
        "problem_identified": 0.60,
        "consequences_identified": 0.35,
        "price_devaluation": 0.20,
    }
    p = weights.get(code, 0.50)
    return random.random() < p


def gen_text(code: str) -> str:
    options = TEXT_VALUES.get(code, ['""'])
    return random.choice(options)


def main():
    db = SessionLocal()
    try:
        params = db.query(ParameterDefinition).filter(ParameterDefinition.is_active == True).order_by(ParameterDefinition.id).all()
        print(f"Параметров в справочнике: {len(params)}")

        all_member_ids = set()
        teams = db.query(Team).all()
        for t in teams:
            members = db.query(TeamMember).filter(TeamMember.team_id == t.id).all()
            for m in members:
                all_member_ids.add(m.user_id)
        print(f"Участники команд: {all_member_ids}")

        convs = (
            db.query(Conversation)
            .filter(Conversation.user_id.in_(all_member_ids))
            .order_by(Conversation.created_at.desc())
            .all()
        )
        print(f"Conversations для заполнения: {len(convs)}")

        inserted = 0
        skipped = 0

        for conv in convs:
            for param in params:
                existing = (
                    db.query(ParameterValue)
                    .filter(
                        ParameterValue.conversation_id == conv.id,
                        ParameterValue.parameter_id == param.id,
                    )
                    .first()
                )
                if existing:
                    skipped += 1
                    continue

                pv = ParameterValue(
                    conversation_id=conv.id,
                    parameter_id=param.id,
                    confidence=random.randint(60, 98),
                )

                if param.value_type == "number":
                    pv.value_number = gen_number(param.code)
                elif param.value_type == "boolean":
                    pv.value_bool = gen_bool(param.code)
                elif param.value_type == "text":
                    pv.value_text = gen_text(param.code)

                db.add(pv)
                inserted += 1

        db.commit()
        print(f"\nГотово! Вставлено: {inserted}, пропущено (уже были): {skipped}")
        print(f"Итого parameter_values: {db.query(ParameterValue).count()}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
