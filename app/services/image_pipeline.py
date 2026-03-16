"""
Пайплайн для анализа скриншотов переписок.

Поток: скриншоты → GPT-4o Vision (OCR + определение ролей + порядок) → текстовый транскрипт → 
       существующий пайплайн анализа по чек-листам.
"""

import os
import json
import asyncio
import base64
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

from openai import OpenAI
from sqlalchemy.orm import Session

from models import Message, Attachment
from database import SessionLocal
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("main")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
UPLOAD_DIR = os.path.abspath("uploads")
client = OpenAI(api_key=OPENAI_API_KEY)


# ========== Утилиты ==========

def _encode_image_to_base64(image_path: Path) -> str:
    """Кодирует изображение в base64 для отправки в GPT-4o Vision."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _get_mime_type(image_path: Path) -> str:
    """Определяет MIME тип по расширению."""
    ext = image_path.suffix.lower()
    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }
    return mime_map.get(ext, "image/png")


def _send_bot_message(db: Session, conversation_id: int, text: str):
    """Отправляет сообщение от бота в диалог."""
    msg = Message(
        conversation_id=conversation_id,
        user_id=None,
        role="bot",
        text=text,
    )
    db.add(msg)
    db.commit()


# ========== Промпт для извлечения диалога ==========

EXTRACT_DIALOGUE_PROMPT = """Ты — эксперт по распознаванию переписок из мессенджеров и CRM-систем.

Перед тобой скриншот(ы) переписки менеджера с клиентом.

Твоя задача:
1. **Распознай весь текст** на скриншоте(ах).
2. **Определи, кто менеджер, а кто клиент** по следующим КЛЮЧЕВЫМ правилам:
   - ⚠️ ГЛАВНОЕ ПРАВИЛО: сообщения СПРАВА (зелёные/синие/цветные пузыри, исходящие) — это ВСЕГДА МЕНЕДЖЕР (тот, кто делал скриншот со своего телефона).
   - Сообщения СЛЕВА (серые/белые пузыри, входящие) — это ВСЕГДА КЛИЕНТ.
   - Это правило приоритетнее содержания сообщений! Скриншот делает менеджер со своего устройства, поэтому его сообщения всегда справа.
   - Дополнительно ориентируйся по содержанию: кто предлагает услугу/товар, кто спрашивает — но расположение пузырей важнее.
3. **Восстанови хронологический порядок** сообщений (сверху вниз = от ранних к поздним).
4. **Если несколько скриншотов** — объедини их в единый диалог, убрав дубликаты.

Верни результат СТРОГО в JSON формате:
{
  "messenger": "WhatsApp / Telegram / SMS / Instagram / Avito / другой",
  "manager_name": "имя менеджера (если видно)",
  "client_name": "имя клиента (если видно)",
  "dialogue": [
    {
      "role": "manager" или "client",
      "text": "текст сообщения",
      "time": "время если видно, иначе null"
    }
  ],
  "notes": "любые заметки: пропущенные сообщения, нечитаемые части, контекст"
}

Важно:
- Если не можешь точно определить роль — укажи наиболее вероятную с пометкой в notes.
- Сохрани эмодзи, ссылки, форматирование как есть.
- Если на скриншоте видны системные сообщения (даты, статусы) — включи их в notes.
- Только JSON, без комментариев вне JSON."""


# ========== Этап 1: Извлечение диалога из скриншотов ==========

async def extract_dialogue_from_images(image_paths: List[Path]) -> Dict[str, Any]:
    """
    Отправляет скриншоты в GPT-4o Vision и извлекает структурированный диалог.
    Поддерживает до 20 изображений в одном запросе.
    """
    content: List[Dict[str, Any]] = [{"type": "text", "text": EXTRACT_DIALOGUE_PROMPT}]

    for img_path in image_paths:
        base64_image = _encode_image_to_base64(img_path)
        mime_type = _get_mime_type(img_path)
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime_type};base64,{base64_image}",
                "detail": "high"  # Максимальное качество распознавания
            }
        })

    logger.info(f"🖼️ Отправляю {len(image_paths)} скриншот(ов) в GPT-4o Vision для распознавания")

    resp = await asyncio.to_thread(
        lambda: client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": content}],
            temperature=0.1,
            max_tokens=4096,
            response_format={"type": "json_object"},
        )
    )

    raw = resp.choices[0].message.content.strip()
    logger.info(f"✅ Получен ответ от GPT-4o Vision ({len(raw)} символов)")
    
    return json.loads(raw)


# ========== Этап 2: Конвертация диалога в текстовый транскрипт ==========

def dialogue_to_transcript(dialogue_data: Dict[str, Any]) -> str:
    """
    Конвертирует извлечённый диалог в текстовый формат,
    совместимый с существующим пайплайном анализа.
    """
    lines = []
    messenger = dialogue_data.get("messenger", "Неизвестный мессенджер")
    manager = dialogue_data.get("manager_name") or "Менеджер"
    client_name = dialogue_data.get("client_name") or "Клиент"

    lines.append(f"=== ПЕРЕПИСКА ({messenger}) ===")
    lines.append(f"Менеджер: {manager}")
    lines.append(f"Клиент: {client_name}")
    lines.append("")

    for msg in dialogue_data.get("dialogue", []):
        role = msg.get("role", "unknown")
        text = msg.get("text", "")
        time_str = msg.get("time")

        if role == "manager":
            speaker = f"Менеджер ({manager})"
        elif role == "client":
            speaker = f"Клиент ({client_name})"
        else:
            speaker = "Неизвестный"

        time_prefix = f"[{time_str}] " if time_str else ""
        lines.append(f"{time_prefix}{speaker}: {text}")

    notes = dialogue_data.get("notes")
    if notes:
        lines.append("")
        lines.append(f"[Примечания: {notes}]")

    return "\n".join(lines)


# ========== Этап 3: Полный пайплайн для основного чата ==========

async def run_pipeline_from_images(
    user_id: int,
    conversation_id: int,
    image_attachment_ids: List[int],
    progress_conversation_id: Optional[int] = None,
):
    """
    Полный пайплайн: скриншоты → распознавание GPT-4o Vision → текстовый транскрипт → анализ по чек-листам.
    
    Args:
        user_id: ID пользователя
        conversation_id: ID диалога для сохранения результатов
        image_attachment_ids: Список ID вложений-изображений
        progress_conversation_id: Опциональный ID диалога для отображения прогресса
    """
    db: Session = SessionLocal()
    display_conv_id = progress_conversation_id if progress_conversation_id else conversation_id
    
    try:
        # 1. Собираем пути к изображениям
        image_paths: List[Path] = []
        for att_id in image_attachment_ids:
            att = db.get(Attachment, att_id)
            if att:
                abs_path = Path(UPLOAD_DIR) / att.storage_key
                if abs_path.exists():
                    image_paths.append(abs_path)
                    logger.info(f"🖼️ Найдено изображение: {abs_path}")
                else:
                    logger.warning(f"⚠️ Файл не найден: {abs_path}")

        if not image_paths:
            _send_bot_message(db, display_conv_id,
                "❌ Не удалось найти загруженные изображения.")
            return

        # 2. Статус: распознаём
        _send_bot_message(db, display_conv_id,
            f"🔍 Распознаю переписку из {len(image_paths)} скриншот(ов)… Это может занять до 30 секунд.")

        # 3. Извлекаем диалог через GPT-4o Vision
        try:
            dialogue_data = await extract_dialogue_from_images(image_paths)
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON от GPT-4o Vision: {e}", exc_info=True)
            _send_bot_message(db, display_conv_id,
                "❌ Не удалось распознать переписку. Попробуйте загрузить более чёткие скриншоты.")
            return
        except Exception as e:
            logger.error(f"Ошибка распознавания скриншотов: {e}", exc_info=True)
            _send_bot_message(db, display_conv_id,
                f"❌ Ошибка при распознавании скриншотов: {e}")
            return

        # 4. Конвертируем в текстовый транскрипт
        transcript = dialogue_to_transcript(dialogue_data)
        msg_count = len(dialogue_data.get("dialogue", []))
        messenger = dialogue_data.get("messenger", "мессенджер")
        manager_name = dialogue_data.get("manager_name") or "Менеджер"
        client_name = dialogue_data.get("client_name") or "Клиент"

        if msg_count == 0:
            _send_bot_message(db, display_conv_id,
                "⚠️ Не удалось извлечь сообщения из скриншота. "
                "Убедитесь, что на изображении видна переписка и попробуйте снова.")
            return

        # 5. Показываем пользователю что распознали
        _send_bot_message(db, display_conv_id,
            f"✅ Переписка распознана!\n\n"
            f"📱 Мессенджер: {messenger}\n"
            f"👤 Менеджер: {manager_name}\n"
            f"👥 Клиент: {client_name}\n"
            f"💬 Сообщений: {msg_count}\n\n"
            f"📝 Запускаю анализ по чек-листам…")

        # 6. Сохраняем транскрипт
        temp_dir = Path(UPLOAD_DIR) / str(user_id) / str(conversation_id)
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        transcript_path = temp_dir / f"transcript_screenshot_{timestamp}.txt"
        transcript_path.write_text(transcript, encoding="utf-8")
        logger.info(f"📄 Транскрипт сохранён: {transcript_path}")

        # 7. Запускаем существующий анализ по чек-листам
        from services.pipeline import run_pipeline_from_raw_text
        await run_pipeline_from_raw_text(
            user_id, conversation_id, transcript,
            progress_conversation_id=progress_conversation_id
        )

    except Exception as e:
        logger.error(f"❌ Ошибка в image pipeline: {e}", exc_info=True)
        try:
            _send_bot_message(db, display_conv_id,
                f"❌ Произошла ошибка при обработке скриншотов: {e}")
        except Exception:
            pass
    finally:
        db.close()


# ========== Этап 3 (тренер): Полный пайплайн для чата тренера ==========

async def run_pipeline_from_images_trener(
    user_id: int,
    conversation_id: int,
    image_attachment_ids: List[int],
):
    """
    Полный пайплайн тренера: скриншоты → распознавание → анализ тренера.
    
    Args:
        user_id: ID пользователя
        conversation_id: ID диалога для сохранения результатов
        image_attachment_ids: Список ID вложений-изображений
    """
    db: Session = SessionLocal()
    
    try:
        # 1. Собираем пути к изображениям
        image_paths: List[Path] = []
        for att_id in image_attachment_ids:
            att = db.get(Attachment, att_id)
            if att:
                abs_path = Path(UPLOAD_DIR) / att.storage_key
                if abs_path.exists():
                    image_paths.append(abs_path)
                    logger.info(f"🖼️ ТРЕНЕР: Найдено изображение: {abs_path}")

        if not image_paths:
            _send_bot_message(db, conversation_id,
                "❌ Не удалось найти загруженные изображения.")
            return

        # 2. Статус: распознаём
        _send_bot_message(db, conversation_id,
            f"🔍 Распознаю переписку из {len(image_paths)} скриншот(ов)… Это может занять до 30 секунд.")

        # 3. Извлекаем диалог через GPT-4o Vision
        try:
            dialogue_data = await extract_dialogue_from_images(image_paths)
        except json.JSONDecodeError as e:
            logger.error(f"ТРЕНЕР: Ошибка парсинга JSON от GPT-4o Vision: {e}", exc_info=True)
            _send_bot_message(db, conversation_id,
                "❌ Не удалось распознать переписку. Попробуйте загрузить более чёткие скриншоты.")
            return
        except Exception as e:
            logger.error(f"ТРЕНЕР: Ошибка распознавания скриншотов: {e}", exc_info=True)
            _send_bot_message(db, conversation_id,
                f"❌ Ошибка при распознавании скриншотов: {e}")
            return

        # 4. Конвертируем в текстовый транскрипт
        transcript = dialogue_to_transcript(dialogue_data)
        msg_count = len(dialogue_data.get("dialogue", []))
        messenger = dialogue_data.get("messenger", "мессенджер")
        manager_name = dialogue_data.get("manager_name") or "Менеджер"
        client_name = dialogue_data.get("client_name") or "Клиент"

        if msg_count == 0:
            _send_bot_message(db, conversation_id,
                "⚠️ Не удалось извлечь сообщения из скриншота. "
                "Убедитесь, что на изображении видна переписка и попробуйте снова.")
            return

        # 5. Показываем пользователю что распознали
        _send_bot_message(db, conversation_id,
            f"✅ Переписка распознана!\n\n"
            f"📱 Мессенджер: {messenger}\n"
            f"👤 Менеджер: {manager_name}\n"
            f"👥 Клиент: {client_name}\n"
            f"💬 Сообщений: {msg_count}\n\n"
            f"🏋️ Запускаю анализ тренера…")

        # 6. Сохраняем транскрипт
        temp_dir = Path(UPLOAD_DIR) / str(user_id) / str(conversation_id)
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
        transcript_path = temp_dir / f"transcript_screenshot_{timestamp}.txt"
        transcript_path.write_text(transcript, encoding="utf-8")
        logger.info(f"📄 ТРЕНЕР: Транскрипт сохранён: {transcript_path}")

        # 7. Запускаем анализ тренера
        from services.pipeline_trener import run_pipeline_from_raw_text_trener
        await run_pipeline_from_raw_text_trener(user_id, conversation_id, transcript)

    except Exception as e:
        logger.error(f"❌ ТРЕНЕР: Ошибка в image pipeline: {e}", exc_info=True)
        try:
            _send_bot_message(db, conversation_id,
                f"❌ Произошла ошибка при обработке скриншотов: {e}")
        except Exception:
            pass
    finally:
        db.close()

