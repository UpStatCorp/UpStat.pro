"""
CurriculumService — создаёт AnalysisTrainingPlan из каталога тренировок
без реального звонка и без GPT.

Подход: создаём stub-Conversation + stub-Message (как в /test/create-training),
чтобы не нарушать NOT NULL constraint на analysis_training_plans.report_message_id.
plan_source="catalog" / "program" — маркер для фильтрации в full-дашборде.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from models import User

# ── Каталог этапов ─────────────────────────────────────────────────────────────
STAGES: list[dict] = [
    {
        "key": "contact",
        "label": "Вступление в контакт",
        "description": "Тренировка первого контакта с клиентом: приветствие, установка rapport.",
        "icon": "👋",
        "levels": 1,  # сколько stage_N.txt есть в каталоге
    },
    {
        "key": "needs",
        "label": "Работа с потребностями",
        "description": "Выявление потребностей клиента через открытые вопросы и активное слушание.",
        "icon": "🔍",
        "levels": 1,
    },
    {
        "key": "presentation",
        "label": "Презентация",
        "description": "Представление продукта с акцентом на выгоды и ценность для клиента.",
        "icon": "📊",
        "levels": 1,
    },
    {
        "key": "objections",
        "label": "Работа с возражениями",
        "description": "Обработка возражений клиента — цена, конкуренты, сроки.",
        "icon": "💡",
        "levels": 1,
    },
    {
        "key": "closing",
        "label": "Завершение сделки",
        "description": "Техники закрытия сделки: пробное закрытие, работа с нерешительностью.",
        "icon": "🤝",
        "levels": 4,  # stage_1..4 для closing
    },
]

_TRAININGS_DIR = Path(__file__).parent.parent / "static" / "docs" / "trainings"


def get_catalog() -> list[dict]:
    """Возвращает список доступных этапов для каталога."""
    catalog = []
    for stage in STAGES:
        available_levels = []
        for lvl in range(1, stage["levels"] + 1):
            prompt_path = _TRAININGS_DIR / stage["key"] / f"stage_{lvl}.txt"
            if prompt_path.exists():
                available_levels.append(lvl)
        catalog.append({**stage, "available_levels": available_levels})
    return catalog


def create_from_catalog(
    db: Session,
    user: "User",
    stage_key: str,
    level: int = 1,
    source: str = "catalog",
) -> tuple[int, int]:
    """
    Создаёт stub-тренировку из каталога.

    Returns:
        (plan_id, training_id) — для редиректа на /voice-training/training
    """
    from models import Conversation, Message, AnalysisTrainingPlan, Training

    # Проверяем промпт
    prompt_path = _TRAININGS_DIR / stage_key / f"stage_{level}.txt"
    if not prompt_path.exists():
        raise ValueError(f"Промпт не найден: {prompt_path}")

    stage_info = next((s for s in STAGES if s["key"] == stage_key), None)
    if not stage_info:
        raise ValueError(f"Неизвестный этап: {stage_key}")

    stage_label = stage_info["label"]

    # Stub-Conversation (без реального звонка)
    conv = Conversation(
        user_id=user.id,
        title=f"[Каталог] {stage_label}",
    )
    db.add(conv)
    db.flush()

    # Stub-Message (нужен для report_message_id NOT NULL)
    stub_msg = Message(
        conversation_id=conv.id,
        user_id=None,
        role="bot",
        text=f"[catalog-stub] stage={stage_key}",
    )
    db.add(stub_msg)
    db.flush()

    # AnalysisTrainingPlan
    plan = AnalysisTrainingPlan(
        user_id=user.id,
        report_message_id=stub_msg.id,
        title=stage_label,
        recommendations_json=json.dumps(
            [{"stage": stage_key, "source": source}],
            ensure_ascii=False,
        ),
        total_trainings=1,
        completed_trainings=0,
        status="active",
        plan_source=source,
    )
    db.add(plan)
    db.flush()

    # Training — одна тренировка.
    # stage=stage_key включает многоэтапный режим: если в каталоге несколько
    # stage_N.txt (как у "closing" — 4 этапа), они проходятся последовательно
    # в рамках ОДНОЙ тренировки, а не как отдельные «уровни».
    training = Training(
        plan_id=plan.id,
        order=1,
        title=stage_label,
        description=stage_info["description"],
        recommendation=stage_info["description"],
        scenario_type=stage_key,
        stage=stage_key,
        status="available",
    )
    db.add(training)
    db.flush()

    # Обновляем total_trainings
    plan.total_trainings = 1
    db.commit()

    return plan.id, training.id
