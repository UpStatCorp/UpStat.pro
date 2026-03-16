"""Сервис для работы со скриптами команд"""
import json
import re
import logging
from typing import Dict, Any, Optional
from pathlib import Path
from docx import Document
from openai import OpenAI
import os

logger = logging.getLogger(__name__)

# Инициализация OpenAI клиента
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))


def extract_text_from_word(file_path: Path) -> str:
    """Извлекает текст из Word документа"""
    try:
        doc = Document(file_path)
        text_parts = []
        for paragraph in doc.paragraphs:
            if paragraph.text.strip():
                text_parts.append(paragraph.text.strip())
        return "\n".join(text_parts)
    except Exception as e:
        logger.error(f"Ошибка чтения Word документа: {e}", exc_info=True)
        raise ValueError(f"Не удалось прочитать Word документ: {str(e)}")


def parse_text_to_checklist_format(text: str) -> Dict[str, Any]:
    """
    Парсит текст в формат чеклиста (JSON).
    Пытается извлечь структуру: блоки с заголовками и критерии.
    """
    # Очищаем текст
    text = text.strip()
    if not text:
        raise ValueError("Текст скрипта пуст")
    
    # Пытаемся найти структуру с блоками и критериями
    blocks = []
    
    # Паттерны для поиска блоков (заголовки БЛОК, раздел, часть и т.д.)
    block_patterns = [
        r'(?:БЛОК|Блок|РАЗДЕЛ|Раздел|ЧАСТЬ|Часть|ЭТАП|Этап)\s*\d+[:\-]?\s*(.+?)(?=\n(?:БЛОК|Блок|РАЗДЕЛ|Раздел|ЧАСТЬ|Часть|ЭТАП|Этап|\Z))',
        r'^\d+[\.\)]\s*(.+?)(?=\n\d+[\.\)]|\Z)',
    ]
    
    # Сначала пытаемся найти блоки
    found_blocks = False
    for pattern in block_patterns:
        matches = re.finditer(pattern, text, re.MULTILINE | re.DOTALL | re.IGNORECASE)
        for match in matches:
            found_blocks = True
            block_content = match.group(1).strip()
            # Извлекаем заголовок (первая строка)
            lines = block_content.split('\n')
            title = lines[0].strip()
            # Остальное - критерии
            criteria_text = '\n'.join(lines[1:]).strip()
            criteria = []
            # Разбиваем на отдельные критерии (по номерам, дефисам, маркерам)
            for crit_line in re.split(r'\n(?:\d+[\.\)]\s*|[-•]\s*)', criteria_text):
                crit_line = crit_line.strip()
                if crit_line and len(crit_line) > 10:  # Минимальная длина критерия
                    criteria.append(crit_line)
            
            if title and criteria:
                blocks.append({
                    "title": title,
                    "criteria": criteria
                })
    
    # Если не нашли блоки, пытаемся разбить по строкам
    if not found_blocks:
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        if len(lines) > 0:
            # Первая строка - заголовок
            title = lines[0]
            # Остальные - критерии
            criteria = [l for l in lines[1:] if len(l) > 10]
            if criteria:
                blocks.append({
                    "title": title,
                    "criteria": criteria
                })
    
    # Если всё ещё ничего не нашли, используем GPT для парсинга
    if not blocks:
        return parse_text_with_gpt(text)
    
    # Формируем JSON в формате чеклиста
    checklist_data = {
        "id": "team_script",
        "title": "Скрипт команды",
        "description": "Скрипт продаж команды",
        "blocks": blocks
    }
    
    return checklist_data


def parse_text_with_gpt(text: str) -> Dict[str, Any]:
    """Использует GPT для парсинга текста в формат чеклиста"""
    if not client.api_key:
        raise ValueError("OPENAI_API_KEY не настроен")
    
    prompt = f"""Преобразуй следующий текст скрипта продаж в JSON формат чеклиста.

Текст скрипта:
{text[:3000]}

Верни ТОЛЬКО валидный JSON в следующем формате (без комментариев, без markdown):
{{
  "id": "team_script",
  "title": "Название скрипта",
  "description": "Описание скрипта",
  "blocks": [
    {{
      "title": "Название блока",
      "criteria": [
        "Критерий 1",
        "Критерий 2"
      ]
    }}
  ]
}}

Разбей текст на логические блоки (этапы продажи, разделы скрипта) и выдели критерии для каждого блока.
Только JSON, без дополнительного текста."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.2,
            timeout=30.0
        )
        
        result = json.loads(response.choices[0].message.content)
        
        # Валидация структуры
        if "blocks" not in result:
            raise ValueError("GPT вернул неверный формат: отсутствует поле 'blocks'")
        
        # Убеждаемся, что есть id и title
        if "id" not in result:
            result["id"] = "team_script"
        if "title" not in result:
            result["title"] = "Скрипт команды"
        
        return result
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга JSON от GPT: {e}")
        raise ValueError(f"Не удалось преобразовать текст в формат чеклиста: {str(e)}")
    except Exception as e:
        logger.error(f"Ошибка при использовании GPT для парсинга: {e}", exc_info=True)
        raise ValueError(f"Ошибка обработки скрипта: {str(e)}")


def convert_to_checklist_format(text: str, is_word_file: bool = False, word_file_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Конвертирует текст или Word файл в формат чеклиста (JSON)
    
    Args:
        text: Текст скрипта (если не Word файл)
        is_word_file: True если это Word файл
        word_file_path: Путь к Word файлу (если is_word_file=True)
    
    Returns:
        Dict в формате чеклиста
    """
    if is_word_file and word_file_path:
        text = extract_text_from_word(word_file_path)
    
    if not text or not text.strip():
        raise ValueError("Текст скрипта пуст")
    
    # Пытаемся парсить вручную
    try:
        checklist = parse_text_to_checklist_format(text)
        return checklist
    except Exception as e:
        logger.warning(f"Не удалось распарсить вручную: {e}, используем GPT")
        # Если не получилось, используем GPT
        return parse_text_with_gpt(text)

