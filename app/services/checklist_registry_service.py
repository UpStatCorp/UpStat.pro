"""
Сервис синхронизации справочника пунктов чеклистов (checklist_item_definitions).
Парсит JSON-файлы из checklists/ и заполняет БД с весами для расчёта Win Probability.
"""

import json
import logging
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from models import ChecklistItemDefinition

logger = logging.getLogger("main")

# Веса по чеклистам и блокам.
# critical=3.0: напрямую влияет на закрытие сделки
# important=2.0: существенно влияет на качество продажи
# basic=1.0: базовые навыки, косвенное влияние
WEIGHT_MAP: dict[str, dict[str, float]] = {
    "deal_closing_and_doubts": {
        "b0": 3.0,  # Фокус на идеях «за»
        "b1": 3.0,  # Уверенное завершение сделки
        "b2": 3.0,  # Продажа цены
        "b3": 3.0,  # Продажа условий
        "b4": 2.0,  # Выбор без выбора
        "b5": 1.0,  # Продажа игрой
        "b6": 1.0,  # Дополнительный инструмент
    },
    "objection_handling_part_1": {
        "b0": 2.0,  # Отношение к возражениям
        "b1": 2.0,  # Подтверждение
        "b2": 2.0,  # Цикл общения
        "b3": 3.0,  # Аргументация
        "b4": 2.0,  # Поддержание взаимопонимания
        "b5": 1.0,  # Восприятие клиентом диалога
        "b6": 2.0,  # Общая эффективность
    },
    "objection_handling_understanding": {
        "b0": 2.0,  # Поддержание взаимопонимания
        "b1": 2.0,  # Уверенность и намерение
        "b2": 2.0,  # Понимание глубинных возражений
        "b3": 3.0,  # Проникновение под возражение
        "b4": 2.0,  # Выявление невысказанных возражений
    },
    "selling_presentation": {
        "b0": 2.0,  # Показ будущей ценности
        "b1": 2.0,  # Уровень понятности
        "b2": 2.0,  # Подготовка через потребности
    },
    "needs_work": {
        "b0": 2.0,  # Формирование интереса
        "b1": 2.0,  # Переход к презентации
    },
    "contact_establishment": {
        "b0": 1.0,  # Заметить причину человека
        "b1": 1.0,  # Пауза после замечания
    },
}


def _get_weight(checklist_id: str, block_idx: int) -> float:
    """Возвращает вес для пункта на основе чеклиста и блока."""
    checklist_weights = WEIGHT_MAP.get(checklist_id, {})
    return checklist_weights.get(f"b{block_idx}", 1.0)


def sync_checklist_definitions(db: Session, checklists_dir: Optional[str] = None) -> int:
    """
    Синхронизирует справочник checklist_item_definitions из JSON-файлов.
    Upsert по item_code: обновляет существующие, создаёт новые.

    Returns:
        Количество пунктов в справочнике после синхронизации.
    """
    if checklists_dir is None:
        checklists_dir = str(Path("checklists"))

    check_dir = Path(checklists_dir)
    if not check_dir.exists():
        logger.warning(f"Директория чеклистов не найдена: {checklists_dir}")
        return 0

    json_files = sorted(check_dir.glob("*.json"))
    if not json_files:
        logger.warning(f"JSON-файлы чеклистов не найдены в {checklists_dir}")
        return 0

    synced_count = 0

    for cf in json_files:
        try:
            data = json.loads(cf.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error(f"Ошибка чтения чеклиста {cf.name}: {e}")
            continue

        checklist_id = data.get("id", cf.stem)
        checklist_title = data.get("title", cf.stem)

        for block_idx, block in enumerate(data.get("blocks", [])):
            block_title = block.get("title", f"Блок {block_idx + 1}")
            weight = _get_weight(checklist_id, block_idx)

            for criteria_idx, criterion_text in enumerate(block.get("criteria", [])):
                item_code = f"{checklist_id}.b{block_idx}.c{criteria_idx}"

                existing = db.query(ChecklistItemDefinition).filter(
                    ChecklistItemDefinition.item_code == item_code
                ).first()

                if existing:
                    existing.checklist_id = checklist_id
                    existing.checklist_title = checklist_title
                    existing.block_title = block_title
                    existing.block_order = block_idx
                    existing.item_order = criteria_idx
                    existing.item_text = criterion_text
                    existing.weight = weight
                else:
                    new_item = ChecklistItemDefinition(
                        checklist_id=checklist_id,
                        checklist_title=checklist_title,
                        block_title=block_title,
                        block_order=block_idx,
                        item_order=criteria_idx,
                        item_text=criterion_text,
                        item_code=item_code,
                        weight=weight,
                        is_active=True,
                    )
                    db.add(new_item)

                synced_count += 1

    db.commit()
    logger.info(f"Справочник чеклистов синхронизирован: {synced_count} пунктов")
    return synced_count
