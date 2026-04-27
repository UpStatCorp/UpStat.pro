"""
Сервис многоэтапных голосовых тренировок.

Загружает промпты этапов из файлов, отслеживает текущий этап,
определяет роль ИИ в каждом этапе и обрабатывает теги переходов
[STAGE_COMPLETE] / [TRAINING_COMPLETE], которые ИИ добавляет в конце своих реплик.

Структура файлов:
    app/static/docs/trainings/<stage_name>/stage_<N>.txt

где <stage_name> — это значение Training.stage (например "closing", "contact"),
а N — порядковый номер этапа внутри тренировки (1, 2, 3, 4, ...).

Если для данного `stage_name` папка/файлы отсутствуют — система работает
в обычном (одно-этапном) режиме как раньше.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


STAGE_COMPLETE_TAG = "[STAGE_COMPLETE]"
TRAINING_COMPLETE_TAG = "[TRAINING_COMPLETE]"

_TAG_REGEX = re.compile(r"\[(?:STAGE_COMPLETE|TRAINING_COMPLETE)\]", re.IGNORECASE)

# Имена tool-функций, которые ИИ может вызывать для перехода между этапами.
# Используются вместо текстовых тегов — tools — это отдельный канал Azure Voice Live,
# вызовы через него НЕ озвучиваются голосом (в отличие от текста в реплике).
TOOL_COMPLETE_STAGE = "complete_stage"
TOOL_COMPLETE_TRAINING = "complete_training"


def build_stage_tools() -> list:
    """
    Возвращает определения tool-функций для Azure Voice Live.
    
    Эти инструменты ИИ вызывает через скрытый канал — они НЕ озвучиваются голосом.
    Используются в многоэтапных тренировках чтобы ИИ мог подать сигнал серверу
    о завершении этапа/тренировки, не произнося технические теги вслух.
    """
    return [
        {
            "type": "function",
            "name": TOOL_COMPLETE_STAGE,
            "description": (
                "Вызови эту функцию когда ты ПОЛНОСТЬЮ завершил все шаги текущего "
                "этапа тренировки (последний шаг выполнен, пользователь согласился "
                "с результатом). Функция невидимо сигнализирует системе, чтобы она "
                "переключила тренировку на следующий этап. Вызывай ТОЛЬКО один раз "
                "в самом конце этапа — НЕ раньше времени!"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
        {
            "type": "function",
            "name": TOOL_COMPLETE_TRAINING,
            "description": (
                "Вызови эту функцию ТОЛЬКО когда ты полностью завершил ВСЮ тренировку "
                "(прошли все этапы, подведён общий итог). Используй её только в "
                "самом последнем этапе тренировки. Функция невидимо сигнализирует "
                "системе что всю тренировку можно закрыть."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    ]


def _trainings_root() -> Path:
    """Возвращает корневую папку с промптами этапов тренировок."""
    return Path(__file__).resolve().parent.parent / "static" / "docs" / "trainings"


@dataclass
class TrainingStage:
    """Описание одного этапа тренировки."""
    number: int
    prompt: str
    ai_role: str
    ai_role_description: str
    training_type: str
    # Шаблон ПЕРВОЙ реплики этапа — передаётся в триггер response.create
    # при переходе, чтобы ИИ сразу выдал согласованную с промптом вступительную
    # фразу, а не импровизировал. Содержит несколько примеров через "ИЛИ".
    first_line_template: str = ""


def _parse_stage_metadata(prompt: str, number: int) -> TrainingStage:
    """
    Достаёт мета-информацию об этапе (роль ИИ, тип тренировки) из текста промпта.

    Промпт должен содержать блок:
        ИНФОРМАЦИЯ ОБ ЭТАПЕ:
        * Номер этапа: ...
        * Роль ИИ: ...
        * Тип тренировки: ...
        * Роль пользователя (напарника): ...
    """
    ai_role = "Тренер"
    training_type = ""

    role_match = re.search(r"Роль\s+ИИ\s*:\s*(.+)", prompt)
    if role_match:
        ai_role = role_match.group(1).strip().split("\n")[0].strip()

    type_match = re.search(r"Тип\s+тренировки\s*:\s*(.+)", prompt)
    if type_match:
        training_type = type_match.group(1).strip().split("\n")[0].strip()

    role_short = ai_role.split("(")[0].strip()
    if "клиент" in role_short.lower():
        role_short = "Клиент"
    elif "менеджер" in role_short.lower():
        role_short = "Менеджер по продажам"

    description_parts = []
    if training_type:
        description_parts.append(training_type)
    description = " · ".join(description_parts)

    return TrainingStage(
        number=number,
        prompt=prompt,
        ai_role=role_short,
        ai_role_description=description,
        training_type=training_type,
    )


# Шаблоны первых реплик для каждого этапа тренировки.
# Ключ: (stage_name, stage_number).
# Используются в триггере response.create при переключении этапов,
# чтобы ИИ не импровизировал вступление и не дублировал его.
_FIRST_LINE_TEMPLATES = {
    ("closing", 1): (
        "Начни ЭТАП 1 (неправильный вариант завершения сделки). "
        "Твоя первая реплика: короткое живое приветствие + кратко (1 фразой) "
        "объясни что сейчас будем делать + предложи клиенту (пользователю) оформить покупку. "
        "Пример своими словами: 'Привет! Сейчас попробуем неправильный способ завершения сделки — "
        "я буду менеджером, ты клиентом. Ну что, оформляем заказ?' "
        "Всё в одной реплике! После этого жди ответ пользователя."
    ),
    ("closing", 2): (
        "Начни ЭТАП 2 (правильный вариант, ты всё ещё менеджер). "
        "Твоя первая реплика должна быть ОДНО сообщение из ДВУХ частей: "
        "(а) короткая связка-переход из прошлого этапа — например 'Так, теперь правильный вариант' "
        "(БЕЗ объявлений 'я менеджер, ты клиент' — пользователь и так это знает); "
        "(б) СРАЗУ предложение оформить: 'Ну что, оформляем?' / 'Давайте тогда заказ?' / 'Готовы оформлять?'. "
        "Пример целиком: 'Так, пробуем теперь правильный подход. Ну что, оформляем заказ?' "
        "После этой реплики жди что скажет пользователь (клиент)."
    ),
    ("closing", 3): (
        "Начни ЭТАП 3 (смена ролей, ты теперь КЛИЕНТ, пользователь — МЕНЕДЖЕР). "
        "Твоя первая реплика — ОДНО сообщение из ДВУХ частей: "
        "(а) короткая фраза про смену ролей: 'Так, теперь меняемся — ты менеджер, я клиент'; "
        "(б) СРАЗУ прямое приглашение пользователю начать: 'Давай, предложи мне оформить — "
        "я отвечу как клиент с сомнениями'. "
        "Пример целиком: 'Так, теперь меняемся ролями — ты менеджер, я клиент. "
        "Давай, предложи мне оформить, а я буду как сомневающийся покупатель.' "
        "После этой реплики жди что скажет пользователь (он должен предложить оформить). "
        "⚠️ НЕ задавай свои вопросы о доставке/товаре — просто ЖДИ пока он предложит."
    ),
    ("closing", 4): (
        "Начни ЭТАП 4 (финальный, правильный вариант, ты всё ещё КЛИЕНТ). "
        "Твоя первая реплика — ОДНО сообщение из ДВУХ частей: "
        "(а) короткая связка: 'Так, теперь правильный вариант'; "
        "(б) СРАЗУ прямое приглашение пользователю начать: 'Давай, снова предложи мне оформить — "
        "а я буду реагировать как клиент'. "
        "Пример целиком: 'Окей, теперь правильный вариант. Ты всё ещё менеджер, я клиент. "
        "Давай, предложи мне оформить — а я отреагирую как сомневающийся покупатель.' "
        "После этой реплики жди что скажет пользователь. "
        "⚠️ Помни: ты КЛИЕНТ, не придумывай что ты 'задал вопрос о доставке' — этого не было!"
    ),
}


def _get_first_line_template(stage_name: str, number: int) -> str:
    """Возвращает шаблон первой реплики для указанного этапа или пустую строку."""
    return _FIRST_LINE_TEMPLATES.get((stage_name, number), "")


def load_stages(stage_name: Optional[str]) -> List[TrainingStage]:
    """
    Загружает все этапы тренировки для указанного `stage_name`.

    Args:
        stage_name: имя этапа продаж из Training.stage (например, "closing").

    Returns:
        Список TrainingStage в порядке возрастания номера этапа,
        либо пустой список если многоэтапная конфигурация для этого
        stage_name отсутствует.
    """
    if not stage_name:
        return []

    folder = _trainings_root() / stage_name
    if not folder.exists() or not folder.is_dir():
        return []

    files = sorted(
        folder.glob("stage_*.txt"),
        key=lambda p: int(re.search(r"stage_(\d+)", p.stem).group(1))
        if re.search(r"stage_(\d+)", p.stem)
        else 0,
    )

    stages: List[TrainingStage] = []
    for path in files:
        match = re.search(r"stage_(\d+)", path.stem)
        if not match:
            continue
        number = int(match.group(1))
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.error(f"Не удалось прочитать промпт этапа {path}: {exc}")
            continue
        stage = _parse_stage_metadata(content, number)
        stage.first_line_template = _get_first_line_template(stage_name, number)
        stages.append(stage)

    if stages:
        logger.info(
            f"📚 Загружено {len(stages)} этапов для тренировки '{stage_name}': "
            + ", ".join(f"#{s.number} ({s.ai_role})" for s in stages)
        )

    return stages


def strip_tags(text: str) -> str:
    """Убирает технические теги [STAGE_COMPLETE]/[TRAINING_COMPLETE] из текста."""
    if not text:
        return text
    cleaned = _TAG_REGEX.sub("", text)
    return re.sub(r"[ \t]+\n", "\n", cleaned).strip()


def has_stage_complete(text: str) -> bool:
    """Проверяет, содержит ли текст тег завершения этапа."""
    return STAGE_COMPLETE_TAG.lower() in (text or "").lower()


def has_training_complete(text: str) -> bool:
    """Проверяет, содержит ли текст тег завершения всей тренировки."""
    return TRAINING_COMPLETE_TAG.lower() in (text or "").lower()
